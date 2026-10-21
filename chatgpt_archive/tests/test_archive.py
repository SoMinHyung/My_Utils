import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from chatgpt_archive.archive import (
    Archive, ArchiveError, UnresolvedReferenceError, date_text, normalize, read_archive,
)
from chatgpt_archive.retry_policy import RATE_LIMIT_POLICY_VERSION, RetryHistory


def turn(number, answer="답변입니다."):
    return {"id": "turn-" + str(number), "status": "completed", "startedAt": 1789387100 + number,
            "items": [{"id": "q-" + str(number), "type": "userMessage",
                       "content": [{"type": "text", "text": "질문 " + str(number)}]},
                      {"id": "a-" + str(number), "type": "agentMessage", "text": answer}]}


def capture(turns=None):
    return {"id": "test-chat", "listed_updated_at": 1789387200,
            "collected_at": "2026-09-15T00:00:00Z", "char_limit": 20000,
            "pages": [{"thread": {"id": "test-chat", "kind": "chatgpt", "title": "한글 / 파일: 테스트",
                                  "createdAt": 1789387067, "updatedAt": 1789387200},
                       "page": {"order": "newest_first", "hasMore": False, "nextCursor": None},
                       "attachments": [], "turns": turns if turns is not None else [turn(1)]}]}


def bundle(cap=None):
    return {"schema_version": 1, "conversations": [capture() if cap is None else cap],
            "discovered": 1, "errors": [], "warnings": []}


