import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const collect = new Function('tools', 'options', await readFile(new URL('../collector.js', import.meta.url), 'utf8'));
const listing = { structuredContent: { threads: [] } };
const state = { exit_code: 0, output: '{"conversations":[]}' };
const report = { exit_code: 0, output: '{"new_errors":[]}' };

test('granted access needs no forced escalation for state or ingest', async () => {
  const calls = [];
  await collect({ mcp__codex_app__list_threads: async () => listing,
    exec_command: async args => { calls.push(args); return calls.length === 1 ? state : report; }
  }, {});
  assert.equal(calls.length, 2);
  assert.ok(calls.every(x => !x.sandbox_permissions && !x.prefix_rule));
});

test('only an actual permission error escalates the identical write once', async () => {
  const calls = [];
  await collect({ mcp__codex_app__list_threads: async () => listing,
    exec_command: async args => {
      calls.push(args);
      return calls.length === 1 ? state : calls.length === 2 ?
        { exit_code: 1, output: '{"error_code":"permission_denied","error":"test denial"}' } : report;
    }
  }, {});
  assert.equal(calls.length, 3);
  assert.equal(calls[1].cmd, calls[2].cmd);
  assert.equal(calls[2].sandbox_permissions, 'require_escalated');
  assert.equal(calls[2].prefix_rule, undefined);
});

test('explicit auto-review denial stops without retry', async () => {
  let calls = 0;
  await assert.rejects(collect({ mcp__codex_app__list_threads: async () => listing,
    exec_command: async () => { calls++; throw new Error('Rejected by auto-review'); }
  }, {}), /Rejected/);
  assert.equal(calls, 1);
});

test('ordinary failures never trigger escalation', async () => {
  let calls = 0;
  await assert.rejects(collect({ mcp__codex_app__list_threads: async () => listing,
    exec_command: async () => { calls++; return {exit_code: 1, output: '{"error_code":"archive_error","error":"invalid DB"}'}; }
  }, {}), /invalid DB/);
  assert.equal(calls, 1);
});

test('storage uses a compact report and never retries a truncated successful output', async () => {
  const calls = [];
  await assert.rejects(collect({
    mcp__codex_app__list_threads: async () => listing,
    exec_command: async args => {
      calls.push(args);
      return calls.length === 1 ? state : {exit_code: 0, output: 'Warning: truncated output'};
    }
  }, {}), {code: 'command_output_invalid', stage: 'ingest', outcome_unknown: true});
  assert.match(calls[1].cmd, /ingest --compact/);
  assert.equal(calls.length, 2);
});

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const never = () => new Promise(() => {});
const page = (id, extra = {}) => ({structuredContent: {
  thread: {id, kind: 'chatgpt', updatedAt: 1},
  turns: [], page: {hasMore: false, ...extra}
}});
const fastOptions = {callTimeoutMs: 30, threadTimeoutMs: 100,
  collectionTimeoutMs: 1000, commandTimeoutMs: 1000, requestIntervalMs: 1};

function fixture(ids, read) {
  const commands = [];
  let bundle;
  return {
    commands, get bundle() { return bundle; },
    tools: {
      mcp__codex_app__list_threads: async () => ({structuredContent: {
        threads: ids.map(id => ({id, kind: 'chatgpt', updatedAt: 1}))
      }}),
      mcp__codex_app__read_thread: read,
      exec_command: async args => {
        commands.push(args);
        if (args.cmd.endsWith(' state')) return state;
        bundle = JSON.parse(args.cmd.split('\n')[1]);
        return {exit_code: 0, output: JSON.stringify({errors: bundle.errors})};
      }
    }
  };
}

test('web copy helper is removed', async () => {
  await assert.rejects(readFile(new URL('../ui_snapshot.js', import.meta.url)), {code: 'ENOENT'});
});

