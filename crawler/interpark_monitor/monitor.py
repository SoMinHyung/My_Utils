import re
import random
import logging
from playwright.sync_api import sync_playwright, Browser, Page, Playwright
from playwright_stealth import Stealth

from models import SeatInfo, ShowStatus

logger = logging.getLogger(__name__)

# macOS + Chrome + 한국어 환경에 맞춘 stealth 설정
_stealth = Stealth(
    navigator_languages_override=("ko-KR", "ko", "en-US", "en"),
    navigator_platform_override="MacIntel",
    navigator_vendor_override="Google Inc.",
    navigator_webdriver=True,       # navigator.webdriver를 false로 위장
    webgl_vendor_override="Apple",
    webgl_renderer_override="Apple M1",
)


class InterparkMonitor:
    def __init__(self, url: str, target_date: str, target_showtime: int, headless: bool = True):
        self.url = url
        self.target_date = target_date        # "2026.04.08"
        self.target_showtime = target_showtime  # 1-based index
        self.headless = headless

        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    @staticmethod
    def _human_click(page: Page, locator):
        """사람처럼 요소 위로 마우스를 이동한 후 랜덤 딜레이 후 클릭"""
        box = locator.bounding_box()
        if box:
            x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
            y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
            page.mouse.move(x, y, steps=random.randint(5, 15))
            page.wait_for_timeout(random.randint(100, 300))
            page.mouse.click(x, y)
        else:
            locator.click()
        page.wait_for_timeout(random.randint(500, 1500))

    def start_browser(self):
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )
        logger.info("브라우저 시작 (headless=%s)", self.headless)

    def stop_browser(self):
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()
        self._browser = None
        self._playwright = None
        logger.info("브라우저 종료")

    def check_seats(self) -> ShowStatus | None:
        """페이지 열기 -> 날짜 선택 -> 회차 선택 -> 좌석 정보 추출"""
        context = self._browser.new_context(
            viewport={"width": 1280 + random.randint(-20, 20), "height": 900 + random.randint(-20, 20)},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            geolocation={"latitude": 37.5665, "longitude": 126.978},
            permissions=["geolocation"],
        )
        _stealth.apply_stealth_sync(context)
        page = context.new_page()

        try:
            # 1. 페이지 로드
            page.goto(self.url, wait_until="networkidle", timeout=30000)
            logger.info("페이지 로드 완료: %s", self.url)
            page.wait_for_timeout(random.randint(1500, 3000))

            # 2. "예매 안내" 팝업 닫기
            self._dismiss_popup(page)

            # 3. 날짜 선택
            self._select_date(page)

            # 4. 회차 선택
            showtime_label = self._select_showtime(page)

            # 5. 좌석 정보 추출
            seats = self._extract_seats(page)

            return ShowStatus(
                show_date=self.target_date,
                showtime=showtime_label,
                seats=seats,
            )

        except Exception as e:
            logger.error("좌석 조회 실패: %s", e)
            try:
                page.screenshot(path="debug_screenshot.png")
                logger.info("디버그 스크린샷 저장: debug_screenshot.png")
            except Exception:
                pass
            return None
        finally:
            context.close()

    def _dismiss_popup(self, page: Page):
        """예매 안내 팝업이 있으면 닫기"""
        try:
            loc = page.locator('button:has-text("닫기")').first
            if loc.is_visible(timeout=3000):
                self._human_click(page, loc)
                logger.info("팝업 닫기 완료")
                return
        except Exception:
            pass
        logger.info("팝업 없음 — 계속 진행")

    def _select_date(self, page: Page):
        """캘린더에서 target_date 클릭"""
        page.wait_for_selector('.datepicker-panel', timeout=10000)

        parts = self.target_date.split(".")
        target_year = int(parts[0])
        target_month = int(parts[1])
        target_day = int(parts[2])

        # 캘린더 월 네비게이션
        self._navigate_calendar_month(page, target_year, target_month)

        # 날짜 클릭: ul[data-view="days"] 내의 비활성이 아닌 li
        day_str = str(target_day)
        day_loc = page.locator(
            f'ul[data-view="days"] li:not(.disabled):not(.muted)', has_text=re.compile(f'^{day_str}$')
        ).first
        self._human_click(page, day_loc)
        logger.info("날짜 선택: %s (day=%s)", self.target_date, day_str)

    def _navigate_calendar_month(self, page: Page, target_year: int, target_month: int):
        """캘린더를 target 월까지 이동"""
        for _ in range(12):
            header_text = page.locator('li[data-view="month current"]').inner_text()
            # "2026. 04" → year=2026, month=4
            match = re.search(r'(\d{4})\.\s*(\d{1,2})', header_text)
            if not match:
                logger.warning("캘린더 헤더 파싱 실패: %s", header_text)
                break

            cur_year = int(match.group(1))
            cur_month = int(match.group(2))
            logger.info("캘린더: %d.%02d (목표: %d.%02d)", cur_year, cur_month, target_year, target_month)

            if cur_year == target_year and cur_month == target_month:
                return

            # 목표가 미래면 ›, 과거면 ‹
            if (cur_year, cur_month) < (target_year, target_month):
                btn = page.locator('li[data-view="month next"]')
            else:
                btn = page.locator('li[data-view="month prev"]')

            self._human_click(page, btn)

    def _select_showtime(self, page: Page) -> str:
        """회차 선택 후 라벨 반환"""
        page.wait_for_selector('.timeTableLabel', timeout=10000)

        labels = page.locator('.timeTableLabel')
        if labels.count() >= self.target_showtime:
            target = labels.nth(self.target_showtime - 1)
            label = target.get_attribute("data-text") or target.inner_text().strip()

            # 이미 선택된 상태(is-toggled)가 아니면 클릭
            cls = target.get_attribute("class") or ""
            if "is-toggled" not in cls:
                self._human_click(page, target)

            logger.info("회차 선택: %s", label)
            page.wait_for_timeout(random.randint(500, 1000))
            return label

        logger.warning("회차를 찾지 못함 (count=%d, target=%d)", labels.count(), self.target_showtime)
        return f"{self.target_showtime}회"

    def _extract_seats(self, page: Page) -> list[SeatInfo]:
        """좌석 등급별 잔여석 추출"""
        page.wait_for_selector('.seatTableItem', timeout=10000)

        seats = []
        items = page.locator('.seatTableItem').all()
        for item in items:
            grade = item.locator('.seatTableName').inner_text().strip()
            status_text = item.locator('.seatTableStatus').inner_text().strip()

            # "0석", "3", "12" 등 파싱
            num_match = re.search(r'(\d+)', status_text)
            if num_match:
                available = int(num_match.group(1))
            else:
                available = 0

            seats.append(SeatInfo(grade=grade, available=available))

        logger.info("추출된 좌석 등급 수: %d", len(seats))
        return seats
