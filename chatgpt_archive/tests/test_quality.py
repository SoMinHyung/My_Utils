import copy
import json
import unittest

from chatgpt_archive.archive import ArchiveError, normalize, read_archive
from chatgpt_archive.quality import issues
from chatgpt_archive.ui_repair import apply_ui_repair
from chatgpt_archive.verification import verification_queue, latest_observations
import test_archive as fixtures
from test_archive import capture, bundle, turn, reference


class QualityTests(unittest.TestCase):
    setUp = fixtures.ArchiveTests.setUp
    tearDown = fixtures.ArchiveTests.tearDown

    def full_ui(self, cap=None):
        cap = cap or capture()
        observation = latest_observations(self.archive.db).get("test-chat")
        messages = []
        for item in normalize(cap)["messages"]:
            messages.append(dict(item, source="chatgpt_ui_copy", body_complete=True, media_count=0))
        return {"schema_version": 1, "source": "chatgpt_authenticated_ui",
                "conversation_id": "test-chat", "url": "https://chatgpt.com/c/test-chat",
                "captured_at": "2026-10-20T04:00:00+00:00",
                "messages": messages, "windows": [[m["id"] for m in messages]],
                "observation_digest": observation["digest"] if observation else None,
                "coverage": {"policy_version": 2, "scope": "observed_current_branch",
                             "history_start": True, "history_end": True,
                             "first_message_id": messages[0]["id"], "last_message_id": messages[-1]["id"],
                             "start_observations": 3, "end_observations": 3, "loading": False,
                             "unsupported_content": []}}

    def test_first_capture_false_end_never_claims_complete_and_alert_is_deduplicated(self):
        # 비교할 옛 백업이 없어도 false-end는 검증 대기다.
        result = self.archive.ingest(bundle(capture([turn(9)])))
        self.assertEqual(result["run_status"], "verification_required")
        self.assertFalse(result["saved"][0]["text_complete"])
        self.assertEqual(len(result["new_verification_required"]), 1)
        self.assertEqual(self.archive.ingest(bundle(capture([turn(9)])))["new_verification_required"], [])
        self.assertIn("전체 이력 미검증", (self.output / "INDEX.md").read_text())

    def test_rejected_capture_is_preserved_without_replacing_good_file(self):
        self.archive.ingest(bundle(capture([turn(2), turn(1)])))
        before = next((self.output / "conversations").glob("*.md")).read_bytes()
        result = self.archive.ingest(bundle(capture([turn(2)])))
        self.assertEqual(result["errors"][0]["code"], "incomplete_history")
        self.assertEqual(next((self.output / "conversations").glob("*.md")).read_bytes(), before)
        self.assertEqual(len(list((self.output / "observations").rglob("*.json"))), 2)
        self.assertEqual(verification_queue(self.archive.db)["items"][0]["id"], "test-chat")

    def test_same_observation_at_later_time_does_not_duplicate_or_rewrite_raw_file(self):
        self.archive.ingest(bundle())
        original = next((self.output / "observations").rglob("*.json")).read_bytes()
        digest = latest_observations(self.archive.db)["test-chat"]["digest"]
        cap = capture()
        cap["collected_at"] = "2026-10-20T04:30:00Z"
        self.archive.ingest(bundle(cap))
        self.assertEqual(self.archive.db.execute("SELECT count(*) FROM observations").fetchone()[0], 1)
        self.assertEqual(next((self.output / "observations").rglob("*.json")).read_bytes(), original)
        self.assertEqual(latest_observations(self.archive.db)["test-chat"]["digest"], digest)

    def test_index_only_and_mixed_reference_can_use_unique_whole_original(self):
        self.archive.ingest(bundle())
        for body in ('::chatgpt-content-reference{index="0"}',
                     '앞\n::chatgpt-content-reference{index="0"}\n뒤'):
            report = self.archive.ingest(bundle(capture([turn(1, body)])))
            self.assertFalse(report["errors"])
            self.assertEqual(report["saved"][0]["reference_recoveries"][0]["message_id"], "a-1")

    def test_questionless_empty_and_unanswered_turns_are_incomplete(self):
        cap = capture()
        cap["pages"][0]["turns"][0]["items"] = cap["pages"][0]["turns"][0]["items"][1:]
        self.assertIn("missing_question", [p["code"] for p in issues(normalize(cap))])
        cap = capture([turn(1, "")])
        codes = [p["code"] for p in issues(normalize(cap))]
        self.assertIn("empty_message", codes)
        self.assertIn("missing_answer", codes)

    def test_silent_shortening_of_question_or_answer_preserves_original(self):
        self.archive.ingest(bundle())
        before = next((self.output / "conversations").glob("*.md")).read_bytes()
        for role in ("question", "answer"):
            cap = capture()
            items = cap["pages"][0]["turns"][0]["items"]
            if role == "question":
                items[0]["content"][0]["text"] = "질문"
            else:
                items[1]["text"] = "답"
            report = self.archive.ingest(bundle(cap))
            self.assertEqual(report["errors"][0]["code"], "ui_verification_required")
            self.assertEqual(next((self.output / "conversations").glob("*.md")).read_bytes(), before)

    def test_full_ui_verification_and_repeated_import_are_idempotent(self):
        self.archive.ingest(bundle())
        ui = self.full_ui()
        result = apply_ui_repair(self.archive, ui)
        self.assertTrue(result["text_complete"])
        self.assertTrue(result["verified_current_branch"])
        self.assertEqual(verification_queue(self.archive.db)["items"], [])
        self.assertFalse(apply_ui_repair(self.archive, ui)["changed"])
        self.assertTrue(read_archive(self.output, "state")["conversations"][0]["text_complete"])

    def test_source_update_requires_new_ui_verification_and_preserves_ui_body(self):
        self.archive.ingest(bundle())
        ui = self.full_ui()
        ui["messages"][1]["text"] = "전체 원문 (조회 도구에서 안 보였던 뒤쪽)"
        apply_ui_repair(self.archive, ui)
        before = next((self.output / "conversations").glob("*.md")).read_bytes()
        changed = capture()
        changed["listed_updated_at"] += 1
        self.archive.ingest(bundle(changed))
        self.assertEqual(next((self.output / "conversations").glob("*.md")).read_bytes(), before)
        self.assertIn("source_update_unverified", verification_queue(self.archive.db)["items"][0]["reasons"])
        with self.assertRaises(ArchiveError):
            apply_ui_repair(self.archive, ui)  # 수집 중 관측 버전이 바뀐 경우

    def test_missing_first_middle_or_last_and_incomplete_copy_are_rejected(self):
        self.archive.ingest(bundle(capture([turn(2), turn(1)])))
        ui = self.full_ui(capture([turn(2), turn(1)]))
        for mutate in (
            lambda c: c["coverage"].update(history_start=False),
            lambda c: c["coverage"].update(loading=True),
            lambda c: c["coverage"].update(unsupported_content=["quiz"]),
            lambda c: c["messages"][0].update(body_complete=False),
            lambda c: c["messages"][1].update(source="chatgpt_ui_visible_text"),
            lambda c: c.update(windows=[["q-1", "a-1"], ["q-2", "a-2"]]),
            lambda c: c["messages"].pop(),
            lambda c: c["messages"][0].update(media_count=1),
        ):
            bad = copy.deepcopy(ui)
            mutate(bad)
            with self.assertRaises(ValueError):
                apply_ui_repair(self.archive, bad)
        self.assertFalse(self.archive.state()["conversations"][0]["text_complete"])

    def test_new_reference_only_conversation_can_be_seeded_from_observation(self):
        self.archive.ingest(bundle(capture([turn(1, reference())])))
        self.assertEqual(self.archive.db.execute("SELECT count(*) FROM conversations").fetchone()[0], 0)
        result = apply_ui_repair(self.archive, self.full_ui())
        self.assertEqual(result["restored_answers"], 1)
        self.assertTrue(result["text_complete"])

    def test_first_truncated_conversation_can_be_created_from_full_ui_body(self):
        cap = capture([turn(1, "x" * 20000)])
        self.archive.ingest(bundle(cap))
        self.assertEqual(self.archive.db.execute("SELECT count(*) FROM conversations").fetchone()[0], 0)
        ui = self.full_ui()
        ui["messages"][1]["text"] = "x" * 25000
        result = apply_ui_repair(self.archive, ui)
        self.assertTrue(result["text_complete"])
        self.assertIn("x" * 25000, next((self.output / "conversations").glob("*.md")).read_text())

    def test_queue_respects_exclusions_and_rate_backoff(self):
        self.archive.ingest(bundle())
        from chatgpt_archive.retry_policy import RetryHistory
        retry = RetryHistory()
        retry.threads["test-chat"] = {"excluded": True}
        self.assertEqual(verification_queue(self.archive.db, retry)["items"][0]["blocked_reason"], "excluded")
        retry.rate_limit_skip_runs_remaining = 2
        self.assertEqual(verification_queue(self.archive.db, retry)["items"][0]["blocked_reason"], "rate_limit_backoff")

    def test_queue_is_read_only_for_legacy_database(self):
        self.archive.ingest(bundle())
        self.archive.db.execute("DROP TABLE observations")
        self.archive.db.commit()
        before = self.output.joinpath("archive.sqlite3").read_bytes()
        self.assertEqual(len(read_archive(self.output, "verification-queue")["items"]), 1)
        self.assertEqual(self.output.joinpath("archive.sqlite3").read_bytes(), before)

    def test_content_failure_is_immediately_eligible_for_screen_verification(self):
        self.archive.ingest(bundle(capture([turn(2), turn(1)])))
        result = self.archive.ingest(bundle(capture([turn(2)])))
        self.assertEqual(result["errors"][0]["stage"], "validation")
        self.assertTrue(verification_queue(self.archive.db)["items"][0]["eligible"])
        self.assertFalse(self.archive.state()["retry_state"])

    def test_partial_whole_copy_can_repair_body_without_claiming_complete(self):
        self.archive.ingest(bundle())
        ui = self.full_ui()
        del ui["coverage"]
        ui["update_observed_bodies"] = True
        ui["messages"][0]["text"] += " 누락되었던 질문 뒷부분"
        ui["messages"][1]["text"] += " 누락되었던 답변 뒷부분"
        result = apply_ui_repair(self.archive, ui)
        self.assertEqual(result["replaced_body_ids"], ["q-1", "a-1"])
        self.assertFalse(result["text_complete"])
        item = verification_queue(self.archive.db)["items"][0]
        self.assertTrue(item["eligible"])
        self.assertEqual(item["verified_body_count"], 2)
        self.assertIsNone(item["next_unverified_message_id"])
        self.assertEqual(item["last_observed_window"], ["q-1", "a-1"])
        self.assertIn("history_unverified", item["reasons"])
        ui["observation_digest"] = "stale"
        with self.assertRaises(ArchiveError):
            apply_ui_repair(self.archive, ui)

    def test_partial_copy_resume_evidence_expires_after_source_change(self):
        self.archive.ingest(bundle())
        ui = self.full_ui()
        del ui["coverage"]
        ui["update_observed_bodies"] = True
        ui["messages"] = ui["messages"][:1]
        ui["windows"] = [["q-1"]]
        apply_ui_repair(self.archive, ui)
        item = verification_queue(self.archive.db)["items"][0]
        self.assertEqual(item["verified_body_count"], 1)
        self.assertEqual(item["next_unverified_message_id"], "a-1")
        changed = capture()
        changed["listed_updated_at"] += 1
        self.archive.ingest(bundle(changed))
        item = verification_queue(self.archive.db)["items"][0]
        self.assertEqual(item["verified_body_count"], 0)
        self.assertEqual(item["next_unverified_message_id"], "q-1")

    def test_partial_copy_does_not_clear_prior_unsupported_content(self):
        self.archive.ingest(bundle())
        ui = self.full_ui()
        del ui["coverage"]
        ui["unsupported_content"] = ["quiz"]
        apply_ui_repair(self.archive, ui)
        ui["unsupported_content"] = []
        apply_ui_repair(self.archive, ui)
        self.assertIn("unsupported_content", verification_queue(self.archive.db)["items"][0]["reasons"])
        full = apply_ui_repair(self.archive, self.full_ui())
        self.assertTrue(full["text_complete"])
        self.assertEqual(verification_queue(self.archive.db)["items"], [])

    def test_single_colon_reference_is_not_mistaken_for_answer_content(self):
        report = self.archive.ingest(bundle(capture([turn(1, ':chatgpt-content-reference{index="0"}')])))
        self.assertEqual(report["errors"][0]["code"], "unresolved_content_reference")
