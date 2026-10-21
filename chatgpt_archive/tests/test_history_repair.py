import copy
import json
from pathlib import Path
import tempfile
import unittest

from chatgpt_archive.archive import Archive, ArchiveError, normalize
from chatgpt_archive.history_repair import GAP_WARNING, preview_repairs
from test_archive import capture, reference, turn


class HistoryRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name) / "archive"
        self.archive = Archive(self.output).__enter__()

    def tearDown(self):
        self.archive.__exit__()
        self.temp.cleanup()

    def seed(self, numbers, overrides=None, ident="test-chat", mutate=None):
        """구 수집기가 최근 일부 대화로 덮어쓴 상태를 재현한다."""
        cap = capture([turn(number) for number in reversed(numbers)])
        cap["id"] = ident
        cap["pages"][0]["thread"]["id"] = ident
        for entry in cap["pages"][0]["turns"]:
            entry["id"] = entry["items"][0]["id"]
        doc = normalize(cap)
        for message in doc["messages"]:
            if overrides and message["id"] in overrides:
                message["text"] = overrides[message["id"]]
                # 원본 응답에도 실제 관측한 본문을 남긴다.
                for item in cap["pages"][0]["turns"]:
                    for entry in item["items"]:
                        if entry["id"] == message["id"] and entry["type"] == "agentMessage":
                            entry["text"] = message["text"]
        if mutate:
            mutate(doc)
        self.archive._save_document(doc, cap, last_error="기존 조회 오류")
        return doc

    def document(self, ident="test-chat"):
        return json.loads(self.archive.db.execute("""SELECT s.payload FROM conversations c
            JOIN snapshots s ON c.id=s.thread_id AND c.digest=s.digest WHERE c.id=?""",
            (ident,)).fetchone()[0])["document"]

    def test_restores_exact_bodies_preserves_versions_and_remote_status(self):
        body = "원문\r\n\r\n|표|값|\n|---|---|\n|한글|✅|\n`do not execute`"
        self.seed([1, 2, 3], {"a-1": body})
        self.seed([3, 4, 5])
        files = {p: p.read_bytes() for p in (self.output / "history").rglob("*") if p.is_file()}
        old = dict(self.archive.db.execute("SELECT * FROM conversations").fetchone())
        report = {"finished_at": "2026-09-15T00:00:00Z",
                  "errors": [{"id": "test-chat", "message": "기존 오류"}]}
        self.archive.db.execute("INSERT INTO runs(finished_at,report) VALUES (?,?)",
                                ("old", json.dumps(report)))
        self.archive.db.commit()
        result = self.archive.repair_history(["test-chat"])
        item = result["repairs"][0]
        self.assertEqual((item["added_questions"], item["added_answer_bodies"]), (2, 2))
        self.assertEqual([m["id"] for m in self.document()["messages"]],
                         [prefix + str(n) for n in range(1, 6) for prefix in ("q-", "a-")])
        for path, content in files.items():
            self.assertEqual(path.read_bytes(), content)
        for path in (self.output / "conversations").iterdir():
            self.assertIn(body.encode("utf-8"), path.read_bytes())
        row = self.archive.db.execute("SELECT * FROM conversations").fetchone()
        self.assertEqual(row["last_checked_at"], old["last_checked_at"])
        self.assertEqual(row["last_error"], old["last_error"])
        self.assertEqual(row["listed_updated_at"], old["listed_updated_at"])
        self.assertEqual(json.loads(self.archive.db.execute("SELECT report FROM runs").fetchone()[0]), report)
        self.assertEqual(self.archive.db.execute("SELECT count(*) FROM runs").fetchone()[0], 1)
        self.assertFalse(row["text_complete"])

    def test_preview_is_read_only_and_apply_idempotent(self):
        self.seed([1, 2])
        self.seed([2, 3])
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in self.output.rglob("*") if p.is_file()}
        preview = preview_repairs(self.output, ["test-chat"])
        self.assertFalse(preview["applied"])
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns)
                                for p in self.output.rglob("*") if p.is_file()})
        self.archive.repair_history(["test-chat"])
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in self.output.rglob("*") if p.is_file()}
        self.assertFalse(self.archive.repair_history(["test-chat"])["repairs"][0]["changed"])
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns)
                                for p in self.output.rglob("*") if p.is_file()})

    def test_disconnected_segments_get_visible_gaps_not_fake_edges(self):
        self.seed([1, 2])
        self.seed([5, 6])
        result = self.archive.repair_history(["test-chat"])
        self.assertEqual(result["repairs"][0]["segments"], 2)
        self.assertEqual(len(self.document()["history_reconstruction"]["segments"]), 2)
        for path in (self.output / "conversations").iterdir():
            self.assertIn(GAP_WARNING, path.read_text())
        self.assertEqual(preview_repairs(self.output, ["test-chat"])["repairs"][0]["segments"], 2)

    def test_reference_is_upgraded_only_with_exact_unique_original(self):
        self.seed([1, 2], {"a-2": "확인된 답변"})
        self.seed([2, 3], {"a-2": reference("a-2"), "a-3": reference("a-3")})
        result = self.archive.repair_history(["test-chat"])["repairs"][0]
        self.assertEqual(result["upgraded_answer_bodies"], 1)
        self.assertEqual(result["unresolved_answers"], 1)
        self.assertEqual(next(m["text"] for m in self.document()["messages"] if m["id"] == "a-2"),
                         "확인된 답변")

    def test_reference_index_changes_do_not_change_current_placeholder(self):
        self.seed([1, 2], {"a-2": reference("a-2")})
        newer = reference("a-2").replace('index="0"', 'index="7"')
        self.seed([2, 3], {"a-2": newer})
        self.archive.repair_history(["test-chat"])
        self.assertEqual(next(m["text"] for m in self.document()["messages"] if m["id"] == "a-2"), newer)

    def test_conflicting_text_and_wrong_reference_source_rejected(self):
        self.seed([1, 2])
        self.seed([2, 3], {"a-2": "다른 답변"})
        with self.assertRaisesRegex(ArchiveError, "서로 다른 본문"):
            self.archive.repair_history(["test-chat"])
        self.seed([1, 2], ident="wrong-ref")
        self.seed([2, 3], {"a-2": reference("a-other")}, ident="wrong-ref")
        with self.assertRaisesRegex(ArchiveError, "메시지 ID"):
            self.archive.repair_history(["wrong-ref"])

    def test_branch_and_cycle_rejected(self):
        self.seed([1, 2])
        self.seed([1, 3])
        with self.assertRaisesRegex(ArchiveError, "분기|모호성"):
            self.archive.repair_history(["test-chat"])
        self.seed([1, 2], ident="cycle")
        self.seed([2, 1], ident="cycle")
        with self.assertRaisesRegex(ArchiveError, "순서"):
            self.archive.repair_history(["cycle"])

    def test_missing_or_overlapping_segment_times_rejected(self):
        self.seed([1])
        self.seed([3], mutate=lambda doc: doc["messages"][0].update(time=None))
        with self.assertRaisesRegex(ArchiveError, "시각"):
            self.archive.repair_history(["test-chat"])
        self.seed([1, 5], ident="overlap")
        self.seed([3], ident="overlap")
        with self.assertRaisesRegex(ArchiveError, "겹칩니다"):
            self.archive.repair_history(["overlap"])

    def test_orphan_answer_metadata_uses_observed_paired_question(self):
        paired = self.seed([1, 2])
        def orphan(doc):
            doc["messages"] = doc["messages"][1:]
            doc["messages"][0]["turn_id"] = "a-2"
            doc["messages"][0]["time"] += 0.5
        self.seed([2, 3], mutate=orphan)
        self.archive.repair_history(["test-chat"])
        answer = next(m for m in self.document()["messages"] if m["id"] == "a-2")
        self.assertEqual(answer, paired["messages"][-1])

    def test_plan_validates_all_targets_before_writing(self):
        self.seed([1, 2])
        self.seed([2, 3])
        before = copy.deepcopy(self.document())
        with self.assertRaises(ArchiveError):
            self.archive.repair_history(["test-chat", "unknown"])
        self.assertEqual(before, self.document())
        with self.assertRaises(ArchiveError):
            self.archive.repair_history([])


if __name__ == "__main__":
    unittest.main()
