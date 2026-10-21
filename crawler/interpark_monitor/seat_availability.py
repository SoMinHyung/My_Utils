"""NOL 좌석 페이지에서 날짜별 등급 잔여석을 읽고 알림 문구를 만든다."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit


SEAT_GRADES_PATH = "/onestop/api/seats/grades"


@dataclass(frozen=True)
class GradeAvailability:
    name: str
    remaining: int


@dataclass(frozen=True)
class ShowAvailability:
    show_date: str
    showtime: str
    grades: tuple[GradeAvailability, ...] | None

    @property
    def total_remaining(self) -> int | None:
        if self.grades is None:
            return None
        return sum(grade.remaining for grade in self.grades)


def parse_grade_payload(payload) -> tuple[GradeAvailability, ...] | None:
    """`/api/seats/grades` 응답을 검증해 등급별 잔여석으로 변환한다."""
    if not isinstance(payload, list) or not payload:
        return None

    grades: list[GradeAvailability] = []
    for item in payload:
        if not isinstance(item, dict):
            return None

        name = item.get("seatGradeName")
        remaining = item.get("remainCount")
        if not isinstance(name, str) or not name.strip():
            return None
        if isinstance(remaining, bool) or not isinstance(remaining, int):
            return None
        if remaining < 0:
            return None

        grades.append(
            GradeAvailability(
                name=name.strip(),
                remaining=remaining,
            )
        )

    return tuple(grades)


class SeatGradeResponseCollector:
    """좌석 등급 API 응답만 보관했다가 UI 동작이 끝난 뒤 파싱한다."""

    def __init__(self):
        self._context = None
        self._listener = self._handle_response
        self._armed = False
        self._pending = []

    def install(self, context) -> "SeatGradeResponseCollector":
        self._context = context
        context.on("response", self._listener)
        return self

    def arm(self) -> None:
        self._pending = []
        self._armed = True

    def _handle_response(self, response) -> None:
        if not self._armed or len(self._pending) >= 10:
            return
        try:
            path = urlsplit(response.url).path.rstrip("/")
            if path != SEAT_GRADES_PATH:
                return
            if response.request.resource_type not in {"xhr", "fetch"}:
                return
            if not 200 <= response.status < 300:
                return
            self._pending.append(response)
        except Exception:
            return

    def _drain_latest(self) -> tuple[GradeAvailability, ...] | None:
        self._armed = False
        pending = self._pending
        self._pending = []
        for response in reversed(pending):
            try:
                grades = parse_grade_payload(response.json())
            except Exception:
                continue
            if grades is not None:
                return grades
        return None

    def wait_for_latest(
        self,
        page,
        *,
        timeout_ms: int = 15_000,
        settle_ms: int = 300,
    ) -> tuple[GradeAvailability, ...] | None:
        deadline = time.monotonic() + timeout_ms / 1000
        latest_valid = None
        parsed_count = 0
        last_response_at = None

        while time.monotonic() < deadline:
            while parsed_count < len(self._pending):
                response = self._pending[parsed_count]
                parsed_count += 1
                last_response_at = time.monotonic()
                try:
                    parsed = parse_grade_payload(response.json())
                except Exception:
                    continue
                if parsed is not None:
                    latest_valid = parsed

            if (
                latest_valid is not None
                and last_response_at is not None
                and time.monotonic() - last_response_at >= settle_ms / 1000
            ):
                break
            page.wait_for_timeout(100)

        self._armed = False
        self._pending = []
        return latest_valid

    def uninstall(self) -> None:
        self._armed = False
        self._pending = []
        if self._context is not None:
            try:
                self._context.remove_listener("response", self._listener)
            except Exception:
                pass
        self._context = None


def _first_visible(locator, description: str):
    for index in range(locator.count()):
        candidate = locator.nth(index)
        if candidate.is_visible():
            return candidate
    raise RuntimeError(f"보이는 {description} 요소를 찾지 못했습니다.")


def _wait_for_first_visible(locator, page, description: str, timeout_ms: int):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        try:
            return _first_visible(locator, description)
        except RuntimeError:
            page.wait_for_timeout(100)
    raise RuntimeError(f"보이는 {description} 요소를 찾지 못했습니다.")


def _wait_for_attribute(
    locator,
    page,
    attribute: str,
    expected: str,
    description: str,
    timeout_ms: int,
) -> None:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if locator.get_attribute(attribute) == expected:
            return
        page.wait_for_timeout(100)
    raise RuntimeError(f"{description} 선택 상태를 확인하지 못했습니다.")


def _visible_time_signature(layer) -> tuple[str, ...]:
    buttons = layer.locator('button[role="row"]')
    return tuple(
        buttons.nth(index).inner_text().strip()
        for index in range(buttons.count())
        if buttons.nth(index).is_visible()
    )


def _wait_for_time_refresh(layer, page, previous: tuple[str, ...], timeout_ms: int):
    """날짜 변경 뒤 React가 이전 회차 목록을 교체할 때까지 잠시 기다린다."""
    deadline = time.monotonic() + min(timeout_ms, 2_000) / 1000
    while time.monotonic() < deadline:
        current = _visible_time_signature(layer)
        if current and current != previous:
            return
        page.wait_for_timeout(100)


def _wait_for_time_buttons(layer, page, showtime_index: int, timeout_ms: int):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        time_buttons = layer.locator('button[role="row"]')
        visible = [
            time_buttons.nth(index)
            for index in range(time_buttons.count())
            if time_buttons.nth(index).is_visible()
        ]
        if len(visible) >= showtime_index:
            return visible[showtime_index - 1]
        page.wait_for_timeout(200)
    raise RuntimeError(f"{showtime_index}회차 버튼을 찾지 못했습니다.")


def change_onestop_schedule(
    page,
    target_date: str,
    showtime_index: int,
    *,
    click_fn,
    before_submit=None,
    timeout_ms: int = 15_000,
) -> str:
    """일정변경 layer에서 날짜/회차를 선택하고 표시된 시간 문자열을 반환한다."""
    if showtime_index < 1:
        raise ValueError("회차 번호는 1 이상이어야 합니다.")

    try:
        target = datetime.strptime(target_date, "%Y.%m.%d")
    except ValueError as exc:
        raise ValueError(f"지원하지 않는 날짜 형식: {target_date}") from exc

    change_button = _first_visible(
        page.get_by_role("button", name="일정변경", exact=True),
        "일정변경 버튼",
    )
    click_fn(page, change_button)

    title = _wait_for_first_visible(
        page.get_by_role("heading", name="일정 선택", exact=True),
        page,
        "일정 선택 제목",
        timeout_ms,
    )
    layer = title.locator(
        "xpath=ancestor::div[contains(@class, 'ScheduleContent_body')][1]"
    )
    if layer.count() != 1:
        layer = title.locator(
            "xpath=ancestor::div[.//button[normalize-space(.)='변경하기']][1]"
        )
    if layer.count() != 1:
        raise RuntimeError("일정 선택 영역을 특정하지 못했습니다.")

    target_month = (target.year, target.month)
    for _ in range(24):
        month_heading = _first_visible(
            layer.locator('h3[class*="EntCalendar_month"]'),
            "달력 월 제목",
        )
        month_text = month_heading.inner_text().strip()
        match = re.search(r"(\d{4})\s*(?:\.|년)\s*(\d{1,2})", month_text)
        if not match:
            raise RuntimeError(f"달력 월을 해석하지 못했습니다: {month_text!r}")
        current_month = (int(match.group(1)), int(match.group(2)))
        if current_month == target_month:
            break
        navigation = (
            layer.locator("#swiperButtonNext")
            if current_month < target_month
            else layer.locator("#swiperButtonPrev")
        )
        click_fn(page, _first_visible(navigation, "달력 이동 버튼"))
    else:
        raise RuntimeError(f"목표 월로 이동하지 못했습니다: {target_date}")

    day_pattern = re.compile(rf"^\s*{target.day}\s*$")
    day_numbers = layer.locator(
        '.swiper-slide-active span[class*="EntCalendar_number"]'
    ).filter(has_text=day_pattern)
    day_button = _first_visible(
        day_numbers.locator("xpath=ancestor::button[not(@disabled)][1]"),
        f"{target_date} 날짜 버튼",
    )
    previous_times = _visible_time_signature(layer)
    click_fn(page, day_button)
    _wait_for_attribute(
        day_button,
        page,
        "aria-pressed",
        "true",
        target_date,
        timeout_ms,
    )
    _wait_for_time_refresh(layer, page, previous_times, timeout_ms)

    time_button = _wait_for_time_buttons(
        layer,
        page,
        showtime_index,
        timeout_ms,
    )
    click_fn(page, time_button)
    _wait_for_attribute(
        time_button,
        page,
        "aria-selected",
        "true",
        f"{showtime_index}회차",
        timeout_ms,
    )
    clock = time_button.locator('[class*="TimeBlock_clock"]').first
    showtime = (
        clock.inner_text().strip()
        if clock.count() > 0
        else time_button.inner_text().strip().splitlines()[0]
    )

    modify_button = _first_visible(
        layer.get_by_role("button", name="변경하기", exact=True),
        "변경하기 버튼",
    )
    modify_class = modify_button.get_attribute("class") or ""
    if (
        not modify_button.is_enabled()
        or modify_button.get_attribute("aria-disabled") == "true"
        or "isUIDisabled" in modify_class
    ):
        raise RuntimeError("날짜/회차를 선택했지만 변경하기 버튼이 활성화되지 않았습니다.")
    if before_submit is not None:
        before_submit()
    click_fn(page, modify_button)
    title.wait_for(state="hidden", timeout=timeout_ms)

    date_pattern = re.compile(
        rf"{target.year}\.{target.month:02d}\.{target.day:02d}\s*\([^)]+\)"
    )
    _wait_for_first_visible(
        page.get_by_text(date_pattern),
        page,
        f"{target_date} 상단 일정",
        timeout_ms,
    )
    page.wait_for_timeout(1_000)
    return showtime


def format_availability_message(
    shows: list[ShowAvailability],
    *,
    reserve_url: str,
    checked_at: datetime | None = None,
) -> str:
    checked_at = checked_at or datetime.now()
    lines = [
        "[인터파크 티켓 잔여석]",
        f"조회시간: {checked_at.strftime('%Y-%m-%d %H:%M:%S')}",
    ]

    for show in shows:
        lines.extend(["", f"{show.show_date} | {show.showtime}"])
        if show.grades is None:
            lines.append("  조회 실패")
            continue

        for grade in show.grades:
            status = f"{grade.remaining}석" if grade.remaining > 0 else "매진 (0석)"
            lines.append(f"  {grade.name}: {status}")
        lines.append(f"  전체 잔여좌석: {show.total_remaining}석")

    lines.extend(["", reserve_url])
    return "\n".join(lines)
