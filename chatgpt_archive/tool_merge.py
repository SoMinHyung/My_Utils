"""조회 도구의 새 메시지만 추가한다. 기존 본문·미디어·출처는 변경하지 않는다."""
from copy import deepcopy
import hashlib
import re

from .archive import ProtectedContentError, UnresolvedReferenceError, dumps
from .content_validation import REFERENCE, has_reference
from .history_repair import _sequences
from .quality import HISTORY_WARNING, issues


MERGE_WARNING = (
    "기존 원문을 보존하고 공통 메시지 ID로 연결되는 새 조회 메시지만 추가했습니다. "
    "조회에서 빠진 구간과 전체 이력은 검증되지 않았습니다."
)


def merge_observation(old, incoming, capture, parent_digest):
    if old["id"] != incoming["id"]:
        raise ProtectedContentError("병합할 대화 ID가 다릅니다.")
    before = {m["id"]: m for m in old["messages"]}
    observed = {m["id"]: m for m in incoming["messages"]}
    if len(before) != len(old["messages"]) or not before:
        raise ProtectedContentError("기존 메시지 ID가 중복되거나 비어 있습니다.")
    added = [mid for mid in observed if mid not in before]
    invalid = [observed[mid] for mid in added if observed[mid]["role"] == "assistant"
               and has_reference(observed[mid]["text"])]
    if invalid:
        raise UnresolvedReferenceError(invalid)
    shared = before.keys() & observed.keys()
    if not shared:
        raise ProtectedContentError("공통 메시지 ID가 없어 새 조회 구간의 위치를 확인할 수 없습니다.")
    preserved = []
    for mid in shared:
        original, new = before[mid], observed[mid]
        if original["role"] != new["role"]:
            raise ProtectedContentError("동일 메시지 ID의 역할이 충돌합니다: " + mid)
        if original["text"] == new["text"]:
            continue
        if new["role"] == "assistant" and has_reference(new["text"]):
            explicit = [source for block in REFERENCE.findall(new["text"])
                        for source in re.findall(r'\bsource_message_id="([^"]+)"', block)]
            if any(source != mid for source in explicit):
                raise ProtectedContentError("참조가 다른 메시지 ID를 가리킵니다: " + mid)
            reason = "reference"
        elif len(new["text"].strip()) < len(original["text"].strip()):
            reason = "shorter_body"
        else:
            # 더 길다는 이유로 수정본/다른 분기를 기존 원문과 섞지 않는다.
            raise ProtectedContentError("동일 메시지 ID의 본문이 충돌합니다: " + mid)
        preserved.append({"id": mid, "reason": reason})
    if any(not observed[mid]["text"].strip() for mid in added):
        raise ProtectedContentError("새 메시지의 본문이 비어 있어 자동 병합하지 않습니다.")
    if added and (capture.get("changing") or any(
            turn.get("status") != "completed" and
            any(item.get("id") in added for item in turn.get("items", []))
            for page in capture["pages"] for turn in page.get("turns", []))):
        raise ProtectedContentError("생성/변경 중인 새 메시지는 완료 후 다시 조회해야 합니다.")

    messages = deepcopy(before)
    messages.update({mid: deepcopy(observed[mid]) for mid in added})
    sequences = _sequences(old)
    old_sequence_ids = [mid for seq in sequences for mid in seq]
    if old_sequence_ids != list(before):
        raise ProtectedContentError("기존 복원 구간의 메시지 순서가 올바르지 않습니다.")
    old_pairs = {}
    for sequence in sequences:
        question = None
        for mid in sequence:
            message = before[mid]
            if message["role"] == "user":
                question = message
            elif (question is not None and message.get("turn_id") is not None and
                  message["turn_id"] in (question["id"], question.get("turn_id"))):
                old_pairs[mid] = question["id"]
    sequences = sequences + [list(observed)]
    edges = {mid: set() for mid in messages}
    neighbours = {mid: set() for mid in messages}
    for sequence in sequences:
        for left, right in zip(sequence, sequence[1:]):
            edges[left].add(right)
            neighbours[left].add(right)
            neighbours[right].add(left)

    # 원래 분리돼 있던 구간은 그대로 보존한다. 새 구간은 반드시 기존 ID와 연결돼야 한다.
    unseen = set(messages)
    segments = []
    old_position = {mid: i for i, mid in enumerate(before)}
    while unseen:
        pending = [min(unseen)]
        component = set()
        while pending:
            mid = pending.pop()
            if mid not in component:
                component.add(mid)
                pending.extend(neighbours[mid] - component)
        unseen -= component
        if not component.intersection(before):
            raise ProtectedContentError("새 메시지 구간이 기존 백업과 연결되지 않습니다.")
        incoming_edges = {mid: 0 for mid in component}
        for mid in component:
            for after in edges[mid]:
                incoming_edges[after] += 1
        ready = [mid for mid, count in incoming_edges.items() if not count]
        ordered = []
        while ready:
            if len(ready) != 1:
                raise ProtectedContentError("새 메시지의 위치가 모호하거나 분기와 충돌합니다.")
            mid = ready.pop()
            ordered.append(mid)
            for after in edges[mid]:
                incoming_edges[after] -= 1
                if not incoming_edges[after]:
                    ready.append(after)
        if len(ordered) != len(component):
            raise ProtectedContentError("조회 메시지 순서가 기존 백업과 충돌합니다.")
        segments.append(ordered)
    segments.sort(key=lambda seq: min(old_position[mid] for mid in seq if mid in before))
    ordered = [mid for seq in segments for mid in seq]
    if [mid for mid in ordered if mid in before] != list(before):
        raise ProtectedContentError("병합 결과가 기존 메시지 순서를 바꿉니다.")

    # 도구가 관측한 질문-답변 연결과 최종 배치가 같아야 한다.
    for segment in segments:
        question = None
        for mid in segment:
            message = messages[mid]
            if message["role"] == "user":
                question = message
            elif mid in old_pairs and (question is None or old_pairs[mid] != question["id"]):
                raise ProtectedContentError("기존 답변의 질문 연결을 바꾸는 병합은 허용하지 않습니다: " + mid)
            elif mid in added:
                paired = [m["id"] for m in incoming["messages"]
                          if m["role"] == "user" and m["turn_id"] == message["turn_id"]]
                if (question is None or (paired and paired != [question["id"]]) or
                        (not paired and message["turn_id"] not in
                         (question["id"], question.get("turn_id")))):
                    raise ProtectedContentError("새 답변을 기존 질문과 확실히 연결할 수 없습니다: " + mid)

    report = {
        "added_message_ids": added,
        "added_questions": sum(messages[mid]["role"] == "user" for mid in added),
        "added_answer_bodies": sum(messages[mid]["role"] == "assistant" for mid in added),
        "preserved_missing_ids": [mid for mid in before if mid not in observed],
        "preserved_bodies": sorted(preserved, key=lambda item: old_position[item["id"]]),
    }
    if not added:
        return deepcopy(old), report
    doc = deepcopy(old)
    doc["messages"] = [messages[mid] for mid in ordered]
    doc["title"], doc["updated_at"] = incoming["title"], incoming["updated_at"]
    doc["source_incomplete"] = old.get("source_incomplete", False) or incoming["source_incomplete"]
    doc["text_complete"] = False
    doc["warnings"] = sorted(set(doc["warnings"] + incoming["warnings"] +
                                 [HISTORY_WARNING, MERGE_WARNING]))
    if "history_reconstruction" in doc:
        doc["history_reconstruction"]["segments"] = [
            {"message_ids": seq, "connection_to_previous_verified": False} for seq in segments]
    # 이전 UI 검증 증거는 보존하되 확장된 본문 전체를 검증했다고 재사용하지 않는다.
    doc["questions_without_observed_answer"] = [
        mid for problem in issues(doc) if problem["code"] == "missing_answer"
        for mid in problem["message_ids"]]
    raw = {k: v for k, v in capture.items() if k != "collected_at"}
    doc.setdefault("tool_merges", []).append({
        "source": "codex_app_read_thread", "parent_snapshot_digest": parent_digest,
        "observation_sha256": hashlib.sha256(dumps(raw).encode("utf-8")).hexdigest(),
        **deepcopy(report),
    })
    return doc, report