test('recent listing requests five but known chats outside it remain tracked', async () => {
  const reads = [];
  const f = fixture(['new'], ({threadId}) => { reads.push(threadId); return page(threadId); });
  withState(f, {conversations: [{id: 'known-outside-list', title: 'previous', text_complete: true}]});
  const list = f.tools.mcp__codex_app__list_threads;
  f.tools.mcp__codex_app__list_threads = args => {
    assert.deepEqual(args, {limit: 5});
    return list(args);
  };
  await collect(f.tools, fastOptions);
  assert.deepEqual(reads, ['new', 'known-outside-list']);
  assert.equal(f.bundle.listing_limit, 5);
});

test('unverified collection ends after ingest without browser or repair fallback', async () => {
  const f = fixture(['chat'], ({threadId}) => page(threadId));
  const execute = f.tools.exec_command;
  const unverified = {run_status: 'verification_required',
    new_verification_required: [{id: 'chat', eligible: true}]};
  f.tools.exec_command = async args => {
    assert.match(args.cmd, / (state$|ingest --compact <<)/);
    const result = await execute(args);
    return args.cmd.endsWith(' state') ? result :
      {exit_code: 0, output: JSON.stringify(unverified)};
  };
  const allowed = new Set(['mcp__codex_app__list_threads', 'mcp__codex_app__read_thread', 'exec_command']);
  const tools = new Proxy(f.tools, {
    get(target, name) {
      assert.ok(allowed.has(name), `unexpected collection tool: ${String(name)}`);
      return target[name];
    }
  });
  assert.deepEqual(await collect(tools, fastOptions), unverified);
  assert.equal(f.commands.length, 2);
  assert.deepEqual(f.bundle.conversations.map(c => c.id), ['chat']);
});

test('one hung conversation times out while other batches reach storage', async () => {
  const f = fixture(['stuck', 'ok1', 'ok2', 'ok3'], ({threadId}) =>
    threadId === 'stuck' ? never() : page(threadId));
  const events = [];
  await collect(f.tools, {...fastOptions, onProgress: event => events.push(event)});
  assert.deepEqual(f.bundle.conversations.map(c => c.id), ['ok1', 'ok2', 'ok3']);
  assert.deepEqual(f.bundle.errors, [{id: 'stuck', message: 'Error: 시간 초과: read_thread',
    code: 'timeout', stage: 'read_thread', page: 1}]);
  assert.equal(events.at(-1).stage, 'done');
});

test('late response after timeout does not continue pagination or save incomplete text', async () => {
  let resolveLate;
  let reads = 0;
  const f = fixture(['late'], () => { reads++; return new Promise(resolve => { resolveLate = resolve; }); });
  await collect(f.tools, fastOptions);
  resolveLate(page('late', {hasMore: true, nextCursor: 'older'}));
  await delay(10);
  assert.equal(reads, 1);
  assert.equal(f.bundle.conversations.length, 0);
  assert.equal(f.bundle.errors[0].id, 'late');
});

test('total conversation deadline bounds slow but responsive pagination', async () => {
  let reads = 0;
  const f = fixture(['long'], async () => {
    await delay(15);
    return page('long', {hasMore: true, nextCursor: String(++reads)});
  });
  await collect(f.tools, {...fastOptions, callTimeoutMs: 100, threadTimeoutMs: 40});
  assert.equal(f.bundle.conversations.length, 0);
  assert.equal(f.bundle.errors[0].code, 'timeout');
  assert.ok(reads < 10);
});

test('collection deadline defers unattempted conversations without counting failures', async () => {
  let reads = 0;
  const ids = Array.from({length: 20}, (_, i) => 'chat-' + i);
  const f = fixture(ids, () => { reads++; return never(); });
  await collect(f.tools, {...fastOptions, collectionTimeoutMs: 40});
  assert.ok(reads < ids.length);
  assert.equal(f.bundle.errors.length + f.bundle.deferred.length, ids.length);
  assert.ok(f.bundle.deferred.some(e => e.reason === 'collection_timeout'));
});

test('hung listing fails with its stage before any storage write', async () => {
  const f = fixture([], never);
  f.tools.mcp__codex_app__list_threads = never;
  await assert.rejects(collect(f.tools, fastOptions), {code: 'timeout', stage: 'list_threads'});
  assert.equal(f.commands.length, 1);
});

