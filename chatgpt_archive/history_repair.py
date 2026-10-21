"""관측된 원문만 복원한다. 구간 사이의 미관측 대화나 분기를 추측하지 않는다."""
from collections import defaultdict
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from contextlib import closing

from .archive import ArchiveError, dumps, now, parse_date
from .content_validation import REFERENCE_WARNING, has_reference, referenced_message_id


HISTORY_WARNING = (
    "이전 저장본에서 확인된 메시지를 합쳐 복원한 부분 기록입니다. "
    "서버의 전체 대화·현재 분기와 대조하지 못했으므로 추가 누락 가능성이 있습니다."
)
GAP_WARNING = "이전 저장 구간과 이 구간의 연결은 확인되지 않았습니다. 사이에 미수집 내용이 있을 수 있습니다."


def _sequences(doc):
    """재복원 시에도 미관측 구간을 잇는 가짜 순서 증거를 만들지 않는다."""
    repair = doc.get("history_reconstruction")
    if repair:
        return [segment["message_ids"] for segment in repair["segments"]]
    return [[m["id"] for m in doc["messages"]]]


def reconstruct(current, snapshots):
    original = current["document"]
    ident = original["id"]
    variants = defaultdict(list)
    edges = defaultdict(set)
    neighbours = defaultdict(set)
    attachments = {}
    pages = {}
    for row in snapshots:
        payload = json.loads(row["payload"])
        doc = payload["document"]
        if doc["id"] != ident:
            raise ArchiveError("다른 대화의 이전 버전이 섞였습니다.")
        sequence_ids = [mid for seq in _sequences(doc) for mid in seq]
        doc_ids = [m.get("id") for m in doc["messages"]]
        if (not all(doc_ids) or len(set(doc_ids)) != len(doc_ids)
                or sorted(sequence_ids) != sorted(doc_ids)):
            raise ArchiveError("이전 버전의 메시지 ID 또는 구간 정보가 올바르지 않습니다.")
        for message in doc["messages"]:
            variants[message["id"]].append((message, row["digest"], row["captured_at"]))
        for sequence in _sequences(doc):
            for left, right in zip(sequence, sequence[1:]):
                edges[left].add(right)
                neighbours[left].add(right)
                neighbours[right].add(left)
        for item in doc["attachments"]:
            attachments[dumps(item)] = deepcopy(item)
        for page in payload.get("source_pages", []):
            pages[dumps(page)] = page

    chosen = {}
    provenance = {}
    current_by_id = {m["id"]: m for m in original["messages"]}
    for mid, observations in variants.items():
        roles = {m["role"] for m, _, _ in observations}
        turns = {m["turn_id"] for m, _, _ in observations}
        times = {m["time"] for m, _, _ in observations}
        if len(roles) != 1:
            raise ArchiveError("동일 메시지 ID의 역할·차례·시각이 충돌합니다: " + mid)
        role = next(iter(roles))
        metadata_source = None
        if len(turns) != 1 or len(times) != 1:
            # 페이지 첫 답변은 질문이 없으면 도구가 자기 ID로 임시 차례를 만든다.
            # 같은 답변이 실제 질문과 함께 관측된 경우에만 그 차례 정보를 사용한다.
            paired = [obs for obs in observations
                      if obs[0]["turn_id"] != mid and obs[0]["turn_id"] in variants
                      and all(q["role"] == "user" and q["time"] == obs[0]["time"]
                              for q, _, _ in variants[obs[0]["turn_id"]])]
            paired_metadata = {(m["turn_id"], m["time"]) for m, _, _ in paired}
            if (role != "assistant" or len(paired_metadata) != 1
                    or any(m["turn_id"] != mid and (m["turn_id"], m["time"]) not in paired_metadata
                           for m, _, _ in observations)):
                raise ArchiveError("동일 메시지 ID의 역할·차례·시각이 충돌합니다: " + mid)
            metadata_source = paired[0]
        bodies = {m["text"] for m, _, _ in observations
                  if role != "assistant" or not has_reference(m["text"])}
        if len(bodies) > 1:
            raise ArchiveError("동일 메시지 ID에 서로 다른 본문이 있어 자동 복원하지 않습니다: " + mid)
        # 참조의 출처가 정확히 일치하지 않으면 본문과 연결하지 않는다.
        if bodies and any(has_reference(m["text"]) and referenced_message_id(m["text"]) != mid
                          for m, _, _ in observations if role == "assistant"):
            raise ArchiveError("참조 표시의 메시지 ID가 일치하지 않습니다: " + mid)
        fallback = next((obs for obs in observations
                         if mid in current_by_id and obs[0]["text"] == current_by_id[mid]["text"]),
                        observations[0])
        selected = next((obs for obs in observations if obs[0]["text"] in bodies), fallback)
        chosen[mid] = deepcopy(selected[0])
        provenance[mid] = {"snapshot_digest": selected[1], "captured_at": selected[2]}
        if metadata_source:
            chosen[mid]["turn_id"] = metadata_source[0]["turn_id"]
            chosen[mid]["time"] = metadata_source[0]["time"]
            provenance[mid]["turn_metadata_snapshot_digest"] = metadata_source[1]

    # 연결된 구간 안에서 순서가 유일할 때만 합친다. 갈라진 분기는 거부한다.
    unseen = set(chosen)
    segments = []
    while unseen:
        pending = [min(unseen)]
        component = set()
        while pending:
            mid = pending.pop()
            if mid in component:
                continue
            component.add(mid)
            pending.extend(neighbours[mid] - component)
        unseen -= component
        incoming = {mid: 0 for mid in component}
        for mid in component:
            for after in edges[mid]:
                incoming[after] += 1
        ready = [mid for mid, count in incoming.items() if count == 0]
        ordered = []
        while ready:
            if len(ready) != 1:
                raise ArchiveError("이전 버전에 분기 또는 순서 모호성이 있어 자동 복원하지 않습니다.")
            mid = ready.pop()
            ordered.append(mid)
            for after in edges[mid]:
                incoming[after] -= 1
                if incoming[after] == 0:
                    ready.append(after)
        if len(ordered) != len(component):
            raise ArchiveError("이전 버전의 메시지 순서가 서로 충돌합니다.")
        segments.append(ordered)

    def time_range(segment):
        dates = [parse_date(chosen[mid]["time"]) for mid in segment]
        if any(date is None for date in dates) or dates != sorted(dates):
            raise ArchiveError("분리된 구간을 배치할 메시지 시각이 없거나 순서가 충돌합니다.")
        return dates[0], dates[-1]

    if len(segments) > 1:
        segments.sort(key=lambda seq: time_range(seq)[0])
        for previous, following in zip(segments, segments[1:]):
            if time_range(previous)[1] >= time_range(following)[0]:
                raise ArchiveError("분리된 저장 구간의 시간이 겹칩니다. 원본 분기 확인이 필요합니다.")

    doc = deepcopy(original)
    doc["messages"] = [chosen[mid] for sequence in segments for mid in sequence]
    doc["attachments"] = list(attachments.values())
    added = [m for m in doc["messages"] if m["id"] not in current_by_id]
    upgraded = [m for m in doc["messages"] if m["id"] in current_by_id
                and m["text"] != current_by_id[m["id"]]["text"]]
    if any(m["role"] != "assistant" or has_reference(m["text"])
           or not has_reference(current_by_id[m["id"]]["text"]) for m in upgraded):
        raise ArchiveError("현재 원문을 바꾸는 복원은 허용하지 않습니다.")
    report = {
        "id": ident, "title": doc["title"], "changed": bool(added or upgraded),
        "added_questions": sum(m["role"] == "user" for m in added),
        "added_answer_bodies": sum(m["role"] == "assistant" and not has_reference(m["text"])
                                  and bool(m["text"].strip()) for m in added),
        "upgraded_answer_bodies": len(upgraded),
        "added_reference_placeholders": sum(m["role"] == "assistant" and has_reference(m["text"])
                                           for m in added),
        "questions": sum(m["role"] == "user" for m in doc["messages"]),
        "messages": len(doc["messages"]), "segments": len(segments),
        "unresolved_answers": sum(m["role"] == "assistant" and has_reference(m["text"])
                                  for m in doc["messages"]),
        "restored_message_ids": [m["id"] for m in added + upgraded],
        "remote_recollected": False, "text_complete": False,
    }
    if not report["changed"]:
        return doc, list(pages.values()), report
    doc["text_complete"] = False
    doc["warnings"] = sorted(set(doc["warnings"] + [HISTORY_WARNING] +
                                ([REFERENCE_WARNING] if report["unresolved_answers"] else [])))
    doc["history_reconstruction"] = {
        "repaired_at": now(), "full_remote_history_verified": False,
        "source_snapshot_digests": [r["digest"] for r in snapshots],
        "message_sources": provenance,
        "segments": [{"message_ids": seq, "connection_to_previous_verified": False}
                     for seq in segments],
    }
    return doc, list(pages.values()), report


def plan_repairs(db, thread_ids):
    if not thread_ids:
        raise ArchiveError("복원할 대화 ID를 명시해야 합니다.")
    plans = []
    for ident in dict.fromkeys(thread_ids):
        row = db.execute("""SELECT c.*,s.payload FROM conversations c JOIN snapshots s
            ON c.id=s.thread_id AND c.digest=s.digest WHERE c.id=?""", (ident,)).fetchone()
        if row is None:
            raise ArchiveError("보관된 대화가 없습니다: " + ident)
        versions = db.execute("""SELECT * FROM snapshots WHERE thread_id=?
            ORDER BY captured_at,digest""", (ident,)).fetchall()
        doc, pages, report = reconstruct(json.loads(row["payload"]), versions)
        plans.append((row, doc, pages, report))
    return plans


def preview_repairs(output, thread_ids):
    database = Path(output).expanduser().resolve() / "archive.sqlite3"
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        return {"applied": False, "repairs": [plan[3] for plan in plan_repairs(db, thread_ids)]}
