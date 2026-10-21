import unittest
from datetime import datetime

from playwright.sync_api import sync_playwright

from interpark_monitor.seat_availability import (
    GradeAvailability,
    SeatGradeResponseCollector,
    ShowAvailability,
    change_onestop_schedule,
    format_availability_message,
    parse_grade_payload,
)


class _FakeRequest:
    def __init__(self, resource_type="xhr"):
        self.resource_type = resource_type


class _FakeResponse:
    def __init__(self, payload, *, url=None, status=200, resource_type="xhr"):
        self.url = url or "https://tickets.interpark.com/onestop/api/seats/grades"
        self.status = status
        self.request = _FakeRequest(resource_type)
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakePage:
    def wait_for_timeout(self, _milliseconds):
        pass


class SeatAvailabilityTests(unittest.TestCase):
    def test_parse_grade_payload(self):
        result = parse_grade_payload(
            [
                {"seatGradeName": "스탠딩석", "remainCount": 0},
                {"seatGradeName": "지정석", "remainCount": 3},
            ]
        )

        self.assertEqual(
            result,
            (
                GradeAvailability("스탠딩석", 0),
                GradeAvailability("지정석", 3),
            ),
        )

    def test_invalid_payload_is_unknown_not_sold_out(self):
        self.assertIsNone(
            parse_grade_payload(
                [{"seatGradeName": "지정석", "remainCount": None}]
            )
        )

    def test_collector_uses_latest_valid_grade_response(self):
        collector = SeatGradeResponseCollector()
        collector.arm()
        collector._handle_response(
            _FakeResponse([{"seatGradeName": "지정석", "remainCount": 2}])
        )
        collector._handle_response(_FakeResponse(ValueError("invalid json")))

        result = collector.wait_for_latest(
            _FakePage(),
            timeout_ms=10,
            settle_ms=0,
        )

        self.assertEqual(result, (GradeAvailability("지정석", 2),))

    def test_collector_ignores_unrelated_response(self):
        collector = SeatGradeResponseCollector()
        collector.arm()
        collector._handle_response(
            _FakeResponse(
                [{"seatGradeName": "지정석", "remainCount": 9}],
                url="https://tickets.interpark.com/onestop/api/seats/maps",
            )
        )

        self.assertIsNone(
            collector.wait_for_latest(
                _FakePage(),
                timeout_ms=0,
                settle_ms=0,
            )
        )

    def test_format_two_dates_in_one_message(self):
        message = format_availability_message(
            [
                ShowAvailability(
                    "2026.12.19",
                    "1회 18:00",
                    (GradeAvailability("지정석", 0),),
                ),
                ShowAvailability(
                    "2026.12.20",
                    "17:00",
                    (GradeAvailability("지정석", 2),),
                ),
            ],
            reserve_url="https://tickets.example/goods/1",
            checked_at=datetime(2026, 9, 2, 12, 34, 56),
        )

        self.assertIn("2026.12.19 | 1회 18:00", message)
        self.assertIn("지정석: 매진 (0석)", message)
        self.assertIn("2026.12.20 | 17:00", message)
        self.assertIn("지정석: 2석", message)
        self.assertIn("전체 잔여좌석: 2석", message)

    def test_failed_date_is_reported_as_failure(self):
        message = format_availability_message(
            [ShowAvailability("2026.12.20", "1회", None)],
            reserve_url="https://tickets.example/goods/1",
        )

        self.assertIn("조회 실패", message)
        self.assertNotIn("매진", message)

    def test_change_schedule_selects_active_slide_date_and_new_showtime(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content(
                    """
                    <div id="schedule-header">2026.12.19(토) 6:00 PM</div>
                    <button id="hidden-change" style="display:none">일정변경</button>
                    <button id="open-schedule">일정변경</button>
                    <div class="ScheduleContent_body__fixture" id="layer"
                         style="display:none">
                      <h2>일정 선택</h2>
                      <h3 class="EntCalendar_month__fixture">2026. 12</h3>
                      <button id="swiperButtonPrev">이전</button>
                      <button id="swiperButtonNext">다음</button>
                      <div class="swiper-slide">
                        <button aria-pressed="false">
                          <span class="EntCalendar_number__fixture">20</span>
                        </button>
                      </div>
                      <div class="swiper-slide swiper-slide-active">
                        <button id="target-day" aria-pressed="false">
                          <span class="EntCalendar_number__fixture">20</span>
                          <span>오늘</span>
                        </button>
                      </div>
                      <div id="times">
                        <button role="row" aria-selected="true">
                          <span class="TimeBlock_clock__fixture">6:00 PM</span>
                        </button>
                      </div>
                      <button id="apply"
                              class="ControlButton_isUIDisabled__fixture">변경하기</button>
                    </div>
                    <script>
                      const layer = document.querySelector('#layer');
                      const times = document.querySelector('#times');
                      const apply = document.querySelector('#apply');
                      document.querySelector('#open-schedule').onclick = () => {
                        layer.style.display = 'block';
                      };
                      document.querySelector('#target-day').onclick = (event) => {
                        event.currentTarget.setAttribute('aria-pressed', 'true');
                        setTimeout(() => {
                          times.innerHTML = `
                            <button role="row" aria-selected="false">
                              <span class="TimeBlock_clock__fixture">5:00 PM</span>
                            </button>`;
                        }, 50);
                      };
                      times.onclick = (event) => {
                        const button = event.target.closest('button[role="row"]');
                        if (!button) return;
                        button.setAttribute('aria-selected', 'true');
                        apply.className = '';
                      };
                      apply.onclick = () => {
                        if (apply.className.includes('isUIDisabled')) return;
                        document.querySelector('#schedule-header').textContent =
                          '2026.12.20(일) 5:00 PM';
                        layer.style.display = 'none';
                      };
                    </script>
                    """
                )
                events = []

                def click_fn(_page, locator):
                    events.append(f"click:{locator.inner_text().strip()}")
                    locator.click()

                showtime = change_onestop_schedule(
                    page,
                    "2026.12.20",
                    1,
                    click_fn=click_fn,
                    before_submit=lambda: events.append("before_submit"),
                    timeout_ms=3_000,
                )
                final_content = page.content()
            finally:
                browser.close()

        self.assertEqual(showtime, "5:00 PM")
        self.assertIn("2026.12.20(일) 5:00 PM", final_content)
        self.assertLess(events.index("before_submit"), events.index("click:변경하기"))

    def test_change_schedule_rejects_zero_showtime_index(self):
        with self.assertRaisesRegex(ValueError, "1 이상"):
            change_onestop_schedule(
                object(),
                "2026.12.20",
                0,
                click_fn=lambda *_: None,
            )


if __name__ == "__main__":
    unittest.main()