test('hung state command fails before listing and is not retried', async () => {
  let calls = 0;
  await assert.rejects(collect({
    exec_command: () => { calls++; return never(); },
    mcp__codex_app__list_threads: () => { assert.fail('state failure must stop discovery'); }
  }, {...fastOptions, commandTimeoutMs: 30}), {code: 'timeout', stage: 'state'});
  assert.equal(calls, 1);
});

test('hung storage reports unknown outcome without retrying the write', async () => {
  let calls = 0;
  await assert.rejects(collect({
    exec_command: () => ++calls === 1 ? state : never(),
    mcp__codex_app__list_threads: async () => listing
  }, {...fastOptions, commandTimeoutMs: 30}), {code: 'timeout', stage: 'ingest'});
  assert.equal(calls, 2);
});

test('command polling has a shared deadline and reports the session', async () => {
  let polls = 0;
  await assert.rejects(collect({
    exec_command: async () => ({session_id: 123, output: ''}),
    write_stdin: async () => { polls++; await delay(5); return {session_id: 123, output: ''}; }
  }, {...fastOptions, commandTimeoutMs: 30}), {code: 'timeout', stage: 'state', session_id: 123});
  assert.ok(polls > 1);
});

test('completed commands with session IDs must not be polled again', async () => {
  let calls = 0;
  await collect({
    exec_command: async () => ({...(++calls === 1 ? state : report), session_id: 123}),
    write_stdin: () => assert.fail('completed session polled'),
    mcp__codex_app__list_threads: async () => listing
  }, fastOptions);
  assert.equal(calls, 2);
});

function withState(f, savedState) {
  const exec = f.tools.exec_command;
  f.tools.exec_command = args => args.cmd.endsWith(' state') ?
    {exit_code: 0, output: JSON.stringify(savedState)} : exec(args);
}

test('rate limit stops further requests and keeps completed conversations for storage', async () => {
  const reads = [];
  const f = fixture(['ok', 'limited', 'unattempted'], ({threadId}) => {
    reads.push(threadId);
    return threadId === 'limited' ? {isError: true, content: [{type: 'text', text: '{"detail":"Too many requests"}'}]} : page(threadId);
  });
  await collect(f.tools, fastOptions);
  assert.deepEqual(reads, ['ok', 'limited']);
  assert.deepEqual(f.bundle.conversations.map(c => c.id), ['ok']);
  assert.equal(f.bundle.errors[0].code, 'rate_limited');
  assert.deepEqual(f.bundle.deferred, [{id: 'unattempted', reason: 'rate_limited'}]);
  assert.equal(f.bundle.run_status, 'partial_failure');
});

test('listing rate limit is persisted so consecutive limits can be counted', async () => {
  const f = fixture([], () => assert.fail('must not read'));
  f.tools.mcp__codex_app__list_threads = () => { throw new Error('HTTP 429'); };
  await collect(f.tools, fastOptions);
  assert.equal(f.bundle.errors[0].stage, 'list_threads');
  assert.equal(f.bundle.errors[0].code, 'rate_limited');
});

test('scheduled backoff prevents all remote requests even for explicit full collection', async () => {
  const f = fixture(['chat'], () => assert.fail('must not read'));
  withState(f, {conversations: [{id: 'chat'}], rate_limit_streak: 2, rate_limit_skip_runs_remaining: 1});
  f.tools.mcp__codex_app__list_threads = () => assert.fail('must not list');
  await collect(f.tools, {...fastOptions, full: true, threadIds: ['chat']});
  assert.equal(f.bundle.run_status, 'deferred');
  assert.deepEqual(f.bundle.errors, []);
  assert.equal(f.bundle.skipped, 0);
  assert.equal(f.bundle.deferred[0].reason, 'rate_limit_backoff');
  assert.equal(f.bundle.rate_limit_backoff_skipped, true);
  assert.ok(!f.bundle.remote_checked);
});

