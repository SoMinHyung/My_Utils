// Codex functions.exec 안에서 실행하는 수집기. 일반 Node.js용 스크립트가 아니다.
// 대화 조회는 Codex의 도구를 사용하고, 파일 저장만 Python에 위임한다.
// new Function('tools', 'options', source)로 실행하면 Promise<report>를 반환한다.
return (async () => {
  const opts = options || {};
  const root = opts.root || "/Users/so/PycharmProjects/My_Utils";
  const outputDirectory = opts.output || "/Users/so/Desktop/gpt";
  const limit = 5;
  const charLimit = 20000;
  const collectedAt = new Date().toISOString();
  const duration = (name, fallback) => {
    const value = opts[name] ?? fallback;
    if (!Number.isSafeInteger(value) || value < 1 || value > 2147483647)
      throw new Error(name + "은 양의 정수(ms)여야 합니다.");
    return value;
  };
  const callTimeoutMs = duration("callTimeoutMs", 30000);
  const threadTimeoutMs = duration("threadTimeoutMs", 120000);
  const collectionTimeoutMs = duration("collectionTimeoutMs", 300000);
  const commandTimeoutMs = duration("commandTimeoutMs", 120000);
  const requestIntervalMs = duration("requestIntervalMs", 3000);
  const isRateLimited = error => /too many requests|rate.?limit|\b429\b/i.test(String(error));
  const progress = opts.onProgress || (typeof notify === "function" ? notify : () => {});
  const timeoutError = (stage, details = {}) => Object.assign(
    new Error("시간 초과: " + stage), {code: "timeout", stage, ...details});
  const bounded = async (stage, action, timeoutMs, details = {}) => {
    if (timeoutMs <= 0) throw timeoutError(stage, details);
    let timer;
    try {
      return await Promise.race([
        Promise.resolve().then(action),
        new Promise((_, reject) => {
          timer = setTimeout(() => reject(timeoutError(stage, details)), timeoutMs);
        })
      ]);
    } finally { clearTimeout(timer); }
  };
  const unpack = (r) => {
    if (r.isError) throw new Error((r.content || []).filter(b => b.type === "text").map(b => b.text).join(" ").slice(0, 400));
    if (r.structuredContent) return r.structuredContent;
    for (const b of r.content || []) {
      if (b.type !== "text") continue;
      try { return JSON.parse(b.text); } catch (_) { /* 다음 블록 확인 */ }
    }
    throw new Error("Codex JSON 응답을 읽을 수 없습니다.");
  };
  const shellQuote = s => "'" + String(s).replace(/'/g, "'\\''") + "'";
  const base = "python3 -m chatgpt_archive --output " + shellQuote(outputDirectory);
  const execute = async (suffix, permissions = {}) => {
    const stage = suffix === "state" ? "state" : "ingest";
    const deadline = Date.now() + commandTimeoutMs;
    let r = await bounded(stage, () => tools.exec_command({cmd: base + " " + suffix,
      workdir: root, max_output_tokens: 12000, yield_time_ms: 1000, ...permissions}), commandTimeoutMs);
    let output = r.output;
    while (r.exit_code == null && r.session_id) {
      const sessionId = r.session_id;
      r = await bounded(stage, () => tools.write_stdin({session_id: sessionId,
        chars: "", max_output_tokens: 12000, yield_time_ms: 1000}), deadline - Date.now(), {session_id: sessionId});
      output += r.output;
    }
    return {exitCode: r.exit_code, output};
  };
  const run = async (suffix) => {
    // 현재 부여된 권한을 사용한다. 명시적 보안 거부(도구 예외)는 잡거나 재시도하지 않는다.
    let result = await execute(suffix);
    let failure;
    if (result.exitCode !== 0) {
      try { failure = JSON.parse(result.output); } catch (_) { /* 일반 실행 실패 */ }
      if (failure?.error_code === "permission_denied") {
        const action = suffix === "state" ? "기존 백업의 대화 ID와 갱신 상태를 읽기 전용으로 조회" :
          "앱에서 조회한 본인 ChatGPT 대화 전문과 메타데이터를 평문으로 보관하고 앱 미리보기 첨부 파일을 복사";
        // heredoc로 대화 원문을 전달하므로 영구 명령 prefix 승인을 함께 요청하지 않는다.
        result = await execute(suffix, {
          sandbox_permissions: "require_escalated",
          justification: `사용자가 요청한 반복 백업을 위해 ${outputDirectory}에서 ${action}하도록 허용할까요? 저장 범위는 이 보관함이며 이전 버전은 유지합니다.`
        });
      }
    }
    if (result.exitCode !== 0) throw new Error(result.output.slice(0, 1000));
    try {
      return JSON.parse(result.output);
    } catch (cause) {
      // 저장 명령을 다시 실행하지 않는다. stdout은 전송 중 잘릴 수 있다.
      throw Object.assign(new Error(
        "명령 결과 JSON을 읽지 못했습니다. 저장을 재실행하지 말고 status --compact로 확인하세요."),
        {code: "command_output_invalid", stage: suffix === "state" ? "state" : "ingest",
          outcome_unknown: suffix !== "state", cause: String(cause)});
    }
  };
  const finish = async bundle => {
    // quoted heredoc: 대화에 $, 백틱, 따옴표가 있어도 셸 코드로 실행되지 않는다.
    const body = JSON.stringify({schema_version: 1, collected_at: collectedAt, listing_limit: limit, ...bundle});
    let marker = "CHATGPT_ARCHIVE_INPUT";
    while (body.split("\n").includes(marker)) marker += "_";
    progress({stage: "ingest", status: "started", collected: bundle.conversations.length, errors: bundle.errors.length});
    const report = await run("ingest --compact <<'" + marker + "'\n" + body + "\n" + marker);
    progress({stage: "done", status: report.run_status || "completed", changed: report.changed,
      errors: report.report_counts?.errors ?? report.errors?.length,
      deferred: report.report_counts?.deferred ?? report.deferred?.length,
      excluded: report.report_counts?.excluded ?? report.excluded?.length});
    return report;
  };
  progress({stage: "startup", status: "started"});
  // 조회 전 상태 읽기가 실패하면 추가 대화 조회를 시작하지 않는다.
  const state = await run("state");
  const retries = new Map((state.retry_state || []).map(t => [t.id, t]));
  const knownIds = new Set([...(state.conversations || []).map(t => t.id), ...retries.keys(), ...(opts.threadIds || [])]);
  const deferKnown = reason => [...knownIds].filter(id => !retries.get(id)?.excluded).map(id => ({id, reason}));
  // 첫 제한 뒤에는 다음 실행에서 재시도한다. 연속 제한 때만 실행 횟수로 쉰다.
  if (state.rate_limit_skip_runs_remaining > 0) {
    return finish({discovered: 0, skipped: 0, conversations: [], errors: [], warnings: [],
      deferred: deferKnown("rate_limit_backoff"), run_status: "deferred", rate_limit_backoff_skipped: true});
  }
  let listing;
  try {
    listing = unpack(await bounded("list_threads", () => tools.mcp__codex_app__list_threads({limit}), callTimeoutMs));
  } catch (error) {
    if (!isRateLimited(error)) throw error;
    return finish({discovered: 0, skipped: 0, conversations: [], warnings: [], run_status: "partial_failure",
      errors: [{id: null, code: "rate_limited", stage: "list_threads", message: String(error).slice(0, 400)}],
      deferred: deferKnown("rate_limited")});
  }
  let lastRequestFinishedAt = Date.now();
  const warnings = [];
  if ((listing.threads || []).length >= limit)
    warnings.push("최근 목록 5개만 요청합니다. Codex 작업도 포함되므로 아직 모르는 ChatGPT 대화를 놓칠 수 있습니다. 이미 저장한 대화는 ID로 계속 추적합니다.");
  if ((listing.unavailableSources || []).length || (listing.unavailableHosts || []).length)
    warnings.push("앱에서 일부 대화 소스/호스트를 조회하지 못했습니다.");
  const threads = new Map();
  for (const t of [...(listing.pinnedThreads || []), ...(listing.threads || [])]) {
    if (t.kind === "chatgpt") threads.set(t.id, t);
  }
  const discovered = threads.size;
  for (const t of state.conversations || []) {
    if (!threads.has(t.id)) threads.set(t.id, {id: t.id, kind: "chatgpt", title: t.title});
  }
  for (const id of retries.keys()) {
    if (!threads.has(id)) threads.set(id, {id, kind: "chatgpt", title: id});
  }
  for (const id of opts.threadIds || []) {
    if (!threads.has(id)) threads.set(id, {id, kind: "chatgpt", title: id});
  }
  const previous = new Map((state.conversations || []).map(t => [t.id, t]));
  const forced = new Set(opts.threadIds || []);
  const deferred = [];
  let skipped = 0;
  const pending = [...threads.values()].filter(t => {
    const retry = retries.get(t.id);
    if (!forced.has(t.id)) {
      if (retry?.excluded) return false;
      if (Date.parse(retry?.next_retry_at) > Date.now()) {
        deferred.push({id: t.id, reason: "retry_backoff", next_retry_at: retry.next_retry_at});
        return false;
      }
    }
    const old = previous.get(t.id);
    // 직전 응답이 미검증이어도 같은 수정 버전의 불완전한 도구 응답을 계속 반복하지 않는다.
    // verification-queue는 진단 목록일 뿐이며 브라우저 보완 단계는 없다.
    // awaiting_ui_verification은 기존 저장 상태와의 호환을 위해 읽는다.
    const unverifiedSameVersion = old?.awaiting_ui_verification &&
      t.updatedAt != null && old.source_checked_updated_at != null &&
      Number(t.updatedAt) === Number(old.source_checked_updated_at) &&
      t.title === (old.source_checked_title ?? old.title) &&
      Date.now() - Date.parse(old.source_checked_at) < 24 * 60 * 60 * 1000;
    if (!forced.has(t.id) && !opts.full && unverifiedSameVersion) {
      skipped++;
      return false;
    }
    // 목록 밖으로 밀린 대화와 미완료 대화는 갱신 시각을 모르므로 다시 조회한다.
    const needed = forced.has(t.id) || opts.full || !old || !old.text_complete || t.updatedAt == null ||
      Number(t.updatedAt) !== Number(old.listed_updated_at) || t.title !== old.title;
    if (!needed) skipped++;
    return needed;
  });
  // 새/정상 대화를 먼저 읽고, 실패 대화끼리는 오래 기다린 순서로 처리한다.
  pending.sort((a, b) => Number(Boolean(retries.get(a.id))) - Number(Boolean(retries.get(b.id))) ||
    (Date.parse(retries.get(a.id)?.last_failure_at) || 0) - (Date.parse(retries.get(b.id)?.last_failure_at) || 0));
  const collectionDeadline = Date.now() + collectionTimeoutMs;
  const readFull = async (entry) => {
    const pages = [];
    const seen = new Set();
    const threadDeadline = Date.now() + threadTimeoutMs;
    progress({stage: "read_thread", id: entry.id, status: "started"});
    let cursor;
    while (true) {
      const args = {threadId: entry.id, turnLimit: 10, includeOutputs: true, maxOutputCharsPerItem: charLimit};
      if (cursor) args.cursor = cursor;
      const waitMs = Math.max(0, requestIntervalMs - (Date.now() - lastRequestFinishedAt));
      if (waitMs >= Math.min(threadDeadline, collectionDeadline) - Date.now())
        throw timeoutError("read_thread", {page: pages.length + 1, attempted: pages.length > 0});
      if (waitMs) await new Promise(resolve => setTimeout(resolve, waitMs));
      const remaining = Math.min(callTimeoutMs, threadDeadline - Date.now(), collectionDeadline - Date.now());
      let page;
      try {
        page = unpack(await bounded("read_thread", () => tools.mcp__codex_app__read_thread(args),
          remaining, {id: entry.id, page: pages.length + 1}));
      } finally { lastRequestFinishedAt = Date.now(); }
      if (page.thread?.kind !== "chatgpt" || page.thread.id !== entry.id)
        throw new Error("요청과 다른 종류/ID의 대화가 반환됐습니다.");
      if (!page.page || typeof page.page.hasMore !== "boolean")
        throw new Error("대화 페이지 완료 여부를 확인할 수 없습니다.");
      pages.push(page);
      if (!page.page.hasMore) break;
      const next = page.page.nextCursor;
      if (!next || seen.has(next)) throw new Error("이전 대화 페이지를 끝까지 읽지 못했습니다.");
      seen.add(next);
      cursor = next;
      if (pages.length >= 500) throw new Error("페이지 수가 500을 초과했습니다. 수동 확인이 필요합니다.");
    }
    // 수집 도중 새 응답/수정이 생겼다면 다음 실행에서 다시 가져오게 한다.
    const changing = new Set(pages.map(p => p.thread.updatedAt)).size > 1;
    return {id: entry.id, source: "codex_app_read_thread", history_boundary_verified: false,
      listed_updated_at: entry.updatedAt ?? null,
      collected_at: collectedAt, char_limit: charLimit, changing, pages};
  };
  const conversations = [];
  const errors = [];
  // 한 번에 하나만 요청한다. 호출 제한이면 남은 대화는 실패 처리 없이 미룬다.
  for (let start = 0; start < pending.length; start++) {
    if (Date.now() >= collectionDeadline) {
      deferred.push(...pending.slice(start).map(entry => ({id: entry.id, reason: "collection_timeout"})));
      break;
    }
    const entry = pending[start];
    try {
      conversations.push(await readFull(entry));
      progress({stage: "read_thread", id: entry.id, status: "fulfilled"});
    } catch (reason) {
      if (reason?.attempted === false) {
        deferred.push({id: entry.id, reason: "collection_timeout"});
        continue;
      }
      const error = {id: entry.id, stage: "read_thread", message: String(reason).slice(0, 400)};
      if (reason?.code === "timeout") Object.assign(error, {code: "timeout", page: reason.page});
      if (isRateLimited(reason)) error.code = "rate_limited";
      errors.push(error);
      progress({stage: "read_thread", id: entry.id, status: "rejected", error});
      if (error.code === "rate_limited") {
        deferred.push(...pending.slice(start + 1).map(t => ({id: t.id, reason: "rate_limited"})));
        break;
      }
    }
  }
  return finish({discovered, warnings, errors, conversations, skipped, deferred, remote_checked: true,
    run_status: errors.length ? "partial_failure" : deferred.length ? "deferred" : "completed"});
})();
