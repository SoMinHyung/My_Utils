import os
import re
import random
import logging
from playwright.sync_api import sync_playwright, Browser, Page, Playwright, BrowserContext, Frame
from playwright_stealth import Stealth

from models import SeatInfo, GameStatus

logger = logging.getLogger(__name__)

_stealth = Stealth(
    navigator_languages_override=("ko-KR", "ko", "en-US", "en"),
    navigator_platform_override="MacIntel",
    navigator_vendor_override="Google Inc.",
    navigator_webdriver=True,
    webgl_vendor_override="Apple",
    webgl_renderer_override="Apple M1",
)

_SEAT_PATTERN = re.compile(r'^(.+?)\s+(\d+)석$')


class DoosanMonitor:
    def __init__(
        self,
        reserve_url: str,
        login_id: str,
        login_pw: str,
        target_date: str,
        opponent: str,
        storage_state_path: str = "storage_state.json",
        headless: bool = True,
    ):
        self.reserve_url = reserve_url
        self.login_id = login_id
        self.login_pw = login_pw
        self.target_date = target_date
        self.opponent = opponent
        self.storage_state_path = storage_state_path
        self.headless = headless

        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    # ---------- Browser lifecycle ----------

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

    def _new_context(self) -> BrowserContext:
        kwargs = dict(
            viewport={"width": 1280 + random.randint(-20, 20), "height": 900 + random.randint(-20, 20)},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="ko-KR",
            timezone_id="Asia/Seoul",
        )
        if os.path.exists(self.storage_state_path):
            kwargs["storage_state"] = self.storage_state_path
            logger.info("저장된 세션 복원: %s", self.storage_state_path)
        context = self._browser.new_context(**kwargs)
        _stealth.apply_stealth_sync(context)
        return context

    @staticmethod
    def _human_click(page: Page, locator):
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

    # ---------- Main flow ----------

    def check_seats(self) -> GameStatus | None:
        context = self._new_context()
        page = context.new_page()

        try:
            # 1. 예매 페이지 이동
            page.goto(self.reserve_url, wait_until="networkidle", timeout=30000)
            logger.info("예매 페이지 로드: %s", self.reserve_url)
            page.wait_for_timeout(random.randint(1500, 3000))

            # 2. 로그인
            self._ensure_logged_in(page, context)

            # 3. 티켓예매 버튼 → Interpark 새창 (GoodsList.asp)
            ip_page = self._open_interpark(page, context)

            # 4. 경기 선택 → BookMain.asp 이동 (실제 상대팀명 반환)
            actual_opponent = self._select_game(ip_page)

            # 5. BookMain.asp 팝업 처리 + ifrmSeat 좌석 추출
            seats = self._extract_seats(ip_page)

            return GameStatus(
                game_date=self.target_date,
                opponent=actual_opponent,
                seats=seats,
            )

        except Exception as e:
            logger.error("좌석 조회 실패: %s", e)
            try:
                page.screenshot(path="debug_screenshot.png")
                logger.info("디버그 스크린샷 저장")
            except Exception:
                pass
            return None
        finally:
            try:
                context.storage_state(path=self.storage_state_path)
            except Exception:
                pass
            context.close()

    # ---------- Step implementations ----------

    def _is_logged_in(self, page: Page) -> bool:
        try:
            return not page.locator('#id').is_visible(timeout=2000)
        except Exception:
            return False

    def _ensure_logged_in(self, page: Page, context: BrowserContext):
        if self._is_logged_in(page):
            logger.info("이미 로그인됨 (세션 유효)")
            return

        logger.info("로그인 필요 — 자격증명 입력")
        page.wait_for_selector('#id', timeout=10000)
        page.fill('#id', self.login_id)
        page.wait_for_timeout(random.randint(200, 500))
        page.fill('#pw', self.login_pw)
        page.wait_for_timeout(random.randint(200, 500))
        self._human_click(page, page.locator('button.btn-bg.bg-primary').first)

        page.wait_for_load_state("networkidle", timeout=15000)
        page.wait_for_timeout(random.randint(1000, 2000))

        if not self._is_logged_in(page):
            raise RuntimeError("로그인 실패 — ID/PW 확인 필요")
        logger.info("로그인 성공")

        context.storage_state(path=self.storage_state_path)
        logger.info("세션 저장: %s", self.storage_state_path)

    def _open_interpark(self, page: Page, context: BrowserContext) -> Page:
        """티켓예매 버튼 클릭 → Interpark 새창(GoodsList.asp) 반환."""
        btn = page.locator('button.btn-bg.bg-primary:text-is("티켓예매")').first
        with context.expect_page(timeout=8000) as new_page_info:
            btn.click()
        ip = new_page_info.value
        ip.wait_for_load_state("networkidle", timeout=30000)
        ip.wait_for_timeout(random.randint(1500, 2500))
        logger.info("Interpark 새창: %s", ip.url)

        # 인증번호 팝업 (#AuthLayer) JS로 숨기기
        try:
            ip.evaluate("$('#AuthLayer').hide()")
            ip.wait_for_timeout(1500)
            logger.info("인증 팝업 숨김")
        except Exception:
            pass

        return ip

    def _select_game(self, page: Page):
        """GoodsList.asp에서 target_date 경기 행의 SportsBooking 클릭 → BookMain.asp."""
        page.wait_for_selector("tr", timeout=15000, state="attached")

        for row in page.locator("tr").all():
            try:
                txt = row.inner_text()
            except Exception:
                continue
            if not self._date_matches(txt):
                continue

            # "두산 vs 삼성 (4.30)" 패턴에서 상대팀 추출
            opponent = self.opponent
            m = re.search(r'두산\s+vs\s+(.+?)\s*[\(\d]', txt)
            if m:
                opponent = m.group(1).strip()

            booking = row.locator('a[onclick*="SportsBooking"]').first
            logger.info("경기 행 발견: %s", txt.strip()[:60])
            booking.click()
            page.wait_for_load_state("networkidle", timeout=30000)
            page.wait_for_timeout(random.randint(1500, 2500))
            logger.info("BookMain 이동: %s", page.url)
            return opponent

        raise RuntimeError(
            f"경기를 찾지 못함 (target_date={self.target_date})"
        )

    def _extract_seats(self, page: Page) -> list[SeatInfo]:
        """BookMain.asp에서 팝업 처리 후 ifrmSeat 프레임의 좌석 정보 추출."""
        # BookMain 팝업 (예매 안내) 닫기
        try:
            page.evaluate("fnBookNoticeShowHide('')")
            page.wait_for_timeout(1000)
            logger.info("BookMain 팝업 닫기")
        except Exception:
            pass

        # ifrmSeat 프레임 대기 (loading.html → BookSeatGate.asp)
        seat_frame = self._wait_for_seat_frame(page)

        # 캡차 패널 접기
        try:
            seat_frame.evaluate("capchaHide()")
            page.wait_for_timeout(1000)
            logger.info("캡차 패널 접기")
        except Exception:
            pass

        # 좌석 링크 파싱: "좌석명 N석" 패턴
        seats: list[SeatInfo] = []
        for link in seat_frame.locator('a[href="#"]').all():
            try:
                txt = link.inner_text().strip()
            except Exception:
                continue
            m = _SEAT_PATTERN.match(txt)
            if m and "휠체어" not in m.group(1) and "테이블석" not in m.group(1):
                seats.append(SeatInfo(grade=m.group(1), available=int(m.group(2))))

        logger.info("추출된 좌석 등급 수: %d", len(seats))
        return seats

    def _wait_for_seat_frame(self, page: Page, timeout: float = 20.0) -> Frame:
        """ifrmSeat 프레임이 BookSeatGate.asp로 로드될 때까지 대기."""
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            frame = page.frame(name="ifrmSeat")
            if frame and "loading" not in frame.url:
                logger.info("ifrmSeat 로드 완료: %s", frame.url)
                frame.wait_for_load_state("networkidle", timeout=10000)
                return frame
            page.wait_for_timeout(500)
        raise RuntimeError("ifrmSeat 프레임 로드 타임아웃")

    def _date_matches(self, text: str) -> bool:
        """target_date='2026.04.30' → 페이지 '04.30' 패턴 매칭."""
        parts = self.target_date.split(".")
        if len(parts) != 3:
            return self.target_date in text
        _, month, day = parts
        m = re.search(r'(\d{2})\.(\d{2})', text)
        if m:
            return int(m.group(1)) == int(month) and int(m.group(2)) == int(day)
        return False