test('first rate limit retries next run and an old fixed cooldown is ignored', async () => {
  const reads = [];
  const f = fixture(['chat'], ({threadId}) => { reads.push(threadId); return page(threadId); });
  withState(f, {conversations: [], rate_limit_streak: 1, rate_limit_skip_runs_remaining: 0,
    next_collection_at: new Date(Date.now() + 1800000).toISOString()});
  await collect(f.tools, fastOptions);
  assert.deepEqual(reads, ['chat']);
  assert.equal(f.bundle.remote_checked, true);
  assert.ok(!f.bundle.rate_limit_backoff_skipped);
});

test('a successful unchanged listing marks a real check so backoff can reset', async () => {
  const f = fixture(['chat'], () => assert.fail('unchanged chat must not be read'));
  withState(f, {conversations: [{id: 'chat', text_complete: true, listed_updated_at: 1}],
    rate_limit_streak: 3, rate_limit_skip_runs_remaining: 0});
  await collect(f.tools, fastOptions);
  assert.equal(f.bundle.skipped, 1);
  assert.equal(f.bundle.conversations.length, 0);
  assert.equal(f.bundle.remote_checked, true);
});

test('false end of history is explicitly unverified in captured source', async () => {
  const f = fixture(['chat'], () => page('chat'));
  await collect(f.tools, fastOptions);
  assert.equal(f.bundle.conversations[0].history_boundary_verified, false);
  assert.equal(f.bundle.conversations[0].source, 'codex_app_read_thread');
});

test('same source version waiting for UI is not repeatedly fetched from broken reader', async () => {
  const f = fixture(['chat'], () => assert.fail('UI queue must handle this version'));
  withState(f, {conversations: [{id: 'chat', text_complete: false,
    awaiting_ui_verification: true, source_checked_updated_at: 1,
    source_checked_at: new Date().toISOString()}]});
  await collect(f.tools, fastOptions);
  assert.equal(f.bundle.skipped, 1);
  assert.equal(f.bundle.conversations.length, 0);
});

test('exclusions survive full runs, backoff skips failures, and explicit ID can retry', async () => {
  const reads = [];
  const f = fixture(['excluded', 'backoff', 'normal'], ({threadId}) => { reads.push(threadId); return page(threadId); });
  withState(f, {conversations: [], retry_state: [
    {id: 'excluded', excluded: true, consecutive_failures: 100},
    {id: 'backoff', next_retry_at: new Date(Date.now() + 60000).toISOString()}
  ]});
  await collect(f.tools, {...fastOptions, full: true});
  assert.deepEqual(reads, ['normal']);
  assert.equal(f.bundle.skipped, 0);
  assert.equal(f.bundle.deferred[0].id, 'backoff');
  reads.length = 0;
  await collect(f.tools, {...fastOptions, threadIds: ['excluded']});
  assert.ok(reads.includes('excluded'));
  assert.ok(!reads.includes('backoff'));
});

test('never-saved failures are retried and healthy new conversations go first', async () => {
  const reads = [];
  const f = fixture(['old-failure', 'new'], ({threadId}) => { reads.push(threadId); return page(threadId); });
  withState(f, {conversations: [], retry_state: [
    {id: 'old-failure', consecutive_failures: 1, last_failure_at: '2026-01-02T00:00:00Z'},
    {id: 'missing-from-list', consecutive_failures: 2, last_failure_at: '2026-01-01T00:00:00Z'}
  ]});
  await collect(f.tools, fastOptions);
  assert.deepEqual(reads, ['new', 'missing-from-list', 'old-failure']);
});

test('one request at a time with spacing after responses, including pagination', async () => {
  const starts = [];
  const finishes = [];
  let active = 0;
  const f = fixture(['multi', 'next'], async ({threadId, cursor}) => {
    assert.equal(active++, 0);
    starts.push(Date.now());
    await delay(5);
    active--;
    finishes.push(Date.now());
    return page(threadId, threadId === 'multi' && !cursor ? {hasMore: true, nextCursor: 'older'} : {});
  });
  await collect(f.tools, {...fastOptions, requestIntervalMs: 20, threadTimeoutMs: 200});
  assert.equal(starts.length, 3);
  for (let i = 1; i < starts.length; i++) assert.ok(starts[i] - finishes[i - 1] >= 18);
});
