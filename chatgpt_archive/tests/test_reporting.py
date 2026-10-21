import copy
import json
import unittest

from chatgpt_archive.reporting import compact_report
from chatgpt_archive.retry_policy import RetryHistory, error_key


class ReportTests(unittest.TestCase):
    def test_large_missing_id_lists_cannot_truncate_summary(self):
        errors = [{"id": str(i), "code": "incomplete_history", "stage": "validation",
                   "message": "existing backup preserved",
                   "message_ids": [str(j) * 36 for j in range(1000)]} for i in range(100)]
        report = {"errors": errors, "new_errors": errors, "verification_required": [],
                  "new_exclusions": [], "run_status": "partial_failure"}
        original = copy.deepcopy(report)
        compact = compact_report(report)
        self.assertLess(len(json.dumps(compact)), 20000)
        self.assertEqual(compact["report_counts"]["new_errors"], 100)
        self.assertEqual(compact["errors"][0]["message_ids_count"], 1000)
        self.assertEqual(report, original)

    def test_historical_content_rejections_never_count_as_transport_failures(self):
        retry = RetryHistory()
        for _ in range(110):
            retry.apply({"finished_at": "2026-10-20T00:00:00Z",
                         "errors": [{"id": "chat", "stage": "read_thread",
                                     "code": "incomplete_history"}]})
        self.assertNotIn("chat", retry.threads)
        retry.apply({"finished_at": "2026-10-20T00:00:00Z",
                     "errors": [{"id": "chat", "stage": "read_thread", "code": "timeout"}]})
        self.assertEqual(retry.threads["chat"]["consecutive_failures"], 1)

    def test_old_validation_stage_does_not_trigger_a_second_alert(self):
        old = {"id": "chat", "code": "incomplete_history", "stage": "read_thread"}
        self.assertEqual(error_key(old), error_key(dict(old, stage="validation")))
