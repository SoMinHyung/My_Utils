"""기존 실행 이력을 이용한 재조회 간격·자동 제외·알림 중복 제거 정책."""
from datetime import datetime, timedelta, timezone
import json
import re
from .content_validation import unresolved_messages
from .quality import issues


FAILURE_LIMIT = 100
RETRY_BASE_SECONDS = 20 * 60
RETRY_MAX_SECONDS = 24 * 60 * 60
RATE_LIMIT_POLICY_VERSION = 2
RATE_LIMIT_MAX_SKIPPED_RUNS = 7
CONTENT_ERRORS = {"incomplete_history", "unresolved_content_reference", "ui_verification_required"}


def error_kind(error):
    message = error.get("message", "").lower()
    code = error.get("code")
    if code == "rate_limited" or re.search(r"too many requests|rate.?limit|\b429\b", message):
        return "rate_limited"
    if code == "timeout" or "시간 초과" in message:
        return "timeout:" + error.get("stage", "read_thread")
    if "unavailable or failed hosts" in message:
        return "host_unavailable"
    if "no codex thread found" in message or "not found" in message or "찾을 수 없" in message:
        return "not_found"
    if "net::" in message:
        return "network"
    return code or error.get("message", "unknown")


def error_key(error):
    kind = error_kind(error)
    # 호출 제한은 대화별 오류가 아닌 공통 조회 서비스 상태다.
    return ("service" if kind == "rate_limited" else error.get("id"),
            "validation" if error.get("code") in CONTENT_ERRORS else
            error.get("stage", "read_thread"), kind)


def parse_stamp(value):
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp
    except (AttributeError, ValueError, TypeError):
        return None


class RetryHistory:
    """runs는 원본이다. 읽기 전용 state에서도 과거 실패를 빠짐없이 반영한다."""
    def __init__(self):
        self.threads = {}
        self.seen_errors = set()
        self.rate_limit_streak = 0
        self.rate_limit_skip_runs_remaining = 0

    def apply(self, report):
        stamp = parse_stamp(report.get("finished_at"))
        saved_ids = {item["id"] for item in report.get("saved", [])}
        rate_limited = any(error_kind(e) == "rate_limited" for e in report.get("errors", []))
        scheduled_backoff = report.get("rate_limit_policy_version") == RATE_LIMIT_POLICY_VERSION
        healthy_check = report.get("remote_checked") and not report.get("errors")
        for ident in saved_ids:
            self.threads.pop(ident, None)
        self.seen_errors = {key for key in self.seen_errors if key[0] not in saved_ids}
        if not rate_limited and (saved_ids or (scheduled_backoff and healthy_check)):
            self.seen_errors = {key for key in self.seen_errors if key[0] != "service"}
        # 구 정책의 30분 대기와 누적 호출 제한은 새 정책의 연속 횟수로 이관하지 않는다.
        # 대화별 실패/제외 이력은 아래에서 그대로 복원한다.
        if scheduled_backoff:
            if rate_limited:
                self.rate_limit_streak += 1
                self.rate_limit_skip_runs_remaining = min(
                    RATE_LIMIT_MAX_SKIPPED_RUNS, 2 ** min(self.rate_limit_streak - 1, 3) - 1)
            elif report.get("rate_limit_backoff_skipped"):
                self.rate_limit_skip_runs_remaining = max(0, self.rate_limit_skip_runs_remaining - 1)
            elif healthy_check:
                self.rate_limit_streak = 0
                self.rate_limit_skip_runs_remaining = 0
        counted = set()
        for error in report.get("errors", []):
            self.seen_errors.add(error_key(error))
            ident = error.get("id")
            if error_kind(error) == "rate_limited":
                continue
            # 미조회/저장 실패는 대화 읽기 실패 횟수에 넣지 않는다.
            if (not ident or ident in counted or error.get("attempted") is False or
                    error.get("code") in CONTENT_ERRORS or
                    error.get("stage", "read_thread") != "read_thread"):
                continue
            counted.add(ident)
            old = self.threads.get(ident, {})
            failures = old.get("consecutive_failures", 0) + 1
            delay = min(RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * 2 ** min(failures - 1, 7))
            self.threads[ident] = {
                "id": ident, "consecutive_failures": failures,
                "excluded": failures >= FAILURE_LIMIT,
                "last_failure_at": report.get("finished_at"),
                "next_retry_at": (stamp + timedelta(seconds=delay)).isoformat() if stamp else None,
            }

    @classmethod
    def load(cls, db):
        history = cls()
        for row in db.execute("SELECT report FROM runs ORDER BY id"):
            history.apply(json.loads(row[0]))
        return history

    def state(self):
        return {"retry_state": list(self.threads.values()),
                "rate_limit_policy_version": RATE_LIMIT_POLICY_VERSION,
                "rate_limit_streak": self.rate_limit_streak,
                "rate_limit_skip_runs_remaining": self.rate_limit_skip_runs_remaining,
                "failure_limit": FAILURE_LIMIT}


def collection_state(db):
    conversations = []
    from .verification import latest_observations
    latest = latest_observations(db)
    for row in db.execute("""SELECT c.id,c.title,c.listed_updated_at,c.text_complete,s.payload
            FROM conversations c JOIN snapshots s ON s.thread_id=c.id AND s.digest=c.digest ORDER BY c.id"""):
        item = dict(row)
        document = json.loads(item.pop("payload"))["document"]
        item["text_complete"] = bool(item["text_complete"]) and not issues(document)
        missing = unresolved_messages(document)
        if missing:
            item["text_complete"] = False
            item["unresolved_message_ids"] = [m.get("id") for m in missing]
        conversations.append(item)
    by_id = {item["id"]: item for item in conversations}
    for ident, observation in latest.items():
        capture = json.loads(observation["payload"])
        first = next(iter(capture.get("pages", [])), {})
        if ident not in by_id:
            item = {"id": ident, "title": first.get("thread", {}).get("title") or ident,
                    "listed_updated_at": None, "text_complete": False}
            conversations.append(item)
            by_id[ident] = item
        by_id[ident]["source_checked_updated_at"] = capture.get("listed_updated_at")
        by_id[ident]["source_checked_title"] = first.get("thread", {}).get("title")
        by_id[ident]["source_checked_at"] = observation["captured_at"]
        by_id[ident]["awaiting_ui_verification"] = (
            not by_id[ident]["text_complete"])
    return {"conversations": conversations, **RetryHistory.load(db).state()}
