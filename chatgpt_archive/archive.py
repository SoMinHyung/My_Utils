"""UTF-8 Markdown/TXT와 SQLite 원본 스냅샷 저장. Python 표준 라이브러리만 사용."""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from urllib.parse import quote

from .retry_policy import RATE_LIMIT_POLICY_VERSION, RetryHistory, collection_state, error_key
from .content_validation import (
    REFERENCE_WARNING, has_reference, resolve_from_history, unresolved_messages,
)
from .quality import HISTORY_WARNING, issues


DEFAULT_OUTPUT = Path("/Users/so/Desktop/gpt")
KST = timezone(timedelta(hours=9))


class ArchiveError(ValueError):
    pass


class UnresolvedReferenceError(ArchiveError):
    code = "unresolved_content_reference"
    stage = "validation"

    def __init__(self, messages):
        self.message_ids = [m.get("id") for m in messages]
        super().__init__("답변 %d건의 실제 본문 대신 참조 표시가 반환됐습니다. 기존 백업을 유지합니다."
                         % len(messages))


class IncompleteHistoryError(ArchiveError):
    code = "incomplete_history"
    stage = "validation"

    def __init__(self, missing_ids):
        self.message_ids = sorted(missing_ids)
        super().__init__("조회 완료로 표시됐지만 기존 메시지 %d건이 빠졌습니다. 기존 백업을 유지합니다."
                         % len(missing_ids))


class ProtectedContentError(ArchiveError):
    code = "ui_verification_required"
    stage = "validation"


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def now():
    return datetime.now(timezone.utc).isoformat()


