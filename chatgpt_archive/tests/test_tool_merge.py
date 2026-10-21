import json
import unittest

import test_archive as fixtures
from chatgpt_archive.ui_repair import apply_ui_repair
from chatgpt_archive.verification import latest_observations
from test_archive import capture, bundle, turn, reference


class ToolMergeTests(unittest.TestCase):
    setUp = fixtures.ArchiveTests.setUp
    tearDown = fixtures.ArchiveTests.tearDown

    def payload(self):
        return json.loads(self.archive.db.execute("""SELECT s.payload FROM conversations c
            JOIN snapshots s ON c.id=s.thread_id AND c.digest=s.digest""").fetchone()[0])

    def seed_restored(self):
        self.archive.ingest(bundle(capture([turn(2), turn(1)])))
        ui = {"schema_version": 1, "conversation_id": "test-chat",
              "url": "https://chatgpt.com/c/test-chat", "source": "chatgpt_authenticated_ui",
              "update_observed_bodies": True,
              "observation_digest": latest_observations(self.archive.db)["test-chat"]["digest"],
              "windows": [["q-1", "a-1", "q-2", "a-2"]],
              "messages": [dict(m, source="chatgpt_ui_copy", body_complete=True)
                           for m in self.payload()["document"]["messages"]]}
        ui["messages"][0]["text"] += " 복원한 긴 질문\r\n|표|"
        ui["messages"][1]["text"] += " 복원한 긴 답변\r\n`원문`"
        apply_ui_repair(self.archive, ui)
        return self.payload()

    def assert_preserved(self, before, after):
        current = {m["id"]: m for m in after["document"]["messages"]}
        for message in before["document"]["messages"]:
            self.assertEqual(current[message["id"]], message)
        for field in ("ui_recollections", "ui_body_evidence", "coverage"):
            if field in before["document"]:
                self.assertEqual(after["document"][field], before["document"][field])
        if "source_browser" in before:
            self.assertEqual(after["source_browser"], before["source_browser"])

    def test_append_despite_missing_history_short_bodies_and_references(self):
        before = self.seed_restored()
        history = {p: p.read_bytes() for p in (self.output / "history").rglob("*") if p.is_file()}
        cap = capture([turn(3, "새 답변\n|표|"), turn(2, reference("a-2"))])
        cap["pages"][0]["turns"][1]["items"][0]["content"][0]["text"] = "질문"
        report = self.archive.ingest(bundle(cap))
        self.assertEqual(report["errors"], [])
        saved = report["saved"][0]
        self.assertEqual(saved["added_message_ids"], ["q-3", "a-3"])
        self.assertEqual((saved["added_questions"], saved["added_answer_bodies"]), (1, 1))
        self.assertEqual(saved["preserved_missing_ids"], ["q-1", "a-1"])
        self.assertEqual(saved["preserved_bodies"],
                         [{"id": "q-2", "reason": "shorter_body"}, {"id": "a-2", "reason": "reference"}])
        after = self.payload()
        self.assert_preserved(before, after)
        self.assertEqual([m["id"] for m in after["document"]["messages"]],
                         ["q-1", "a-1", "q-2", "a-2", "q-3", "a-3"])
        for path, content in history.items():
            self.assertEqual(path.read_bytes(), content)
        for path in (self.output / "conversations").iterdir():
            self.assertIn("새 답변\n|표|", path.read_text())
            self.assertIn("복원한 긴 질문".encode(), path.read_bytes())
        self.assertFalse(saved["text_complete"])
        self.assertEqual(report["recovered_ids"], [])
        self.assertEqual(report["run_status"], "verification_required")
        self.assertEqual(after["source_pages"], cap["pages"])
        self.assertEqual(self.archive.state()["retry_state"], [])

    def test_repeat_and_tail_only_capture_do_not_duplicate_or_shrink(self):
        self.seed_restored()
        cap = capture([turn(3), turn(2)])
        self.archive.ingest(bundle(cap))
        before = self.payload()
        count = self.archive.db.execute("SELECT count(*) FROM snapshots").fetchone()[0]
        for raw in (cap, capture([turn(3)])):
            report = self.archive.ingest(bundle(raw))
            self.assertEqual(report["errors"], [])
            self.assertEqual(report["changed"], 0)
            self.assertEqual(report["saved"][0]["added_message_ids"], [])
            self.assertEqual(self.payload(), before)
        self.assertEqual(self.archive.db.execute("SELECT count(*) FROM snapshots").fetchone()[0], count)

    def test_ui_images_attachments_and_provenance_survive_append(self):
        self.seed_restored()
        image = self.output.parent / "picture.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\noriginal-image")
        cap = {"schema_version": 1, "conversation_id": "test-chat",
               "url": "https://chatgpt.com/c/test-chat", "source": "chatgpt_authenticated_ui",
               "messages": [{"id": "a-2", "role": "assistant", "text": "답변입니다.",
                             "source": "chatgpt_ui_copy"}],
               "media": [{"message_id": "a-2", "question_id": "q-2", "role": "assistant",
                          "name": "picture.png", "download_path": str(image)}]}
        apply_ui_repair(self.archive, cap)
        before = self.payload()
        files = {p: p.read_bytes() for p in (self.output / "attachments").rglob("*") if p.is_file()}
        report = self.archive.ingest(bundle(capture([turn(3), turn(2)])))
        self.assertFalse(report["errors"])
        self.assert_preserved(before, self.payload())
        self.assertEqual(self.payload()["document"]["attachments"], before["document"]["attachments"])
        for path, data in files.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertIn("![대화 이미지]", next((self.output / "conversations").glob("*.md")).read_text())

    def test_history_reconstruction_segments_are_extended_without_erasing_gaps(self):
        self.archive.ingest(bundle(capture([turn(4), turn(1)])))
        old = self.payload()
        old["document"]["history_reconstruction"] = {
            "segments": [{"message_ids": ["q-1", "a-1"], "connection_to_previous_verified": False},
                         {"message_ids": ["q-4", "a-4"], "connection_to_previous_verified": False}]}
        self.archive._save_document(old["document"], capture([turn(4), turn(1)]))
        report = self.archive.ingest(bundle(capture([turn(5), turn(4)])))
        self.assertFalse(report["errors"])
        segments = self.payload()["document"]["history_reconstruction"]["segments"]
        self.assertEqual([s["message_ids"] for s in segments],
                         [["q-1", "a-1"], ["q-4", "a-4", "q-5", "a-5"]])
        self.assertFalse(any(s["connection_to_previous_verified"] for s in segments))

    def test_shared_ids_can_place_missing_middle_question_and_answer(self):
        self.archive.ingest(bundle(capture([turn(3), turn(1)])))
        before = self.payload()
        report = self.archive.ingest(bundle(capture([turn(3), turn(2), turn(1)])))
        self.assertFalse(report["errors"])
        self.assert_preserved(before, self.payload())
        self.assertEqual([m["id"] for m in self.payload()["document"]["messages"]],
                         [prefix + str(i) for i in (1, 2, 3) for prefix in ("q-", "a-")])

    def test_question_can_be_added_then_its_answer_on_the_next_run(self):
        self.seed_restored()
        question_only = turn(3)
        question_only["items"].pop()
        first = self.archive.ingest(bundle(capture([question_only, turn(2)])))
        self.assertFalse(first["errors"])
        self.assertEqual(first["saved"][0]["added_message_ids"], ["q-3"])
        self.assertIn("q-3", self.payload()["document"]["questions_without_observed_answer"])
        second = self.archive.ingest(bundle(capture([turn(3)])))
        self.assertFalse(second["errors"])
        self.assertEqual(second["saved"][0]["added_message_ids"], ["a-3"])
        self.assertNotIn("q-3", self.payload()["document"]["questions_without_observed_answer"])

    def test_conflicts_unanchored_and_unfinished_data_remain_observations_only(self):
        self.seed_restored()
        cases = [capture([turn(3)]),  # 공통 ID 없음
                 capture([turn(3), turn(1)]),  # 기존 다음 차례와 분기
                 capture([turn(2), turn(3)]),  # 새 답변/질문을 앞에 놓으면 연결 모호
                 capture([turn(3, reference("a-3")), turn(2)]),
                 capture([turn(3, ""), turn(2)]),
                 capture([turn(3, "x" * 20000), turn(2)]),
                 capture([turn(3), turn(2, "길이가 더 긴 다른 기존 본문")]),
                 capture([turn(3), turn(2, reference("wrong-id"))])]
        wrong_role = capture([turn(3), turn(2)])
        wrong_role["pages"][0]["turns"][1]["items"][1].update(
            type="userMessage", content=[{"type": "text", "text": "답변입니다."}])
        cases.append(wrong_role)
        unfinished = capture([turn(3), turn(2)])
        unfinished["pages"][0]["turns"][0]["status"] = "in_progress"
        cases.append(unfinished)
        changing = capture([turn(3), turn(2)])
        changing["changing"] = True
        cases.append(changing)
        before = self.payload()
        for cap in cases:
            with self.subTest(cap=cap):
                report = self.archive.ingest(bundle(cap))
                self.assertTrue(report["errors"])
                self.assertEqual(report["changed"], 0)
                self.assertEqual(self.payload(), before)
        self.assertEqual(self.archive.state()["retry_state"], [])

    def test_old_body_is_not_replaced_even_when_new_body_is_longer(self):
        before = self.seed_restored()
        cap = capture([turn(3), turn(2)])
        cap["pages"][0]["turns"][1]["items"][1]["text"] = "답변입니다. 더 긴 내용"
        report = self.archive.ingest(bundle(cap))
        self.assertTrue(report["errors"])
        self.assertEqual(self.payload(), before)

    def test_new_question_cannot_reassign_an_existing_answer(self):
        self.archive.ingest(bundle())
        before = self.payload()
        q1 = turn(1)
        q1["items"].pop()
        q2 = turn(2)
        q2["items"][1]["id"] = "a-1"
        report = self.archive.ingest(bundle(capture([q2, q1])))
        self.assertTrue(report["errors"])
        self.assertEqual(self.payload(), before)

    def test_old_full_verification_is_not_extended_to_new_messages(self):
        from test_quality import QualityTests
        from chatgpt_archive.quality import issues
        self.archive.ingest(bundle())
        ui = QualityTests.full_ui(self)
        apply_ui_repair(self.archive, ui)
        before = self.payload()
        self.assertTrue(before["document"]["text_complete"])
        result = self.archive.ingest(bundle(capture([turn(2), turn(1)])))
        self.assertFalse(result["errors"])
        after = self.payload()
        self.assert_preserved(before, after)
        self.assertFalse(after["document"]["text_complete"])
        self.assertIn("history_unverified", [item["code"] for item in issues(after["document"])])
        self.assertEqual(result["recovered_ids"], [])

    def test_first_capture_still_rejects_reference_only_and_truncated_bodies(self):
        for answer in (reference(), "x" * 20000):
            report = self.archive.ingest(bundle(capture([turn(1, answer)])))
            self.assertTrue(report["errors"])
            self.assertEqual(report["total"], 0)
