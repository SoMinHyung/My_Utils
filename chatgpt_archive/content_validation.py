"""앱의 본문 대신 반환되는 참조 표시를 실제 답변과 구분한다."""
import re


REFERENCE = re.compile(r":{1,2}chatgpt-content-reference\s*\{[^}\r\n]*\}")
REFERENCE_WARNING = "답변 본문 누락: 앱이 실제 답변 대신 참조 표시를 반환했습니다. 재조회가 필요합니다."


def has_reference(text):
    return isinstance(text, str) and bool(REFERENCE.search(text))


def unresolved_messages(document):
    return [message for message in document.get("messages", [])
            if message.get("role") == "assistant" and has_reference(message.get("text"))]


def referenced_message_id(text):
    """혼합 본문/여러 블록을 추측해서 치환하지 않는다."""
    if not isinstance(text, str) or not REFERENCE.fullmatch(text.strip()):
        return None
    found = re.search(r'\bsource_message_id="([A-Za-z0-9_-]+)"', text)
    return found.group(1) if found else None


def resolve_from_history(document, answers):
    restored = []
    for message in unresolved_messages(document):
        source_id = message.get("id")
        answer = answers.get(source_id)
        explicit_ids = re.findall(r'\bsource_message_id="([^"]+)"', "\n".join(
            REFERENCE.findall(message["text"])))
        # 참조 index는 메시지 ID가 아니다. 바깥 ID의 유일한 전체 원문으로만 대체한다.
        # 본문 일부를 조립하지 않으며 명시적 다른 ID는 거부한다.
        if not source_id or any(ident != source_id for ident in explicit_ids) or not answer:
            continue
        message["text"] = answer["text"]
        restored.append({"message_id": source_id, "snapshot_digest": answer["digest"],
                         "captured_at": answer["captured_at"]})
    if restored:
        document["reference_recoveries"] = document.get("reference_recoveries", []) + restored
    return restored
