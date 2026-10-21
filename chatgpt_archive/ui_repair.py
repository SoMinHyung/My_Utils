"""사용자가 로그인한 화면에서 확인·복사한 원문을 ID로 대조해 수동 복원한다."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from contextlib import closing

from .archive import ArchiveError, atomic_write, dumps, now, safe_name, normalize
from .content_validation import REFERENCE, REFERENCE_WARNING, has_reference, unresolved_messages
from .quality import HISTORY_WARNING, issues, validate_ui_coverage
from .verification import latest_observations


UI_WARNING = (
    "로그인한 ChatGPT 화면에서 복사한 본문으로 보완했습니다. "
    "인용 링크·동적 도표·퀴즈 기능은 원래 화면과 다를 수 있으며, 전체 계정/모든 분기 백업은 아닙니다."
)


def plan_ui_repair(db, capture):
    ident = capture.get("conversation_id", "")
    if (capture.get("schema_version") != 1 or capture.get("source") != "chatgpt_authenticated_ui"
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", ident)
            or capture.get("url") != "https://chatgpt.com/c/" + ident):
        raise ArchiveError("화면 수집의 대화 출처가 올바르지 않습니다.")
    row = db.execute("""SELECT c.*,s.payload FROM conversations c JOIN snapshots s
        ON c.id=s.thread_id AND c.digest=s.digest WHERE c.id=?""", (ident,)).fetchone()
    observation = latest_observations(db).get(ident)
    if row is None:
        if not observation:
            raise ArchiveError("기존 백업/조회 관측과 대조할 수 없는 대화입니다.")
        raw = json.loads(observation["payload"])
        if not raw.get("pages") or any(
                p.get("thread", {}).get("id") != ident or p["thread"].get("kind") != "chatgpt"
                for p in raw["pages"]):
            raise ArchiveError("도구 관측과 화면의 대화 ID/종류가 일치하지 않습니다.")
        try:
            seed = normalize(raw, keep_unresolved=True)
        except ArchiveError:
            # 첫 조회부터 20,000자 잘림/비텍스트 응답이면 정상 스냅샷이 없을 수 있다.
            # 잘린 본문을 가져다 쓰지 않고 메타데이터만 가져와 UI 원문으로 새로 채운다.
            thread = raw["pages"][0]["thread"]
            seed = {"id": ident, "title": thread.get("title") or "제목 없음",
                    "created_at": thread.get("createdAt"), "updated_at": thread.get("updatedAt"),
                    "messages": [], "attachments": [
                        a for p in raw["pages"] for a in p.get("attachments", [])],
                    "warnings": [HISTORY_WARNING], "text_complete": False, "source_incomplete": True}
        seed["attachments"] = [{"name": a.get("name") or "첨부 파일", "available": False}
                               for a in seed["attachments"]]
        payload = {"document": seed, "source_pages": raw["pages"]}
        row = {"listed_updated_at": raw.get("listed_updated_at"),
               "last_error": None, "last_checked_at": None}
    else:
        payload = json.loads(row["payload"])
    doc = deepcopy(payload["document"])
    update_bodies = capture.get("update_observed_bodies") is True
    if update_bodies and (
            "observation_digest" not in capture or
            capture["observation_digest"] != (observation or {}).get("digest")):
        raise ArchiveError("부분 본문 갱신은 최신 도구 관측 해시와 일치해야 합니다.")
    old = {m["id"]: m for m in doc["messages"]}
    observed = {}
    for message in capture.get("messages", []):
        mid = message.get("id")
        if (not isinstance(mid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", mid)
                or mid in observed or message.get("role") not in ("user", "assistant")
                or not isinstance(message.get("text"), str)
                or message.get("source") not in ("chatgpt_ui_copy", "chatgpt_ui_visible_text")):
            raise ArchiveError("화면에서 수집한 메시지 형식 또는 ID가 올바르지 않습니다.")
        if message.get("url", capture["url"]) != capture["url"]:
            raise ArchiveError("다른 대화의 메시지가 섞였습니다.")
        if message["role"] == "assistant" and has_reference(message["text"]):
            raise ArchiveError("화면 복사 결과에도 미해결 참조가 있습니다.")
        observed[mid] = message
        if update_bodies and (message.get("source") != "chatgpt_ui_copy" or
                              message.get("body_complete") is not True):
            raise ArchiveError("부분 본문 갱신에는 동일 ID의 전체 복사 증거가 필요합니다.")
    if not observed:
        raise ArchiveError("화면에서 수집한 메시지가 없습니다.")

    merged = deepcopy(old)
    added = []
    restored = []
    replaced = []
    for mid, message in observed.items():
        if mid in old:
            previous = old[mid]
            if previous["role"] != message["role"]:
                raise ArchiveError("메시지의 역할이 기존 백업과 다릅니다: " + mid)
            if message["role"] == "user":
                # 앱 도구가 붙인 이미지 안내만 제외하고 질문 본문을 대조한다.
                previous_text = re.sub(r"\n*\[User attached .*?image contents were not included\]",
                                       "", previous["text"]).strip()
                if (previous_text != message["text"].strip()
                        and not capture.get("coverage") and not update_bodies):
                    raise ArchiveError("질문 원문이 기존 백업과 다릅니다: " + mid)
            elif has_reference(previous["text"]):
                source_ids = [source_id for block in REFERENCE.findall(previous["text"])
                              for source_id in re.findall(r'\bsource_message_id="([^"]+)"', block)]
                # 화면의 바깥 메시지 ID로 전체 원문을 대조하므로 index-only/혼합 참조도
                # 복원 가능하다. 다른 원문 ID를 명시한 참조는 임의 치환하지 않는다.
                if any(source_id != mid for source_id in source_ids) or not message["text"].strip():
                    raise ArchiveError("실제 답변과 참조 ID를 연결할 수 없습니다: " + mid)
                merged[mid]["text"] = message["text"]
                restored.append(mid)
            # 전체 UI 검증 입력은 부분 잘림/펼치기 누락도 고친다. 아래 검증 실패 시 저장하지 않는다.
            if (capture.get("coverage") or update_bodies) and merged[mid]["text"] != message["text"]:
                merged[mid]["text"] = message["text"]
                replaced.append(mid)
        else:
            merged[mid] = {"id": mid, "role": message["role"], "text": message["text"],
                           "time": None, "turn_id": None}
            added.append(mid)

    media_files = []
    for item in capture.get("media", []):
        mid, question_id = item["message_id"], item["question_id"]
        if question_id not in merged or merged[question_id]["role"] != "user":
            raise ArchiveError("이미지의 질문 ID를 확인할 수 없습니다.")
        if mid not in merged:
            if item["role"] != "assistant" or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", mid):
                raise ArchiveError("질문을 이미지 메타데이터만으로 만들 수 없습니다.")
            merged[mid] = {"id": mid, "role": "assistant", "text": "",
                           "time": merged[question_id]["time"], "turn_id": question_id}
            added.append(mid)
        if merged[mid]["role"] != item["role"]:
            raise ArchiveError("이미지 역할이 일치하지 않습니다.")
        path = Path(item["download_path"]).expanduser()
        if not path.is_absolute() or not path.is_file():
            raise ArchiveError("화면에서 내려받은 이미지 파일이 없습니다.")
        path = path.resolve()
        blob = path.read_bytes()
        image_type = ("png" if blob.startswith(b"\x89PNG\r\n\x1a\n") else
                      "jpeg" if blob.startswith(b"\xff\xd8\xff") else None)
        if image_type is None:
            raise ArchiveError("지원하는 PNG/JPEG 이미지가 아닙니다.")
        extension = Path(item["name"]).suffix.lower()
        if extension not in ((".png",) if image_type == "png" else (".jpg", ".jpeg")):
            raise ArchiveError("이미지의 확장자와 실제 형식이 일치하지 않습니다.")
        digest = hashlib.sha256(blob).hexdigest()
        relative = "attachments/" + ident + "/" + digest[:20] + "_" + safe_name(item["name"])
        attachment = {"name": item["name"], "available": True, "path": relative, "sha256": digest,
                      "message_id": mid, "source": "chatgpt_ui_download"}
        if attachment not in doc["attachments"]:
            doc["attachments"].append(attachment)
        if relative not in merged[mid].get("media", []):
            merged[mid].setdefault("media", []).append(relative)
        media_files.append((relative, blob))

    # 이전 관측 순서와 화면에서 관측한 순서를 함께 만족해야 한다.
    from .history_repair import _sequences
    sequences = _sequences(doc) + capture.get("windows", []) + capture.get("media_windows", [])
    edges = {mid: set() for mid in merged}
    incoming = {mid: 0 for mid in merged}
    for sequence in sequences:
        if len(set(sequence)) != len(sequence) or any(mid not in merged for mid in sequence):
            raise ArchiveError("화면 수집 구간에 잘못된 메시지 ID가 있습니다.")
        for left, right in zip(sequence, sequence[1:]):
            if right not in edges[left]:
                edges[left].add(right)
                incoming[right] += 1
    ready = [mid for mid, count in incoming.items() if count == 0]
    ordered = []
    while ready:
        if len(ready) != 1:
            raise ArchiveError("화면과 저장본의 순서를 유일하게 연결할 수 없습니다.")
        mid = ready.pop()
        ordered.append(mid)
        for after in edges[mid]:
            incoming[after] -= 1
            if not incoming[after]:
                ready.append(after)
    if len(ordered) != len(merged):
        raise ArchiveError("화면과 저장본의 대화 순서가 충돌합니다.")
    question = None
    for mid in ordered:
        message = merged[mid]
        if message["role"] == "user":
            question = message
            if message["turn_id"] is None:
                message["turn_id"] = mid
        elif message["turn_id"] is None:
            if question is None:
                raise ArchiveError("질문과 연결되지 않은 새 답변입니다.")
            message["turn_id"], message["time"] = question["id"], question["time"]
    doc["messages"] = [merged[mid] for mid in ordered]
    unanswered = [message["id"] for index, message in enumerate(doc["messages"])
                  if message["role"] == "user"
                  and (index == len(ordered) - 1 or doc["messages"][index + 1]["role"] == "user")]
    doc["questions_without_observed_answer"] = unanswered
    doc["text_complete"] = False
    unsupported = [] if capture.get("coverage") else doc.get("ui_unsupported_content", [])
    doc["ui_unsupported_content"] = list(unsupported)
    for item in capture.get("unsupported_content", []):
        if item not in doc["ui_unsupported_content"]:
            doc["ui_unsupported_content"].append(item)
    coverage = validate_ui_coverage(capture, doc["messages"])
    if coverage:
        if capture.get("observation_digest") != (observation or {}).get("digest"):
            raise ArchiveError("화면 수집 중 도구 관측이 갱신됐습니다. 최신 관측과 다시 대조해야 합니다.")
        if observation:
            raw = json.loads(observation["payload"])
            latest_ids = {item["id"] for page in raw["pages"] for turn in page.get("turns", [])
                          for item in turn.get("items", [])
                          if item.get("type") in ("userMessage", "agentMessage")}
            if not latest_ids.issubset(observed.keys() | media_ids_from(capture)):
                raise ArchiveError("최신 도구 관측의 메시지가 화면 수집에서 빠졌습니다.")
            coverage["observation_digest"] = observation["digest"]
            doc["updated_at"] = raw["pages"][0]["thread"].get("updatedAt")
            doc["title"] = raw["pages"][0]["thread"].get("title") or doc["title"]
            row = dict(row, listed_updated_at=raw.get("listed_updated_at"))
        doc["coverage"] = coverage
        doc["source_incomplete"] = False
        doc["ui_reviewed_at"] = capture.get("captured_at") or now()
        doc["warnings"] = [warning for warning in doc["warnings"] if warning != HISTORY_WARNING]
        doc["text_complete"] = not issues(doc)
    if not unresolved_messages(doc):
        doc["warnings"] = [warning for warning in doc["warnings"] if warning != REFERENCE_WARNING]
    doc["warnings"] = sorted(set(doc["warnings"] + [UI_WARNING]))
    if "history_reconstruction" in doc:
        doc["history_reconstruction"]["segments"] = [{"message_ids": ordered,
                                                       "connection_to_previous_verified": False}]
    audit = {"source": capture["source"], "captured_at": max(
        (m.get("captured_at", "") for m in observed.values()), default=""),
        "capture_sha256": hashlib.sha256(dumps(capture).encode("utf-8")).hexdigest(),
        "message_ids": sorted(observed), "restored_answer_ids": restored, "added_message_ids": added,
        "replaced_body_ids": replaced}
    evidence = doc.setdefault("ui_body_evidence", {})
    for mid, message in observed.items():
        if message.get("source") == "chatgpt_ui_copy" and message.get("body_complete") is True:
            evidence[mid] = {
                "sha256": hashlib.sha256(message["text"].encode("utf-8")).hexdigest(),
                "observation_digest": capture.get("observation_digest"),
                "captured_at": message.get("captured_at", capture.get("captured_at")),
            }
    if capture.get("windows"):
        doc["ui_last_observed_window"] = capture["windows"][-1]
    if not any(a.get("capture_sha256") == audit["capture_sha256"]
               for a in doc.get("ui_recollections", [])):
        doc.setdefault("ui_recollections", []).append(audit)
    if capture.get("captured_at"):
        from .retry_policy import parse_stamp
        if parse_stamp(capture["captured_at"]) is None:
            raise ArchiveError("화면 수집 시각이 올바르지 않습니다.")
        doc["ui_reviewed_at"] = capture["captured_at"]
        doc["ui_review_outcome"] = ("complete" if coverage else
                                    capture.get("review_outcome", "partial"))
        if capture.get("observation_digest") == (observation or {}).get("digest"):
            doc["ui_observation_digest"] = capture.get("observation_digest")
    report = {"id": ident, "title": doc["title"], "restored_answers": len(restored),
              "added_questions": sum(merged[mid]["role"] == "user" for mid in added),
              "added_answer_bodies": sum(merged[mid]["role"] == "assistant" and bool(merged[mid]["text"])
                                        for mid in added),
              "replaced_body_ids": replaced,
              "images": len(media_files), "questions": sum(m["role"] == "user" for m in doc["messages"]),
              "unresolved_answers": len(unresolved_messages(doc)), "text_complete": doc["text_complete"],
              "verified_current_branch": bool(coverage),
              "questions_without_answer": len(unanswered),
              "questions_without_answer_ids": unanswered,
              "messages": len(doc["messages"]), "changed": doc != payload["document"]}
    return row, payload, doc, media_files, report


def media_ids_from(capture):
    return {item["message_id"] for item in capture.get("media", [])}


def preview_ui_repair(output, capture):
    database = Path(output).expanduser().resolve() / "archive.sqlite3"
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        return {"applied": False, **plan_ui_repair(db, capture)[-1]}


def apply_ui_repair(archive, capture):
    row, payload, doc, media_files, report = plan_ui_repair(archive.db, capture)
    if not report["changed"]:
        return {"applied": True, **report}
    for relative, blob in media_files:
        target = archive.output / relative
        if target.exists() and target.read_bytes() != blob:
            raise ArchiveError("기존 이미지 파일과 내용이 충돌합니다.")
        atomic_write(target, blob)
    archive._save_document(doc, {"pages": payload["source_pages"], "source_browser": capture,
                                "listed_updated_at": row["listed_updated_at"]},
                           last_error=None if doc["text_complete"] else row["last_error"],
                           last_checked_at=doc.get("ui_reviewed_at") or row["last_checked_at"])
    archive.write_index()
    result = {"applied": True, "finished_at": now(), **report}
    atomic_write(archive.output / "repairs" / ("ui-" + doc["ui_recollections"][-1]["capture_sha256"] + ".json"),
                 json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result
