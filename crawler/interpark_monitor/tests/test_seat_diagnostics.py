import json
import tempfile
import unittest
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from interpark_monitor.seat_diagnostics import (
    SeatResponseDiagnostics,
    find_candidate_paths,
    inspect_seat_page,
    safe_url,
    save_seat_diagnostics,
    wait_for_manual_image_completion,
)


class _FakeRequest:
    resource_type = "xhr"


class _FakeResponse:
    request = _FakeRequest()
    url = "https://tickets.example/seat/list?access_token=secret#fragment"
    status = 200
    headers = {"content-type": "application/json"}

    @staticmethod
    def json():
        return {
            "data": {
                "availableCount": 3,
                "status": "AVAILABLE",
                "seatToken": "this-token-must-not-be-logged",
            }
        }


class _FakeInput:
    def __init__(self, *, visible=True, timeout=False):
        self.visible = visible
        self.timeout = timeout
        self.wait_calls = []

    @property
    def first(self):
        return self

    def count(self):
        return 1

    def is_visible(self):
        return self.visible

    def wait_for(self, **kwargs):
        self.wait_calls.append(kwargs)
        if self.timeout:
            raise PlaywrightTimeoutError("manual wait timeout")


class _FakePage:
    def __init__(self, image_input):
        self.image_input = image_input
        self.waited_ms = []

    def locator(self, _selector):
        return self.image_input

    def wait_for_timeout(self, milliseconds):
        self.waited_ms.append(milliseconds)


class SeatDiagnosticsTests(unittest.TestCase):
    def test_safe_url_removes_query_fragment_and_long_path_token(self):
        result = safe_url(
            "https://name:password@example.com/seat/abcdefghijklmnopqrstuvwxyz012345?token=secret#part"
        )
        self.assertEqual(result, "https://example.com/seat/<redacted>")

    def test_candidate_paths_do_not_expose_seat_token_value(self):
        matches = find_candidate_paths(_FakeResponse.json())
        serialized = json.dumps(matches, ensure_ascii=False)
        self.assertIn("availableCount", serialized)
        self.assertIn("number=3", serialized)
        self.assertIn("AVAILABLE", serialized)
        self.assertNotIn("this-token-must-not-be-logged", serialized)

    def test_response_listener_keeps_only_sanitized_structure(self):
        messages = []
        diagnostics = SeatResponseDiagnostics(print_fn=messages.append)
        diagnostics.arm()
        diagnostics.handle_response(_FakeResponse())
        self.assertEqual(diagnostics.records, [])
        diagnostics.drain()

        self.assertEqual(len(diagnostics.records), 1)
        serialized = json.dumps(diagnostics.records, ensure_ascii=False)
        self.assertNotIn("access_token", serialized)
        self.assertNotIn("secret", serialized)
        self.assertNotIn("this-token-must-not-be-logged", serialized)

    def test_manual_completion_only_waits_until_input_is_hidden(self):
        image_input = _FakeInput()
        page = _FakePage(image_input)

        result = wait_for_manual_image_completion(
            page,
            "input",
            timeout_ms=1234,
            print_fn=lambda _message: None,
        )

        self.assertTrue(result)
        self.assertEqual(
            image_input.wait_calls,
            [{"state": "hidden", "timeout": 1234}],
        )
        self.assertEqual(page.waited_ms, [1000])

    def test_manual_completion_timeout_prevents_diagnostics(self):
        image_input = _FakeInput(timeout=True)
        page = _FakePage(image_input)

        result = wait_for_manual_image_completion(
            page,
            "input",
            timeout_ms=1,
            print_fn=lambda _message: None,
        )

        self.assertFalse(result)
        self.assertEqual(page.waited_ms, [])

    def test_save_diagnostics_overwrites_fixed_json_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "seat_diagnostics.json"
            saved = save_seat_diagnostics(
                [{"url": "https://example.com/seat"}],
                {"frames": []},
                path,
            )

            payload = json.loads(saved.read_text(encoding="utf-8"))
            self.assertEqual(payload["network"][0]["url"], "https://example.com/seat")
            self.assertEqual(payload["dom"], {"frames": []})

    def test_dom_report_counts_candidates_without_exposing_seat_identifiers(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content(
                    """
                    <div class="seat available" data-seat-id="private-seat-123"
                         data-status="AVAILABLE" aria-label="1층 A구역 1번 좌석"></div>
                    <svg><circle class="seat disabled" fill="#cccccc"></circle></svg>
                    <canvas></canvas>
                    """
                )

                messages = []
                report = inspect_seat_page(page, print_fn=messages.append)
            finally:
                browser.close()

        counts = report["frames"][0]["counts"]
        self.assertEqual(counts["canvas"], 1)
        self.assertEqual(counts["svg"], 1)
        self.assertEqual(counts["data_seat_id"], 1)
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("private-seat-123", serialized)
        self.assertNotIn("1층 A구역 1번 좌석", serialized)
        self.assertIn("AVAILABLE", serialized)


if __name__ == "__main__":
    unittest.main()
