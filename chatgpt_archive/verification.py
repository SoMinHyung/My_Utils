"""화면 검증 대기열. 읽기 전용이며 실패/제외/호출 제한을 임의로 해제하지 않는다."""
from datetime import datetime, timedelta, timezone
import hashlib
import json

from .quality import issues
from .retry_policy import RetryHistory, parse_stamp


def latest_observations(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='observations'").fetchone():
        return {}
    return {row["thread_id"]: dict(row) for row in db.execute(
        "SELECT * FROM observations ORDER BY captured_at,rowid")}


def verification_queue(db, retries=None):
    retries = retries or RetryHistory.load(db)
    latest = latest_observations(db)
    known = {}
    for row in db.execute("""SELECT c.*,s.payload FROM conversations c JOIN snapshots s
            ON c.id=s.thread_id AND c.digest=s.digest ORDER BY c.last_checked_at,c.id"""):
        known[row["id"]] = (dict(row), json.loads(row["payload"])["document"])
    items = []
    current = datetime.now(timezone.utc)
    for ident in list(known) + [key for key in latest if key not in known]:
        row, doc = known.get(ident, ({}, {}))
        observation = latest.get(ident)
        capture = json.loads(observation["payload"]) if observation else {}
        problems = issues(doc) if doc else [{"code": "no_valid_snapshot", "message_ids": []}]
        coverage = doc.get("coverage", {})
        if observation and coverage.get("observation_digest") != observation["digest"]:
            problems.append({"code": "source_update_unverified", "message_ids": []})
        if not problems:
            continue
        retry = retries.threads.get(ident, {})
        blocked = None
        if retries.rate_limit_skip_runs_remaining:
            blocked = "rate_limit_backoff"
        elif retry.get("excluded"):
            blocked = "excluded"
        elif (parse_stamp(retry.get("next_retry_at")) or current) > current:
            blocked = "retry_backoff"
        # 화면에도 없는 답변/지원하지 않는 요소는 매 예약마다 무한 재조회하지 않는다.
        reviewed = parse_stamp(doc.get("ui_reviewed_at"))
        if (not blocked and reviewed and reviewed + timedelta(hours=24) > current
                and doc.get("ui_review_outcome", "blocked") in ("blocked", "complete")
                and doc.get("ui_observation_digest") == (observation or {}).get("digest")):
            blocked = "ui_review_backoff"
        page = next(iter(capture.get("pages", [])), {})
        # 본문 복사 증거는 전체 이력/이미지 검증과 다르다. 다음 예약의 재개 위치만 제공한다.
        evidence = doc.get("ui_body_evidence", {})
        unchecked = [m["id"] for m in doc.get("messages", []) if
                     evidence.get(m["id"], {}).get("sha256") !=
                     hashlib.sha256(m["text"].encode("utf-8")).hexdigest() or
                     evidence.get(m["id"], {}).get("observation_digest") !=
                     (observation or {}).get("digest")]
        items.append({
            "id": ident, "title": row.get("title") or page.get("thread", {}).get("title") or ident,
            "url": "https://chatgpt.com/c/" + ident,
            "observation_digest": (observation or {}).get("digest"),
            "source_updated_at": capture.get("listed_updated_at", row.get("listed_updated_at")),
            "reasons": sorted(set(p["code"] for p in problems)),
            "eligible": blocked is None, "blocked_reason": blocked,
            "verified_body_count": len(doc.get("messages", [])) - len(unchecked),
            "next_unverified_message_id": unchecked[0] if unchecked else None,
            "last_observed_window": doc.get("ui_last_observed_window", []),
        })
    return {"items": items, "eligible": sum(item["eligible"] for item in items)}