def read_archive(output, command):
    """상태 조회는 디렉터리·DB·잠금 파일을 만들거나 기존 파일을 수정하지 않는다."""
    if command not in ("state", "status", "verification-queue"):
        raise ValueError("읽기 전용 명령이 아닙니다.")
    output = Path(output).expanduser().resolve()
    database = output / "archive.sqlite3"
    try:
        database.stat()
    except FileNotFoundError:
        if command == "verification-queue":
            return {"items": [], "eligible": 0}
        return {"conversations": []} if command == "state" else {"total": 0, "output": str(output)}
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=30)) as db:
        db.row_factory = sqlite3.Row
        if command == "state":
            return collection_state(db)
        if command == "verification-queue":
            from .verification import verification_queue
            return verification_queue(db)
        row = db.execute("SELECT report FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        return json.loads(row[0]) if row else {"total": 0, "output": str(output)}


def parse_date(value):
    if value is None:
        return None
    try:
        dt = (datetime.fromtimestamp(value, timezone.utc) if isinstance(value, (int, float))
              else datetime.fromisoformat(value.replace("Z", "+00:00")))
        return dt.astimezone(KST)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def date_text(value):
    dt = parse_date(value)
    return dt.strftime("%Y-%m-%d %H:%M:%S KST") if dt is not None else "알 수 없음"


def safe_name(value, max_bytes=90):
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", str(value)).strip(" .") or "제목 없음"
    return name.encode("utf-8")[:max_bytes].decode("utf-8", "ignore").rstrip(" .") or "대화"


def markdown_label(value):
    return re.sub(r"([\\`*_{}\[\]<>|])", r"\\\1", str(value).replace("\n", " "))


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = data if isinstance(data, bytes) else data.encode("utf-8")
    if path.is_file() and path.read_bytes() == data:
        return
    with tempfile.NamedTemporaryFile(dir=str(path.parent), delete=False) as out:
        temp = Path(out.name)
        try:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        except BaseException:
            temp.unlink()
            raise
    try:
        os.replace(str(temp), str(path))
    finally:
        if temp.exists():
            temp.unlink()


def reject_truncation(value, char_limit):
    if isinstance(value, dict):
        for key, item in value.items():
            if "truncat" in key.lower() and item is True:
                raise ArchiveError("도구가 메시지 잘림을 표시했습니다. 기존 백업을 유지합니다.")
            if key == "text" and isinstance(item, str):
                if len(item.encode("utf-16-le")) // 2 >= char_limit:
                    raise ArchiveError("메시지가 도구의 글자 수 상한에 도달했습니다. 기존 백업을 유지합니다.")
                if re.search(r"\[(?:\.\.\.\s*)?truncated[^\]]*\]", item, re.I):
                    raise ArchiveError("메시지에 잘림 표시가 있습니다. 기존 백업을 유지합니다.")
            reject_truncation(item, char_limit)
    elif isinstance(value, list):
        for item in value:
            reject_truncation(item, char_limit)


def normalize(capture, previous_answers=None, *, keep_unresolved=False):
    pages = capture.get("pages", [])
    if not pages or pages[-1].get("page", {}).get("hasMore") is not False:
        raise ArchiveError("마지막 페이지를 확인하지 못했습니다.")
    thread = pages[0].get("thread", {})
    ident = thread.get("id", "")
    if thread.get("kind") != "chatgpt" or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", ident):
        raise ArchiveError("ChatGPT 대화 ID가 올바르지 않습니다.")
    if capture.get("id") != ident:
        raise ArchiveError("수집 ID와 대화 ID가 다릅니다.")
    char_limit = capture.get("char_limit")
    if not isinstance(char_limit, int) or char_limit < 1:
        raise ArchiveError("조회 글자 수 제한 정보가 없습니다.")
    warnings = []
    text_complete = not capture.get("changing", False)
    if not text_complete:
        warnings.append("수집 도중 대화가 변경됐습니다. 다음 실행에서 다시 확인합니다.")
    turns = []
    seen = {}
    attachments = []
    for pos, page in enumerate(pages):
        if page.get("thread", {}).get("id") != ident or page["thread"].get("kind") != "chatgpt":
            raise ArchiveError("다른 대화의 페이지가 섞였습니다.")
        if pos < len(pages) - 1 and page.get("page", {}).get("hasMore") is not True:
            raise ArchiveError("대화 페이지의 연결이 올바르지 않습니다.")
        if page.get("page", {}).get("order") != "newest_first":
            raise ArchiveError("지원하지 않는 페이지 정렬 방식입니다.")
        reject_truncation(page.get("turns", []), char_limit)
        attachments.extend(page.get("attachments", []))
    for page in reversed(pages):
        for turn in reversed(page.get("turns", [])):
            turn_id = turn.get("id")
            if not turn_id:
                raise ArchiveError("대화 차례의 ID가 없습니다.")
            if turn_id in seen:
                if dumps(seen[turn_id]) != dumps(turn):
                    raise ArchiveError("페이지 사이에서 대화가 변경됐습니다. 다시 조회해야 합니다.")
                continue
            seen[turn_id] = turn
            turns.append(turn)
    if not turns:
        raise ArchiveError("질문·답변이 없는 조회 결과입니다.")
    if any(t.get("status") != "completed" for t in turns):
        text_complete = False
        warnings.append("미완료/오류 응답이 포함돼 있습니다. 다음 실행에서 다시 확인합니다.")
    messages = []
    for turn in turns:
        for item in turn.get("items", []):
            kind = item.get("type")
            if kind == "agentMessage":
                text = item.get("text")
            elif kind == "userMessage":
                blocks = item.get("content", [])
                text = "\n\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")
                if any(b.get("type") != "text" for b in blocks):
                    warnings.append("질문의 비텍스트 블록은 JSON 원본과 첨부 파일을 확인하세요.")
            else:
                warnings.append("질문·답변 외 항목은 JSON 원본에 보관했습니다: " + str(kind))
                continue
            if not isinstance(text, str):
                raise ArchiveError("본문이 문자열이 아닌 메시지가 있습니다.")
            messages.append({"id": item.get("id"), "role": "user" if kind == "userMessage" else "assistant",
                             "text": text, "time": turn.get("startedAt"), "turn_id": turn["id"]})
    if not messages:
        raise ArchiveError("저장할 질문·답변 텍스트가 없습니다.")
    if not any(m["role"] == "user" for m in messages):
        warnings.append("조회 결과에 질문 메시지가 없습니다. 원 대화와 비교가 필요합니다.")
    if not any(m["role"] == "assistant" for m in messages):
        text_complete = False
        warnings.append("답변 메시지가 아직 없습니다.")
    ids = [m["id"] for m in messages]
    if any(not isinstance(mid, str) or not mid for mid in ids) or len(ids) != len(set(ids)):
        raise ArchiveError("메시지 ID가 없거나 중복됩니다.")
    doc = {"id": ident, "title": thread.get("title") or "제목 없음",
           "created_at": thread.get("createdAt"), "updated_at": thread.get("updatedAt"),
           "messages": messages, "attachments": attachments,
           "source_incomplete": not text_complete,
           "text_complete": False, "warnings": sorted(set(warnings + [HISTORY_WARNING]))}
    resolve_from_history(doc, previous_answers or {})
    missing = unresolved_messages(doc)
    if missing and not keep_unresolved:
        raise UnresolvedReferenceError(missing)
    doc["questions_without_observed_answer"] = [
        mid for problem in issues(doc) if problem["code"] == "missing_answer"
        for mid in problem["message_ids"]]
    return doc