def reference(ident="a-1"):
    return '::chatgpt-content-reference{index="0" source_message_id="%s"}' % ident


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name) / "output"
        self.archive = Archive(self.output).__enter__()

    def tearDown(self):
        self.archive.__exit__()
        self.temp.cleanup()

    def test_read_only_queries_do_not_create_or_modify_archive_files(self):
        missing = self.output / "missing"
        self.assertEqual(read_archive(missing, "state"), {"conversations": []})
        self.assertEqual(read_archive(missing, "status")["total"], 0)
        self.assertFalse(missing.exists())
        report = self.archive.ingest(bundle())
        def file_state():
            return {str(p): (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode)
                    for p in self.output.rglob("*") if p.is_file()}
        before = file_state()
        self.assertEqual(read_archive(self.output, "state"), self.archive.state())
        self.assertEqual(read_archive(self.output, "status"), report)
        self.assertEqual(file_state(), before)

    def test_pagination_restores_order_and_removes_overlap(self):
        cap = capture([turn(3), turn(2)])
        older = copy.deepcopy(cap["pages"][0])
        older["turns"] = [turn(2), turn(1)]
        cap["pages"][0]["page"].update(hasMore=True, nextCursor="older")
        cap["pages"].append(older)
        doc = normalize(cap)
        self.assertEqual([m["id"] for m in doc["messages"]],
                         ["q-1", "a-1", "q-2", "a-2", "q-3", "a-3"])

    def test_unfinished_pagination_and_conflicting_overlap_rejected(self):
        cap = capture()
        cap["pages"][0]["page"].update(hasMore=True, nextCursor="older")
        with self.assertRaises(ArchiveError):
            normalize(cap)
        older = copy.deepcopy(cap["pages"][0])
        older["page"]["hasMore"] = False
        older["turns"][0]["items"][1]["text"] = "수정됨"
        cap["pages"].append(older)
        with self.assertRaises(ArchiveError):
            normalize(cap)

    def test_exact_body_survives_markdown_txt_sqlite_and_export_repair(self):
        original = "## 표와 코드\r\n\r\n|값|설명|\r\n|---|---|\r\n|한글|🍀|\n\n```python\nprint('한글')\n```\n$(touch /tmp/do-not-run)\n`date`"
        result = self.archive.ingest(bundle(capture([turn(1, original)])))
        self.assertEqual(result["changed"], 1)
        self.assertEqual(result["errors"], [])
        paths = list((self.output / "conversations").iterdir())
        self.assertEqual(len(paths), 2)
        for path in paths:
            self.assertIn(original, path.read_bytes().decode('utf-8'))
            self.assertNotIn(":", path.name)
            path.unlink()
        self.archive.export_all()
        self.assertEqual(len(list((self.output / "conversations").iterdir())), 2)
        payload = json.loads(self.archive.db.execute("SELECT payload FROM snapshots").fetchone()[0])
        self.assertEqual(payload["document"]["messages"][1]["text"], original)

    def test_repeat_dedup_and_previous_versions_survive_edits_and_missing_chats(self):
        cap = capture()
        self.archive.ingest(bundle(cap))
        cap["collected_at"] = "2026-09-15T01:00:00Z"
        result = self.archive.ingest(bundle(cap))
        self.assertEqual(result["changed"], 0)
        self.assertEqual(self.archive.db.execute("SELECT count(*) FROM snapshots").fetchone()[0], 1)
        cap["pages"][0]["turns"][0]["items"][1]["text"] = "수정된 답변"
        self.archive.ingest(bundle(cap))
        self.assertEqual(self.archive.db.execute("SELECT count(*) FROM snapshots").fetchone()[0], 2)
        self.assertEqual(len(list((self.output / "history" / "test-chat").glob("*.md"))), 2)
        result = self.archive.ingest({"schema_version": 1, "conversations": [],
                                     "errors": [{"id": "test-chat", "message": "대화를 찾을 수 없음"}]})
        self.assertEqual(result["total"], 1)
        self.assertIn("수정된 답변", next((self.output / "conversations").glob("*.md")).read_text())
        self.assertIn("재확인 필요", (self.output / "INDEX.md").read_text())

    def test_index_uses_update_time_instead_of_check_time(self):
        captures = []
        for ident, updated in (("older", 1789387200), ("newer", "2026-09-15T00:00:00Z"),
                               ("unknown", None), ("invalid", "not-a-date")):
            cap = capture()
            cap["id"] = ident
            cap["pages"][0]["thread"].update(id=ident, title=ident, updatedAt=updated)
            self.archive.save(cap)
            captures.append(cap)

        self.archive.write_index()
        index = (self.output / "INDEX.md").read_text()
        rows = [line for line in index.splitlines() if line.startswith("| [")]
        self.assertIn("| 마지막 업데이트 |", index)
        self.assertNotIn("마지막 확인", index)
        self.assertEqual([line.split("]", 1)[0] for line in rows],
                         ["| [newer", "| [older", "| [invalid", "| [unknown"])
        self.assertTrue(rows[0].endswith(date_text("2026-09-15T00:00:00Z") + " |"))
        self.assertTrue(rows[1].endswith(date_text(1789387200) + " |"))
        self.assertTrue(rows[-1].endswith("알 수 없음 |"))

        older = captures[0]
        older["collected_at"] = "2026-09-21T00:00:00Z"
        self.assertFalse(self.archive.save(older)["changed"])
        self.archive.write_index()
        self.assertEqual((self.output / "INDEX.md").read_text(), index)

        older["pages"][0]["thread"]["updatedAt"] = "2026-09-20T00:00:00Z"
        older["pages"][0]["turns"][0]["items"][1]["text"] = "수정된 답변"
        self.archive.save(older)
        self.archive.write_index()
        rows = [line for line in (self.output / "INDEX.md").read_text().splitlines()
                if line.startswith("| [")]
        self.assertTrue(rows[0].startswith("| [older]"))
        self.assertTrue(rows[0].endswith(date_text("2026-09-20T00:00:00Z") + " |"))

    def test_truncated_update_does_not_replace_last_good_snapshot(self):
        self.archive.ingest(bundle())
        bad = capture([turn(1, "x" * 20000)])
        result = self.archive.ingest(bundle(bad))
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(self.archive.db.execute("SELECT count(*) FROM snapshots").fetchone()[0], 1)
        self.assertIn("답변입니다.", next((self.output / "conversations").glob("*.md")).read_text())
        for text in ("😀" * 10000, "일부 내용 [truncated]"):
            with self.assertRaises(ArchiveError):
                normalize(capture([turn(1, text)]))

    def test_references_are_not_answers_even_with_surrounding_text(self):
        for text in (reference(), "일부 본문\n" + reference(), reference() + "\n" + reference("a-2")):
            with self.assertRaises(UnresolvedReferenceError) as caught:
                normalize(capture([turn(1, text)]))
            self.assertEqual(caught.exception.message_ids, ["a-1"])
        # 질문에 인용된 참조 표시는 답변 누락으로 오인하지 않는다.
        cap = capture()
        cap["pages"][0]["turns"][0]["items"][0]["content"][0]["text"] = reference()
        self.assertFalse(normalize(cap)["text_complete"])  # 참조 인용과 별개로 이력 경계는 미검증

    def test_reference_failure_preserves_good_backup_without_counting_transport_failure(self):
        self.archive.ingest(bundle())
        before = {p: p.read_bytes() for p in (self.output / "conversations").iterdir()}
        failed = bundle(capture([turn(2, reference("a-2"))]))
        first = self.archive.ingest(failed)
        self.assertEqual(first["saved"], [])
        self.assertEqual(first["errors"][0]["code"], "unresolved_content_reference")
        self.assertEqual(first["errors"][0]["stage"], "validation")
        self.assertEqual(first["errors"][0]["message_ids"], ["a-2"])
        self.assertEqual(first["run_status"], "partial_failure")
        self.assertEqual(first["recovered_ids"], [])
        self.assertEqual(self.archive.state()["retry_state"], [])
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertEqual(self.archive.ingest(failed)["new_errors"], [])
        recovered = self.archive.ingest(bundle(capture([turn(2, "실제로 조회된 본문"), turn(1)])))
        self.assertEqual(recovered["recovered_ids"], [])  # 읽기 재개 ≠ 전체 이력 복구
        self.assertTrue(recovered["verification_required"])
        self.assertEqual(recovered["errors"], [])

    def test_false_end_of_history_cannot_replace_a_longer_known_conversation(self):
        self.archive.ingest(bundle(capture([turn(2), turn(1)])))
        before = {p: p.read_bytes() for p in (self.output / "conversations").iterdir()}
        result = self.archive.ingest(bundle(capture([turn(2)])))
        self.assertEqual(result["errors"][0]["code"], "incomplete_history")
        self.assertEqual(result["errors"][0]["message_ids"], ["a-1", "q-1"])
        self.assertEqual(result["changed"], 0)
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)

    def test_same_message_reference_uses_exact_previous_body_and_keeps_raw_source(self):
        original = "원문\n\n|표|값|\n|---|---|\n|A|1|\n"
        self.archive.ingest(bundle(capture([turn(1, original)])))
        report = self.archive.ingest(bundle(capture([turn(1, reference())])))
        self.assertEqual(report["errors"], [])
        self.assertEqual(len(report["saved"][0]["reference_recoveries"]), 1)
        row = self.archive.db.execute("""SELECT s.payload FROM conversations c
            JOIN snapshots s ON s.thread_id=c.id AND s.digest=c.digest""").fetchone()
        payload = json.loads(row[0])
        self.assertEqual(payload["document"]["messages"][1]["text"], original)
        self.assertEqual(payload["source_pages"][0]["turns"][0]["items"][1]["text"], reference())
        self.assertIn(original, next((self.output / "conversations").glob("*.txt")).read_text())

    def test_ambiguous_or_different_message_history_is_not_used_as_replacement(self):
        self.archive.ingest(bundle(capture([turn(1, "첫 본문")])))
        # 서로 다른 ID의 본문/다른 대화 본문은 가져다 붙이지 않는다.
        self.assertTrue(self.archive.ingest(bundle(capture([turn(2, reference())])))["errors"])
        self.archive.ingest(bundle(capture([turn(1, "변경된 본문")])))
        self.assertTrue(self.archive.ingest(bundle(capture([turn(1, reference())])))["errors"])

    def test_legacy_repair_restores_only_known_message_and_preserves_history(self):
        self.archive.ingest(bundle(capture([turn(1, "보존할 원문")])))
        cap = capture([turn(2), turn(1)])
        legacy = normalize(cap)
        for message in legacy["messages"]:
            if message["role"] == "assistant":
                message["text"] = reference(message["id"])
        # 예전 수집기가 이미 정상 저장한 파일을 재현한다.
        legacy["text_complete"] = True
        self.archive._save_document(legacy, cap)
        historical = {p: p.read_bytes() for p in (self.output / "history").rglob("*") if p.is_file()}
        raw_flag = self.archive.db.execute("SELECT text_complete FROM conversations").fetchone()[0]
        self.assertEqual(raw_flag, 1)
        self.assertFalse(read_archive(self.output, "state")["conversations"][0]["text_complete"])
        before_runs = self.archive.db.execute("SELECT count(*) FROM runs").fetchone()[0]
        result = self.archive.repair_references()
        self.assertEqual(result["restored_messages"], 1)
        self.assertEqual(result["repaired"][0]["unresolved_message_ids"], ["a-2"])
        for path, data in historical.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertEqual(self.archive.db.execute("SELECT count(*) FROM runs").fetchone()[0], before_runs)
        self.assertIn("답변 본문 누락", (self.output / "INDEX.md").read_text())
        saved = next((self.output / "conversations").glob("*.md")).read_text()
        self.assertIn("보존할 원문", saved)
        self.assertIn("답변 본문 미수집", saved)
        self.assertEqual(self.archive.repair_references()["restored_messages"], 0)
        self.assertFalse(self.archive.repair_references()["repaired"][0]["changed"])

    def test_attachments_copied_only_from_app_preview_and_missing_is_visible(self):
        with tempfile.TemporaryDirectory(prefix="codex-file-preview-") as temp:
            source = Path(temp) / "이미지.png"
            source.write_bytes(b"example-image-bytes")
            cap = capture()
            cap["pages"][0]["attachments"] = [{"name": "이미지.png", "path": str(source)},
                                             {"name": "외부 파일", "path": "/etc/hosts"}]
            result = self.archive.ingest(bundle(cap))
            self.assertEqual(result["errors"], [])
            self.assertFalse(result["saved"][0]["text_complete"])
            copied = list((self.output / "attachments").rglob("*.png"))
            self.assertEqual(len(copied), 1)
            self.assertEqual(copied[0].read_bytes(), source.read_bytes())
            self.assertIn("원본 미보관", next((self.output / "conversations").glob("*.md")).read_text())

    def test_roleless_or_incomplete_data_does_not_claim_finished(self):
        cap = capture()
        cap["pages"][0]["turns"][0]["status"] = "inProgress"
        self.assertFalse(normalize(cap)["text_complete"])
        cap = capture()
        cap["pages"][0]["turns"][0]["items"] = cap["pages"][0]["turns"][0]["items"][1:]
        self.assertTrue(normalize(cap)["warnings"])

    def test_duplicate_error_notification_suppressed_and_recovery_detected(self):
        self.archive.ingest(bundle())
        failed = {"schema_version": 1, "conversations": [], "errors": [{"id": "test-chat", "message": "연결 실패"}]}
        self.assertEqual(len(self.archive.ingest(failed)["new_errors"]), 1)
        self.assertEqual(self.archive.ingest(failed)["new_errors"], [])
        resumed = self.archive.ingest(bundle())
        self.assertFalse(resumed["recovered"])
        self.assertEqual(resumed["run_status"], "verification_required")

    def test_legacy_history_excludes_at_100_and_preserves_backups_until_explicit_success(self):
        self.archive.ingest(bundle())
        originals = {p: p.read_bytes() for directory in ("conversations", "history")
                     for p in (self.output / directory).rglob("*") if p.is_file()}
        error = {"id": "test-chat", "message": "No Codex thread found"}
        for i in range(99):
            old = {"finished_at": "2026-10-01T00:00:00Z", "saved": [], "errors": [error]}
            self.archive.db.execute("INSERT INTO runs(finished_at,report) VALUES (?,?)",
                                    (old["finished_at"], json.dumps(old)))
        self.archive.db.commit()
        retry = read_archive(self.output, "state")["retry_state"][0]
        self.assertEqual(retry["consecutive_failures"], 99)
        self.assertFalse(retry["excluded"])
        report = self.archive.ingest({"schema_version": 1, "conversations": [], "errors": [error]})
        self.assertEqual(report["new_exclusions"][0]["consecutive_failures"], 100)
        self.assertEqual(report["new_errors"], [])
        self.assertIn("자동 수집 제외", (self.output / "INDEX.md").read_text())
        for path, original in originals.items():
            self.assertEqual(path.read_bytes(), original)
        report = self.archive.ingest({"schema_version": 1, "conversations": [], "errors": []})
        self.assertFalse(report["recovered"])
        self.assertEqual(report["new_exclusions"], [])
        self.assertEqual(len(report["excluded"]), 1)
        report = self.archive.ingest(bundle())
        self.assertFalse(report["recovered"])
        self.assertTrue(report["verification_required"])
        self.assertEqual(report["excluded"], [])
        self.assertEqual(read_archive(self.output, "state")["retry_state"], [])

    def test_alternating_errors_and_skipped_runs_do_not_repeat_notifications(self):
        self.archive.ingest(bundle())
        host = {"id": "test-chat", "message": "Unavailable or failed hosts: durable"}
        rate = {"id": "test-chat", "message": "Too many requests"}
        def failed(error):
            return self.archive.ingest({"schema_version": 1, "conversations": [], "errors": [error]})
        self.assertEqual(len(failed(host)["new_errors"]), 1)
        self.assertEqual(len(failed(rate)["new_errors"]), 1)
        self.archive.ingest({"schema_version": 1, "conversations": [], "errors": [],
                             "deferred": [{"id": "test-chat", "reason": "retry_backoff"}]})
        self.assertEqual(failed(host)["new_errors"], [])
        self.assertEqual(failed(rate)["new_errors"], [])
        state = self.archive.state()
        self.assertEqual(state["retry_state"][0]["consecutive_failures"], 2)
        self.assertEqual(state["rate_limit_streak"], 2)
        self.assertEqual(state["rate_limit_skip_runs_remaining"], 1)

    def test_failure_policy_tracks_unsaved_ids_but_ignores_rate_limits_and_unattempted_work(self):
        history = RetryHistory()
        stamp = datetime(2026, 10, 1, tzinfo=timezone.utc)
        for i in range(110):
            history.apply({"finished_at": stamp.isoformat(), "errors": [
                {"id": "rate", "message": "Too many requests"},
                {"id": "unattempted", "stage": "collection", "code": "timeout"},
                {"id": "storage", "stage": "save", "message": "failed"},
                {"id": "throttled", "attempted": False, "message": "timeout"}]})
        self.assertEqual(history.threads, {})
        # 구 정책의 호출 제한은 새 예약 건너뛰기 횟수로 가져오지 않는다.
        self.assertEqual(history.rate_limit_streak, 0)
        self.assertEqual(history.rate_limit_skip_runs_remaining, 0)
        for i in range(1, 9):
            history.apply({"finished_at": stamp.isoformat(), "errors": [
                {"id": "never-saved", "message": "No Codex thread found"}]})
            retry = history.threads["never-saved"]
            self.assertEqual(retry["consecutive_failures"], i)
            self.assertEqual(retry["next_retry_at"],
                             (stamp + timedelta(minutes=min(1440, 20 * 2 ** (i - 1)))).isoformat())

    def test_first_new_report_announces_legacy_exclusions_once_without_counting_again(self):
        error = {"id": "never-saved", "message": "No Codex thread found"}
        for i in range(100):
            old = {"finished_at": "2026-10-01T00:00:00Z", "errors": [error]}
            self.archive.db.execute("INSERT INTO runs(finished_at,report) VALUES (?,?)",
                                    (old["finished_at"], json.dumps(old)))
        self.archive.db.commit()
        report = self.archive.ingest({"schema_version": 1, "conversations": [], "errors": []})
        self.assertEqual(report["new_exclusions"][0]["consecutive_failures"], 100)
        self.assertFalse(report["recovered"])

    def test_rate_limit_backoff_counts_runs_once_and_caps_at_seven_skips(self):
        history = RetryHistory()
        limited = {"rate_limit_policy_version": RATE_LIMIT_POLICY_VERSION,
                   "errors": [{"id": "a", "message": "Too many requests"},
                              {"id": "b", "message": "Too many requests"}]}
        skipped = {"rate_limit_policy_version": RATE_LIMIT_POLICY_VERSION,
                   "rate_limit_backoff_skipped": True, "errors": []}
        for streak, skip_count in enumerate([0, 1, 3, 7, 7], 1):
            history.apply(limited)
            self.assertEqual(history.rate_limit_streak, streak)
            self.assertEqual(history.rate_limit_skip_runs_remaining, skip_count)
            for remaining in range(skip_count - 1, -1, -1):
                history.apply(skipped)
                self.assertEqual(history.rate_limit_skip_runs_remaining, remaining)
                self.assertEqual(history.rate_limit_streak, streak)
        self.assertEqual(history.threads, {})

    def test_rate_limit_state_persists_and_resets_after_a_real_healthy_check(self):
        def ingest(**data):
            return self.archive.ingest({"schema_version": 1, "conversations": [], "errors": [], **data})
        limited = {"errors": [{"id": None, "stage": "list_threads", "message": "Too many requests"}]}
        first = ingest(**limited)
        self.assertEqual(first["rate_limit_streak"], 1)
        self.assertEqual(first["rate_limit_skip_runs_remaining"], 0)
        second = ingest(**limited)
        self.assertEqual(second["rate_limit_skip_runs_remaining"], 1)
        self.assertEqual(read_archive(self.output, "state")["rate_limit_skip_runs_remaining"], 1)
        # 조회하지 않은 실행은 정상 복구가 아니며, 명시적인 건너뛰기만 횟수를 소비한다.
        idle = ingest()
        self.assertEqual(idle["rate_limit_skip_runs_remaining"], 1)
        skipped = ingest(rate_limit_backoff_skipped=True, run_status="deferred")
        self.assertEqual(skipped["rate_limit_streak"], 2)
        self.assertEqual(skipped["rate_limit_skip_runs_remaining"], 0)
        other_failure = ingest(remote_checked=True, errors=[
            {"id": "missing", "stage": "read_thread", "message": "No Codex thread found"}])
        self.assertEqual(other_failure["rate_limit_streak"], 2)
        third = ingest(**limited)
        self.assertEqual(third["rate_limit_skip_runs_remaining"], 3)
        for _ in range(3):
            ingest(rate_limit_backoff_skipped=True, run_status="deferred")
        healthy = ingest(remote_checked=True, skipped=14)
        self.assertEqual(healthy["rate_limit_streak"], 0)
        self.assertEqual(healthy["rate_limit_skip_runs_remaining"], 0)
        self.assertEqual(read_archive(self.output, "state")["rate_limit_streak"], 0)
        new_limit = ingest(**limited)
        self.assertEqual(new_limit["rate_limit_streak"], 1)
        self.assertEqual(new_limit["rate_limit_skip_runs_remaining"], 0)
        self.assertEqual(len(new_limit["new_errors"]), 1)

    def test_old_cooldown_is_removed_without_losing_thread_failure_history(self):
        old = {"finished_at": "2026-10-01T00:00:00Z", "errors": [
            {"id": "limited", "message": "Too many requests"},
            {"id": "missing", "message": "No Codex thread found"}],
            "next_collection_at": "2099-01-01T00:00:00Z"}
        self.archive.db.execute("INSERT INTO runs(finished_at,report) VALUES (?,?)",
                                (old["finished_at"], json.dumps(old)))
        self.archive.db.commit()
        state = read_archive(self.output, "state")
        self.assertEqual(state["rate_limit_skip_runs_remaining"], 0)
        self.assertEqual(state["rate_limit_streak"], 0)
        self.assertNotIn("next_collection_at", state)
        self.assertEqual(state["retry_state"][0]["id"], "missing")
        self.assertEqual(state["retry_state"][0]["consecutive_failures"], 1)


if __name__ == "__main__":
    unittest.main()
