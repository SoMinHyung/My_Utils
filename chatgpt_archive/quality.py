"""조회 종료와 원문 검증을 구분한다. 앱의 hasMore=False는 전체 이력 증거가 아니다."""
import hashlib
import json
from datetime import datetime

from .content_validation import has_reference


POLICY_VERSION = 2
HISTORY_WARNING = "대화 시작부터 끝까지 확인되지 않았습니다. 조회 도구의 마지막 페이지는 전체 이력을 보장하지 않습니다."


def fingerprint(messages):
    content = [{key: m.get(key) for key in ("id", "role", "text", "media")} for m in messages]
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def issues(document):
    """옛 버전의 text_complete=True도 독립적인 검증 증거 없이는 신뢰하지 않는다."""
    messages = document.get("messages", [])
    coverage = document.get("coverage", {})
    problems = []
    if not (coverage.get("policy_version") == POLICY_VERSION
            and coverage.get("source") == "chatgpt_authenticated_ui"
            and coverage.get("scope") == "observed_current_branch"
            and coverage.get("history_start") and coverage.get("history_end")
            and coverage.get("message_fingerprint") == fingerprint(messages)):
        problems.append({"code": "history_unverified", "message_ids": []})
    unanswered = []
    question = None
    answered = False
    for message in messages:
        mid = message.get("id")
        if message.get("role") == "user":
            if question and not answered:
                unanswered.append(question)
            question, answered = mid, False
        elif message.get("role") == "assistant":
            if not question:
                problems.append({"code": "missing_question", "message_ids": [mid]})
            elif message.get("text", "").strip() or message.get("media"):
                answered = True
            if has_reference(message.get("text")):
                problems.append({"code": "unresolved_content_reference", "message_ids": [mid]})
        if not message.get("text", "").strip() and not message.get("media"):
            problems.append({"code": "empty_message", "message_ids": [mid]})
    if question and not answered:
        unanswered.append(question)
    if unanswered:
        problems.append({"code": "missing_answer", "message_ids": unanswered})
    if document.get("source_incomplete"):
        problems.append({"code": "source_incomplete", "message_ids": []})
    if document.get("ui_unsupported_content"):
        problems.append({"code": "unsupported_content", "message_ids": []})
    if any(not a.get("available") for a in document.get("attachments", [])):
        problems.append({"code": "missing_attachment", "message_ids": []})
    return problems


def validate_ui_coverage(capture, messages):
    """현재 화면의 양 끝, 연속 창, 모든 본문·미디어를 직접 확인한 입력만 인정한다.

    브라우저는 이 증거를 UI 관측으로 만들어야 한다. 기존 read_thread 결과를
    start/end=True로 감싸는 것은 지원하지 않는다.
    """
    proof = capture.get("coverage")
    if proof is None:
        return None  # 이전 수동 복원 파일은 부분 복원으로 계속 사용 가능
    try:
        stamp = datetime.fromisoformat(capture["captured_at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("timezone required")
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        raise ValueError("전체 화면 검증의 실제 수집 시각(시간대 포함)이 필요합니다.") from exc
    ordered = [m["id"] for m in messages]
    observed = {m["id"]: m for m in capture["messages"]}
    media = capture.get("media", [])
    media_ids = {m["message_id"] for m in media}
    if (proof.get("policy_version") != POLICY_VERSION
            or proof.get("scope") != "observed_current_branch"
            or proof.get("history_start") is not True or proof.get("history_end") is not True
            or proof.get("first_message_id") != ordered[0]
            or proof.get("last_message_id") != ordered[-1]
            or proof.get("loading") is not False
            or proof.get("unsupported_content") != []
            or proof.get("start_observations", 0) < 3
            or proof.get("end_observations", 0) < 3):
        raise ValueError("화면 수집의 시작·끝·로딩·지원하지 않는 내용 검증이 부족합니다.")
    # 기존 백업과 합쳐서 순서를 만들 수 있다는 것과, 이번 화면에서 전부 봤다는 것은 다르다.
    if set(ordered) != set(observed) | media_ids or not ordered:
        raise ValueError("이번 화면 수집에서 기존 질문·답변 전체를 다시 확인하지 못했습니다.")
    edges = set()
    for window in capture.get("windows", []) + capture.get("media_windows", []):
        edges.update(zip(window, window[1:]))
    if any(pair not in edges for pair in zip(ordered, ordered[1:])):
        raise ValueError("화면 수집 창 사이에 직접 관측하지 않은 연결 구간이 있습니다.")
    for message in messages:
        source = observed.get(message["id"], {})
        if message["text"].strip():
            if message["role"] == "assistant" and source.get("source") != "chatgpt_ui_copy":
                raise ValueError("답변 전체 복사 버튼으로 확보하지 않은 본문이 있습니다.")
            if source.get("body_complete") is not True:
                raise ValueError("질문 펼치기/답변 전체 복사를 확인하지 않은 메시지가 있습니다.")
        if source.get("media_count", 0) != sum(m["message_id"] == message["id"] for m in media):
            raise ValueError("화면에서 확인한 이미지 수와 저장한 이미지 수가 다릅니다.")
    return {
        "policy_version": POLICY_VERSION, "source": capture["source"],
        "scope": "observed_current_branch", "history_start": True, "history_end": True,
        "first_message_id": ordered[0], "last_message_id": ordered[-1],
        "message_fingerprint": fingerprint(messages),
    }