class Archive:
    def __init__(self, output=DEFAULT_OUTPUT):
        self.output = Path(output).expanduser().resolve()
        self.db = None

    def __enter__(self):
        self.output.mkdir(parents=True, exist_ok=True, mode=0o700)
        # 사용자 지정 출력 경로에서도 실수로 대화 원문이 커밋되지 않게 한다.
        if not (self.output / ".gitignore").exists():
            atomic_write(self.output / ".gitignore", "*\n!.gitignore\n")
        self.db = sqlite3.connect(str(self.output / "archive.sqlite3"), timeout=30)
        os.chmod(str(self.output / "archive.sqlite3"), 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS snapshots (
                thread_id TEXT NOT NULL, digest TEXT NOT NULL,
                captured_at TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY (thread_id, digest)
            );
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, stem TEXT NOT NULL,
                digest TEXT NOT NULL, listed_updated_at REAL, text_complete INTEGER NOT NULL,
                last_checked_at TEXT NOT NULL, last_error TEXT
            );
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY, finished_at TEXT NOT NULL, report TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS observations (
                thread_id TEXT NOT NULL, digest TEXT NOT NULL, captured_at TEXT NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY(thread_id,digest)
            );
        """)
        return self

    def __exit__(self, *_):
        if self.db:
            self.db.close()

    def state(self):
        return collection_state(self.db)

    def _attachments(self, ident, items):
        saved = []
        seen = set()
        temp_root = Path(tempfile.gettempdir()).resolve()
        for item in items:
            name = str(item.get("name") or "첨부 파일")
            source = Path(item.get("path") or "").expanduser().resolve()
            key = (name, str(source))
            if key in seen:
                continue
            seen.add(key)
            attachment = {"name": name, "available": False}
            # 앱이 반환한 미리보기 임시 파일만 복사한다. 임의의 로컬 파일은 읽지 않는다.
            try:
                relative = source.relative_to(temp_root)
                allowed = relative.parts and relative.parts[0].startswith("codex-file-preview-")
            except ValueError:
                allowed = False
            if allowed and source.is_file():
                blob = source.read_bytes()
                digest = hashlib.sha256(blob).hexdigest()
                relative_target = Path("attachments") / ident / (digest[:20] + "_" + safe_name(name))
                target = self.output / relative_target
                if not target.exists():
                    atomic_write(target, blob)
                elif hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                    raise ArchiveError("저장된 첨부 파일의 해시가 일치하지 않습니다.")
                attachment.update(available=True, path=relative_target.as_posix(), sha256=digest)
            saved.append(attachment)
        return saved

    def _reference_answers(self, ident):
        candidates = {}
        for row in self.db.execute(
                "SELECT digest,captured_at,payload FROM snapshots WHERE thread_id=? ORDER BY captured_at",
                (ident,)):
            payload = json.loads(row["payload"])
            completed_ids = {
                item.get("id")
                for page in payload.get("source_pages", [])
                for turn in page.get("turns", []) if turn.get("status") == "completed"
                for item in turn.get("items", []) if item.get("type") == "agentMessage"
            }
            completed_ids.update(
                message.get("id") for message in payload.get("source_browser", {}).get("messages", [])
                if message.get("source") == "chatgpt_ui_copy")
            for message in payload["document"]["messages"]:
                text = message.get("text")
                message_id = message.get("id")
                if (message.get("role") != "assistant" or message_id not in completed_ids or
                        not isinstance(text, str) or not text.strip() or has_reference(text)):
                    continue
                try:
                    reject_truncation({"text": text}, 20000)
                except ArchiveError:
                    continue
                candidates.setdefault(message_id, {}).setdefault(text, {
                    "text": text, "digest": row["digest"], "captured_at": row["captured_at"]})
        # 동일 ID의 서로 다른 본문이 있으면 어느 버전인지 추측하지 않는다.
        return {ident: next(iter(versions.values())) for ident, versions in candidates.items()
                if len(versions) == 1}

    def save(self, capture):
        prior = self.db.execute("""SELECT s.payload,c.digest FROM conversations c JOIN snapshots s
            ON s.thread_id=c.id AND s.digest=c.digest WHERE c.id=?""", (capture.get("id"),)).fetchone()
        # 먼저 원래 응답의 ID/본문을 확인한다. 기존 ID의 참조·축소는 보존 병합에서 처리한다.
        doc = normalize(capture, keep_unresolved=True)
        if prior:
            payload = json.loads(prior["payload"])
            old = payload["document"]
            old_ids = {m.get("id") for m in old["messages"]}
            new_ids = {m.get("id") for m in doc["messages"]}
            if (new_ids - old_ids or old.get("ui_recollections") or
                    old.get("history_reconstruction") or old.get("tool_merges")):
                from .tool_merge import merge_observation
                merged, details = merge_observation(old, doc, capture, prior["digest"])
                if not details["added_message_ids"]:
                    return {"id": doc["id"], "changed": False, "messages": len(old["messages"]),
                            "warnings": old["warnings"], "text_complete": False,
                            "reference_recoveries": [], **details}
                # 기존 이미지 연결/첨부는 그대로 두고 새 미리보기 파일만 추가한다.
                for attachment in self._attachments(doc["id"], doc["attachments"]):
                    if attachment not in merged["attachments"]:
                        merged["attachments"].append(attachment)
                preserved_capture = dict(capture)
                if "source_browser" in payload:
                    preserved_capture["source_browser"] = payload["source_browser"]
                return {**self._save_document(merged, preserved_capture), **details}
        answers = self._reference_answers(capture.get("id")) if has_reference(dumps(capture)) else {}
        resolve_from_history(doc, answers)
        missing_references = unresolved_messages(doc)
        if missing_references:
            raise UnresolvedReferenceError(missing_references)
        if prior:
            missing = (old_ids - {None}) - new_ids
            if missing:
                raise IncompleteHistoryError(missing)
            old_attachments = {a.get("name") for a in old.get("attachments", []) if a.get("available")}
            new_attachments = {a.get("name") for a in doc.get("attachments", [])}
            if old_attachments - new_attachments or any(m.get("media") for m in old["messages"]):
                raise ProtectedContentError("기존 이미지/첨부 연결을 새 조회 결과에서 확인할 수 없어 보존합니다.")
            old_messages = {m["id"]: m for m in old["messages"]}
            for message in doc["messages"]:
                previous_message = old_messages.get(message["id"])
                if previous_message and (
                        previous_message["role"] != message["role"] or
                        (not has_reference(previous_message["text"])
                         and len(message["text"].strip()) < len(previous_message["text"].strip()))):
                    raise ProtectedContentError(
                        "동일 메시지 ID의 본문이 짧아지거나 역할이 달라졌습니다. 기존 본문을 보존합니다.")
        doc["attachments"] = self._attachments(doc["id"], doc["attachments"])
        if any(not a["available"] for a in doc["attachments"]):
            doc["warnings"].append("일부 첨부 원본을 복사하지 못했습니다. 텍스트만 보관된 항목이 있습니다.")
            doc["text_complete"] = False  # 다음 실행에서 임시 파일을 다시 조회/복사한다.
        return self._save_document(doc, capture)

    def record_observation(self, capture):
        ident = capture.get("id")
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", ident):
            raise ArchiveError("관측 원본의 대화 ID가 올바르지 않습니다.")
        raw = dumps(capture)
        stable = {key: value for key, value in capture.items() if key != "collected_at"}
        digest = hashlib.sha256(dumps(stable).encode("utf-8")).hexdigest()
        # 원문/수정 버전이 같으면 관측 시각만 갱신한다. 같은 원문을 매 예약마다 복제하지 않는다.
        self.db.execute("""INSERT INTO observations VALUES (?,?,?,?)
            ON CONFLICT(thread_id,digest) DO UPDATE SET captured_at=excluded.captured_at""",
                        (ident, digest, now(), raw))
        self.db.commit()
        target = self.output / "observations" / ident / (digest + ".json")
        if not target.exists():
            stored = self.db.execute("SELECT payload FROM observations WHERE thread_id=? AND digest=?",
                                     (ident, digest)).fetchone()[0]
            atomic_write(target, stored + "\n")

    def _save_document(self, doc, capture, last_error=None, last_checked_at=None):
        # 읽는 시각과 일회성 미리보기 경로는 버전 판정에서 제외한다.
        digest = hashlib.sha256(dumps(doc).encode("utf-8")).hexdigest()
        prior = self.db.execute("SELECT * FROM conversations WHERE id=?", (doc["id"],)).fetchone()
        stamp = capture.get("collected_at") or now()
        stem = (prior["stem"] if prior else
                date_text(doc["created_at"])[:10] + "_" + safe_name(doc["title"]) + "_" + doc["id"])
        payload = {"document": doc, "source_pages": capture["pages"], "captured_at": stamp}
        if "source_browser" in capture:
            payload["source_browser"] = capture["source_browser"]
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("INSERT OR IGNORE INTO snapshots VALUES (?,?,?,?)",
                            (doc["id"], digest, stamp, dumps(payload)))
            self.db.execute("""INSERT INTO conversations
                (id,title,stem,digest,listed_updated_at,text_complete,last_checked_at,last_error)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET title=excluded.title,digest=excluded.digest,
                listed_updated_at=excluded.listed_updated_at,text_complete=excluded.text_complete,
                last_checked_at=excluded.last_checked_at,last_error=excluded.last_error""",
                (doc["id"], doc["title"], stem, digest, capture.get("listed_updated_at"),
                 int(doc["text_complete"]), last_checked_at or stamp, last_error))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        self.export(doc["id"])
        return {"id": doc["id"], "changed": prior is None or prior["digest"] != digest,
                "messages": len(doc["messages"]), "warnings": doc["warnings"],
                "text_complete": doc["text_complete"],
                "reference_recoveries": doc.get("reference_recoveries", [])}

    def repair_references(self):
        """구 버전이 정상 처리한 참조를 재검증한다. 기존 스냅샷과 재시도 이력은 유지한다."""
        rows = self.db.execute("""SELECT c.*,s.payload FROM conversations c
            JOIN snapshots s ON s.thread_id=c.id AND s.digest=c.digest ORDER BY c.id""").fetchall()
        repaired = []
        for row in rows:
            payload = json.loads(row["payload"])
            doc = payload["document"]
            if not unresolved_messages(doc):
                continue
            restored = resolve_from_history(doc, self._reference_answers(row["id"]))
            remaining = unresolved_messages(doc)
            # 로컬 복원은 원격 재조회 성공이 아니다. 기존 실패/대기 이력을 초기화하지 않는다.
            doc["text_complete"] = False
            warning = (REFERENCE_WARNING if remaining else
                       "이전 저장본의 동일 메시지 ID로 본문을 복원했습니다. 최신 대화 재확인이 필요합니다.")
            doc["warnings"] = sorted(set(doc["warnings"] + [warning]))
            saved = self._save_document(doc, {
                "pages": payload["source_pages"], "listed_updated_at": row["listed_updated_at"],
                "collected_at": payload["captured_at"],
            }, last_error=row["last_error"])
            repaired.append({"id": row["id"], "changed": saved["changed"],
                             "restored_message_ids": [r["message_id"] for r in restored],
                             "unresolved_message_ids": [m.get("id") for m in remaining]})
        self.write_index()
        return {"repaired": repaired, "restored_messages": sum(
            len(r["restored_message_ids"]) for r in repaired)}

    def repair_history(self, thread_ids):
        from .history_repair import plan_repairs
        # 전체 계획을 검증한 뒤 저장한다. 충돌한 대화가 있으면 어느 것도 바꾸지 않는다.
        plans = plan_repairs(self.db, thread_ids)
        for row, doc, pages, report in plans:
            if not report["changed"]:
                continue
            saved = self._save_document(doc, {
                "pages": pages, "listed_updated_at": row["listed_updated_at"],
            }, last_error=row["last_error"], last_checked_at=row["last_checked_at"])
            report["changed"] = saved["changed"]
        result = {"applied": True, "finished_at": now(),
                  "repairs": [plan[3] for plan in plans], "remote_recollected": False}
        # 원격 실행 이력, 실패 횟수, last_run.json은 수정하지 않는다.
        if any(plan[3]["changed"] for plan in plans):
            digest = hashlib.sha256(dumps(result).encode("utf-8")).hexdigest()
            atomic_write(self.output / "repairs" / ("history-" + digest + ".json"),
                         json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            self.write_index()
        return result

    def export(self, ident):
        row = self.db.execute("SELECT * FROM conversations WHERE id=?", (ident,)).fetchone()
        snap = self.db.execute("SELECT * FROM snapshots WHERE thread_id=? AND digest=?",
                               (ident, row["digest"])).fetchone()
        payload = json.loads(snap["payload"])
        doc = payload["document"]
        history_dir = Path("history") / ident
        # 관측한 모든 버전의 JSON과 읽을 수 있는 Markdown을 별도로 남긴다.
        for version in self.db.execute("SELECT * FROM snapshots WHERE thread_id=?", (ident,)):
            data = json.loads(version["payload"])
            raw = self.output / history_dir / (version["digest"] + ".json")
            if not raw.exists():
                atomic_write(raw, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            version_md = raw.with_suffix(".md")
            if not version_md.exists():
                atomic_write(version_md, render(data["document"], data["captured_at"], "../../"))
        atomic_write(self.output / "conversations" / (row["stem"] + ".md"),
                     render(doc, payload["captured_at"], "../"))
        atomic_write(self.output / "conversations" / (row["stem"] + ".txt"),
                     render(doc, payload["captured_at"], "../", plain=True))

    def export_all(self):
        for row in self.db.execute("SELECT id FROM conversations").fetchall():
            self.export(row["id"])
        self.write_index()

    def ingest(self, bundle):
        if bundle.get("schema_version") != 1:
            raise ArchiveError("지원하지 않는 수집 파일 버전입니다.")
        prior_run = self.db.execute("SELECT report FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        prior_report = json.loads(prior_run[0]) if prior_run else {}
        retries = RetryHistory.load(self.db)
        report = {"finished_at": now(), "discovered": bundle.get("discovered", 0),
                  "changed": 0, "unchanged": bundle.get("skipped", 0),
                  "saved": [], "errors": list(bundle.get("errors", [])),
                  "warnings": list(bundle.get("warnings", [])),
                  "deferred": list(bundle.get("deferred", [])),
                  "rate_limit_policy_version": RATE_LIMIT_POLICY_VERSION,
                  "remote_checked": bool(bundle.get("remote_checked")),
                  "rate_limit_backoff_skipped": bool(bundle.get("rate_limit_backoff_skipped")),
                  "run_status": bundle.get("run_status", "completed")}
        for capture in bundle.get("conversations", []):
            try:
                self.record_observation(capture)
                saved = self.save(capture)
                report["saved"].append(saved)
                report["changed" if saved["changed"] else "unchanged"] += 1
            except (ArchiveError, OSError, sqlite3.Error) as exc:
                error = {"id": capture.get("id"), "stage": getattr(exc, "stage", "save"),
                         "message": str(exc)[:400]}
                if isinstance(exc, (UnresolvedReferenceError, IncompleteHistoryError)):
                    error.update(code=exc.code, message_ids=exc.message_ids)
                elif isinstance(exc, ProtectedContentError):
                    error["code"] = exc.code
                report["errors"].append(error)
        for error in report["errors"]:
            self.db.execute("UPDATE conversations SET last_error=?,text_complete=0 WHERE id=?",
                            (error["message"], error.get("id")))
        self.db.commit()
        report["total"] = self.db.execute("SELECT count(*) FROM conversations").fetchone()[0]
        report["output"] = str(self.output)
        report["new_errors"] = [e for e in report["errors"] if error_key(e) not in retries.seen_errors]
        report["recovered_ids"] = [s["id"] for s in report["saved"]
                                   if s["text_complete"] and s["id"] in retries.threads]
        report["recovered"] = bool(report["recovered_ids"])
        retries.apply(report)
        report["excluded"] = [r for r in retries.threads.values() if r["excluded"]]
        old_excluded = {r["id"] for r in prior_report.get("excluded", [])}
        report["new_exclusions"] = [r for r in report["excluded"] if r["id"] not in old_excluded]
        report["rate_limit_streak"] = retries.rate_limit_streak
        report["rate_limit_skip_runs_remaining"] = retries.rate_limit_skip_runs_remaining
        if report["errors"]:
            report["run_status"] = "partial_failure"
        from .verification import verification_queue
        report["verification_required"] = verification_queue(self.db, retries=retries)["items"]
        pending_key = lambda item: dumps([item["id"], item.get("source_updated_at"), item["reasons"]])
        old_pending = {pending_key(item) for item in prior_report.get("verification_required", [])}
        report["new_verification_required"] = [
            item for item in report["verification_required"] if item["eligible"] and pending_key(item) not in old_pending]
        if report["verification_required"] and report["run_status"] == "completed":
            report["run_status"] = "verification_required"
        self.db.execute("INSERT INTO runs(finished_at,report) VALUES (?,?)",
                        (report["finished_at"], dumps(report)))
        self.db.commit()
        atomic_write(self.output / "last_run.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        # 중단된 이전 실행이 있더라도 DB에 커밋된 내용으로 내보내기를 복구한다.
        self.export_all()
        return report

    def write_index(self):
        rows = self.db.execute("""SELECT c.*,s.payload FROM conversations c
            JOIN snapshots s ON s.thread_id=c.id AND s.digest=c.digest ORDER BY c.id""").fetchall()
        entries = [(row, json.loads(row["payload"])["document"]) for row in rows]
        entries.sort(key=lambda entry: parse_date(entry[1].get("updated_at")) or
                     datetime.min.replace(tzinfo=KST), reverse=True)
        latest = self.db.execute("SELECT report FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        report = json.loads(latest[0]) if latest else {}
        lines = ["# ChatGPT 대화 보관함", "", "대화 제목을 누르면 질문과 답변을 읽을 수 있습니다.", "",
                 "- 시간 표기: 한국 시간(KST)", "- 원본: Codex 앱이 조회해 준 ChatGPT 대화의 현재 분기",
                 "- 보관 범위: 조회된 텍스트와 복사에 성공한 첨부 파일. 계정 전체/모든 분기 백업을 뜻하지 않습니다.",
                 "- ChatGPT에서 삭제하기 전, 필요한 질문·답변과 첨부가 이 파일에 있는지 확인하세요.", ""]
        if report:
            lines += ["최근 실행: " + date_text(report["finished_at"]), ""]
            for warning in report.get("warnings", []):
                lines += ["> " + warning, ""]
            if report.get("errors"):
                lines += ["> 최근 실행에서 %d건을 조회/저장하지 못했습니다. 기존 백업은 유지했습니다." % len(report["errors"]), ""]
            if report.get("deferred"):
                lines += ["> 호출 제한·재조회 간격·시간 제한으로 %d건의 조회를 미뤘습니다." % len(report["deferred"]), ""]
        retry_state = RetryHistory.load(self.db).threads
        lines += ["| 대화 | TXT | 메시지 | 확인 상태 | 마지막 업데이트 |", "|---|---|---:|---|---|"]
        for row, doc in entries:
            status = ("재확인 필요" if row["last_error"] or not row["text_complete"] or doc["warnings"]
                      else "조회된 텍스트 저장됨")
            if any(problem["code"] == "history_unverified" for problem in issues(doc)):
                status = "전체 이력 미검증 — 재확인 필요 (화면 확인 대기)"
            if unresolved_messages(doc):
                status = "답변 본문 누락 (참조만 저장됨)"
            retry = retry_state.get(row["id"], {})
            if retry.get("excluded"):
                status = "자동 수집 제외 (연속 실패 %d회, 기존 백업 보존)" % retry["consecutive_failures"]
            path = "conversations/" + row["stem"]
            lines.append("| [%s](%s) | [TXT](%s) | %s | %s | %s |" % (
                markdown_label(row["title"]), quote(path + ".md"), quote(path + ".txt"),
                len(doc["messages"]), status, date_text(doc.get("updated_at"))))
        lines += ["", "## 이전 버전", "", "`history/대화ID/`에 관측한 이전 버전의 Markdown과 JSON을 보관합니다.",
                  "대화가 앱 목록에서 사라져도 여기 저장된 파일을 자동 삭제하지 않습니다.", ""]
        atomic_write(self.output / "INDEX.md", "\n".join(lines))


def render(doc, captured_at, prefix, plain=False):
    heading = "" if plain else "# "
    lines = [heading + (doc["title"] if plain else markdown_label(doc["title"])), "",
             "대화 주소: https://chatgpt.com/c/" + doc["id"],
             "대화 시작: " + date_text(doc["created_at"]),
             "앱의 수정 시각: " + date_text(doc["updated_at"]),
             "이 버전 저장: " + date_text(captured_at), ""]
    warnings = list(doc["warnings"])
    if any(p["code"] == "history_unverified" for p in issues(doc)) and HISTORY_WARNING not in warnings:
        warnings.append(HISTORY_WARNING)
    if unresolved_messages(doc) and REFERENCE_WARNING not in warnings:
        warnings.append(REFERENCE_WARNING)
    for warning in warnings:
        lines += [("확인 필요: " if plain else "> 확인 필요: ") + warning, ""]
    question = 0
    repair = doc.get("history_reconstruction", {})
    segment_starts = {segment["message_ids"][0] for segment in repair.get("segments", [])[1:]
                      if segment["message_ids"]}
    for message in doc["messages"]:
        if message["id"] in segment_starts:
            from .history_repair import GAP_WARNING
            lines += ["=" * 60 if plain else "---", "",
                      ("확인 필요: " if plain else "> 확인 필요: ") + GAP_WARNING, ""]
        if message["role"] == "user":
            question += 1
            label = "질문 %d · 나" % question
        else:
            label = "답변 %d · ChatGPT" % question if question else "답변 · ChatGPT"
        body = message["text"]
        if message["role"] == "assistant" and has_reference(body):
            body = "[답변 본문 미수집 — 아래 내용은 앱이 반환한 참조 표시입니다.]\n\n" + body
        lines += ["=" * 60 if plain else "---", "", ("" if plain else "## ") + label,
                  "", date_text(message["time"]), "", body, ""]
        for media_path in message.get("media", []):
            target = prefix + media_path
            lines += [("이미지: " + target if plain else
                       "![대화 이미지](" + quote(target) + ")"), ""]
        if message["id"] in doc.get("questions_without_observed_answer", []):
            lines += [("확인 필요: " if plain else "> 확인 필요: ") +
                      "이 질문에 연결되는 답변은 확보하지 못했습니다. 원문을 추정해 채우지 않았습니다.", ""]
    if doc["attachments"]:
        lines += ["" if plain else "---", "", ("" if plain else "## ") + "첨부 파일", ""]
        for item in doc["attachments"]:
            if item["available"]:
                target = prefix + item["path"]
                lines.append("- " + (item["name"] + " → " + target if plain else
                                      "[" + markdown_label(item["name"]) + "](" + quote(target) + ")"))
            else:
                lines.append("- " + markdown_label(item["name"]) + " (원본 미보관)")
        lines.append("")
    return "\n".join(lines)


@contextmanager
def archive_lock(output):
    # macOS/Linux. 커밋과 파일 내보내기를 포함한 전체 실행의 중첩을 막는다.
    import fcntl
    output = Path(output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (output / ".archive.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ArchiveError("다른 백업이 실행 중입니다. 다음 실행에서 다시 시도하세요.") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
