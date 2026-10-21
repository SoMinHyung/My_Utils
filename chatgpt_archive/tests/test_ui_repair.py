import json
import unittest

from chatgpt_archive.archive import ArchiveError
from chatgpt_archive.ui_repair import apply_ui_repair, preview_ui_repair
import test_history_repair as fixtures
from test_archive import reference


class UIRepairTests(unittest.TestCase):
    setUp = fixtures.HistoryRepairTests.setUp
    tearDown = fixtures.HistoryRepairTests.tearDown
    seed = fixtures.HistoryRepairTests.seed
    document = fixtures.HistoryRepairTests.document
    def ui_capture(self):
        return {"schema_version": 1, "conversation_id": "test-chat",
                "url": "https://chatgpt.com/c/test-chat", "source": "chatgpt_authenticated_ui",
                "windows": [["q-0", "a-0", "q-1", "a-1", "q-2", "a-2"]],
                "messages": [
                    {"id": "q-0", "role": "user", "text": "처음 질문", "source": "chatgpt_ui_visible_text"},
                    {"id": "a-0", "role": "assistant", "text": "처음 답변", "source": "chatgpt_ui_copy"},
                    {"id": "q-1", "role": "user", "text": "질문 1", "source": "chatgpt_ui_visible_text"},
                    {"id": "a-1", "role": "assistant", "text": "실제 답변\r\n|표|", "source": "chatgpt_ui_copy"},
                    {"id": "q-2", "role": "user", "text": "질문 2", "source": "chatgpt_ui_visible_text"},
                    {"id": "a-2", "role": "assistant", "text": "화면 표시 형식", "source": "chatgpt_ui_copy"},
                ]}

    def test_ui_import_exact_body_source_and_new_question(self):
        self.seed([1, 2], {"a-1": reference()})
        capture = self.ui_capture()
        before = list(self.archive.db.execute("SELECT digest FROM snapshots"))
        preview = preview_ui_repair(self.output, capture)
        self.assertEqual(list(self.archive.db.execute("SELECT digest FROM snapshots")), before)
        result = apply_ui_repair(self.archive, capture)
        self.assertEqual((result["added_questions"], result["restored_answers"], result["unresolved_answers"]),
                         (1, 1, 0))
        self.assertEqual(result["messages"], preview["messages"])
        messages = {m["id"]: m for m in self.document()["messages"]}
        self.assertEqual(messages["a-1"]["text"], "실제 답변\r\n|표|")
        self.assertEqual(messages["a-2"]["text"], "답변입니다.")
        self.assertIsNone(messages["q-0"]["time"])
        self.assertFalse(apply_ui_repair(self.archive, capture)["changed"])
        payload = json.loads(self.archive.db.execute("""SELECT s.payload FROM snapshots s
            JOIN conversations c ON s.thread_id=c.id AND s.digest=c.digest""").fetchone()[0])
        self.assertEqual(payload["source_browser"], capture)
        self.assertEqual(self.archive.db.execute("SELECT count(*) FROM runs").fetchone()[0], 0)

    def test_ui_conflicts_or_wrong_conversation_rejected(self):
        self.seed([1, 2], {"a-1": reference()})
        for mutate in (lambda c: c.update(url="https://chatgpt.com/c/other"),
                       lambda c: c["messages"][2].update(text="다른 질문"),
                       lambda c: c["messages"][3].update(text=reference("a-1")),
                       lambda c: c["windows"].append(["q-2", "q-0"])):
            cap = self.ui_capture()
            mutate(cap)
            with self.assertRaises(ArchiveError):
                apply_ui_repair(self.archive, cap)
        self.assertEqual(len(self.document()["messages"]), 4)

    def test_index_only_and_mixed_references_use_observed_outer_message_id(self):
        self.seed([1, 2], {"a-1": '도입\n::chatgpt-content-reference{index="0"}\n후기'})
        result = apply_ui_repair(self.archive, self.ui_capture())
        self.assertEqual(result["restored_answers"], 1)
        self.assertEqual(self.document()["messages"][3]["text"], "실제 답변\r\n|표|")

    def test_explicit_different_reference_message_id_is_rejected(self):
        self.seed([1, 2], {"a-1": reference("other-id")})
        with self.assertRaises(ArchiveError):
            apply_ui_repair(self.archive, self.ui_capture())

    def test_ui_image_preserves_message_link_and_history_relative_path(self):
        self.seed([1, 2], {"a-1": reference()})
        cap = self.ui_capture()
        image = self.output.parent / "download.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        cap["media"] = [{"message_id": "image-2", "question_id": "q-2", "role": "assistant",
                         "name": "answer.png", "download_path": str(image)}]
        cap["media_windows"] = [["q-2", "a-2", "image-2"]]
        result = apply_ui_repair(self.archive, cap)
        self.assertEqual(result["images"], 1)
        self.assertEqual(self.document()["messages"][-1]["text"], "")
        self.assertEqual(len(self.document()["messages"][-1]["media"]), 1)
        self.assertTrue(next((self.output / "attachments").rglob("*.png")).exists())
        current = next((self.output / "conversations").glob("*.md")).read_text()
        self.assertIn("![대화 이미지](../attachments/", current)
        self.assertTrue(any("![대화 이미지](../../attachments/" in p.read_text()
                            for p in (self.output / "history").rglob("*.md")))

    def test_jpeg_format_validation_and_unanswered_question(self):
        self.seed([1, 2], {"a-1": reference()})
        cap = self.ui_capture()
        cap["messages"].append({"id": "q-3", "role": "user", "text": "추가 질문",
                                "source": "chatgpt_ui_visible_text"})
        cap["windows"][0].append("q-3")
        image = self.output.parent / "download.jpeg"
        image.write_bytes(b"\xff\xd8\xfffixture")
        cap["media"] = [{"message_id": "q-3", "question_id": "q-3", "role": "user",
                         "name": "question.png", "download_path": str(image)}]
        with self.assertRaises(ArchiveError):
            apply_ui_repair(self.archive, cap)
        cap["media"][0]["name"] = "question.jpeg"
        result = apply_ui_repair(self.archive, cap)
        self.assertEqual(result["questions_without_answer_ids"], ["q-3"])
        self.assertIn("이 질문에 연결되는 답변은 확보하지 못했습니다.",
                      next((self.output / "conversations").glob("*.md")).read_text())
