"""
26012666(웨이브투어스) 상세페이지는 "잔여석 안내 서비스를 제공하지 않습니다"라 예매하기 클릭 이후
로그인(카카오) -> 좌석/인원선택 단계까지 들어가야 잔여석을 확인할 수 있는지 조사하는 1회성 스크립트.

보안 유의: login_id/login_pw 값 자체는 절대 print/screenshot 하지 않는다.
전체 페이지는 저장하지 않고 image의 실제 문자 그래픽 영역만 저장한다.
"""
import configparser
import random
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from image_text import extract_image_text_with_codex
from seat_diagnostics import (
    SeatResponseDiagnostics,
    inspect_seat_page,
    save_seat_diagnostics,
    wait_for_manual_image_completion,
)

config = configparser.ConfigParser()
config.read("config.ini")
url = config['Interpark']['url']
target_date = config['Interpark']['target_date']  # "2026.12.19"
login_id = config['Interpark']['login_id']
login_pw = config['Interpark']['login_pw']

# monitor.py의 InterparkMonitor와 동일한 스텔스 설정 (봇 탐지로 인한 클릭 무반응 방지)
_stealth = Stealth(
    navigator_languages_override=("ko-KR", "ko", "en-US", "en"),
    navigator_platform_override="MacIntel",
    navigator_vendor_override="Google Inc.",
    navigator_webdriver=True,
    webgl_vendor_override="Apple",
    webgl_renderer_override="Apple M1",
)


def human_click(page, locator):
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


def log_dialog(dialog):
    print(f"[DIALOG] type={dialog.type} message={dialog.message!r}")
    dialog.dismiss()


IMAGE_TITLE = "화면의 문자를 입력해주세요"
IMAGE_INPUT_SELECTOR = 'input[placeholder*="화면의 문자"]:visible'
IMAGE_MIN_WIDTH = 100
IMAGE_MIN_HEIGHT = 30
IMAGE_MAX_HEIGHT = 160
IMAGE_MIN_ASPECT_RATIO = 1.5
IMAGE_MAX_ASPECT_RATIO = 10.0


def find_image_modal(page, timeout=10_000):
    """현재 보이는 image 입력창과 모달 locator를 반환한다."""
    image_input = page.locator(IMAGE_INPUT_SELECTOR).first
    try:
        image_input.wait_for(state="visible", timeout=timeout)
    except PlaywrightTimeoutError:
        return None

    # 접근성 속성이 있으면 그것을 우선하고, 없으면 입력창에서 가장 가까운
    # 제목/완료 버튼 포함 조상으로 올라간다.
    semantic = page.locator(
        '[role="dialog"]:visible, [aria-modal="true"]:visible'
    ).filter(has_text=IMAGE_TITLE)
    semantic_count = semantic.count()
    if semantic_count == 1:
        modal = semantic
    elif semantic_count > 1:
        print("보이는 image 모달 후보가 여러 개라 저장하지 않습니다.")
        return None
    else:
        modal = image_input.locator(
            "xpath=ancestor::*["
            "(self::div or self::section) "
            "and .//*[contains(normalize-space(.), '화면의 문자를 입력해주세요')] "
            "and .//*[normalize-space(.)='입력완료']"
            "][1]"
        )

    if modal.count() != 1 or not modal.is_visible():
        print("image 입력창은 발견했지만 모달 영역을 유일하게 특정하지 못했습니다.")
        return None

    complete_control = modal.locator(
        'button:has-text("입력완료"), [role="button"]:has-text("입력완료")'
    )
    if complete_control.count() == 0:
        print("image 모달에 입력완료 컨트롤이 없어 저장하지 않습니다.")
        return None

    return modal, image_input, complete_control


def find_image_visual(modal, image_input):
    """모달 안에서 실제 문자 그래픽(img/canvas/svg/CSS 배경)만 찾는다."""
    input_box = image_input.bounding_box()
    if not input_box:
        print("image 입력창의 위치를 계산하지 못했습니다.")
        return None

    descendants = modal.locator("*")
    metadata = descendants.evaluate_all(
        """
        elements => elements.map((element, index) => {
            const rect = element.getBoundingClientRect();
            const style = window.getComputedStyle(element);
            const tag = element.tagName.toUpperCase();
            const attributes = [
                element.id,
                element.getAttribute("class"),
                element.getAttribute("src"),
                element.getAttribute("alt"),
                element.getAttribute("aria-label"),
                element.getAttribute("data-testid"),
            ].filter(Boolean).join(" ").toLowerCase();

            return {
                index,
                tag,
                x: rect.x,
                y: rect.y,
                width: rect.width,
                height: rect.height,
                bottom: rect.bottom,
                visible: (
                    rect.width > 0 &&
                    rect.height > 0 &&
                    style.display !== "none" &&
                    style.visibility !== "hidden" &&
                    Number(style.opacity) !== 0
                ),
                isImageTag: tag === "IMG",
                isCanvas: tag === "CANVAS",
                isSvg: tag === "SVG",
                hasImageRole: element.getAttribute("role") === "img",
                hasBackgroundImage: (
                    style.backgroundImage !== "none" &&
                    style.backgroundImage.includes("url(")
                ),
                hasImageHint: /captcha|capcha|security|verify|verification|\ubb38\uc790/.test(attributes),
                insideControl: Boolean(element.closest('button, a, [role="button"]')),
            };
        })
        """
    )

    input_center_x = input_box["x"] + input_box["width"] / 2
    candidates = []
    for item in metadata:
        if not item["visible"]:
            continue
        if not (
            item["isImageTag"]
            or item["isCanvas"]
            or item["isSvg"]
            or item["hasImageRole"]
            or item["hasBackgroundImage"]
        ):
            continue
        if item["insideControl"] and not item["hasImageHint"]:
            continue
        if item["bottom"] > input_box["y"] + 2:
            continue
        if item["width"] < IMAGE_MIN_WIDTH or item["height"] < IMAGE_MIN_HEIGHT:
            continue
        if item["height"] > IMAGE_MAX_HEIGHT or item["width"] > input_box["width"] * 1.05:
            continue

        aspect_ratio = item["width"] / item["height"]
        if not IMAGE_MIN_ASPECT_RATIO <= aspect_ratio <= IMAGE_MAX_ASPECT_RATIO:
            continue

        center_x = item["x"] + item["width"] / 2
        center_distance = abs(center_x - input_center_x)
        if center_distance > input_box["width"] * 0.35:
            continue

        type_score = 0
        if item["isCanvas"]:
            type_score = 50
        elif item["isImageTag"]:
            type_score = 40
        elif item["hasBackgroundImage"]:
            type_score = 35
        elif item["isSvg"]:
            type_score = 30
        elif item["hasImageRole"]:
            type_score = 20

        hint_score = 100 if item["hasImageHint"] else 0
        vertical_gap = max(0, input_box["y"] - item["bottom"])
        item["score"] = hint_score + type_score - center_distance / 10 - vertical_gap / 100
        candidates.append(item)

    if not candidates:
        print("image 모달은 찾았지만 문자 영역을 특정하지 못했습니다.")
        return None

    candidates.sort(key=lambda item: item["score"], reverse=True)
    best = candidates[0]
    print(
        "image 문자 영역 선택: "
        f"{best['tag']} {best['width']:.0f}x{best['height']:.0f}px"
    )
    return descendants.nth(best["index"])


def save_image_if_visible(page, output_dir="images", timeout=10_000):
    """문자 image가 보이면 실제 문자 그래픽 영역만 PNG로 저장한다."""
    found = find_image_modal(page, timeout=timeout)
    if found is None:
        return None

    modal, image_input, complete_button = found
    image_visual = find_image_visual(modal, image_input)
    if image_visual is None:
        return None

    image_visual.scroll_into_view_if_needed()

    image_dir = Path(output_dir)
    image_dir.mkdir(parents=True, exist_ok=True)

    # 새 결과와 과거 결과를 혼동하지 않도록 저장 전에 이전 파일을 정리한다.
    image_path = image_dir / "image.png"
    image_path.unlink(missing_ok=True)
    for previous_image in image_dir.glob("image_*.png"):
        if previous_image.is_file():
            previous_image.unlink()

    image_visual.screenshot(
        path=str(image_path),
        animations="disabled",
        caret="hide",
    )
    return image_path.resolve(), image_input, complete_button


with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
        ],
    )
    context = browser.new_context(
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
    page.on("dialog", log_dialog)
    page.on("pageerror", lambda exc: print(f"[PAGEERROR] {exc}"))

    page.goto(url, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(random.randint(1500, 3000))

    try:
        loc = page.locator('button:has-text("닫기")').first
        if loc.is_visible(timeout=3000):
            human_click(page, loc)
    except Exception:
        pass

    day = target_date.split(".")[-1]
    day_loc = page.locator(
        'ul[data-view="days"] li:not(.disabled):not(.muted)',
        has_text=__import__("re").compile(f'^{day}$'),
    ).first
    human_click(page, day_loc)
    print(f"날짜 선택: {target_date}")

    labels = page.locator('.timeTableLabel')
    if labels.count() > 0:
        human_click(page, labels.first)
        print(f"회차 선택: {labels.first.inner_text()!r}")

    page.wait_for_timeout(1000)

    # 예매하기 버튼
    btn = page.locator('a.sideBtn:has-text("예매하기")').first
    print(f"예매하기 버튼 존재: {btn.count()}")

    try:
        with context.expect_page(timeout=8000) as new_page_info:
            human_click(page, btn)
        target_page = new_page_info.value
        target_page.wait_for_load_state("networkidle", timeout=30000)
        print(f"새창으로 이동: {target_page.url}")
    except Exception as e:
        print(f"새창 안 열림({type(e).__name__}) — 동일창 확인")
        page.wait_for_timeout(3000)
        target_page = page
        print(f"동일창 URL: {page.url}")

    target_page.on("dialog", log_dialog)
    target_page.wait_for_timeout(2000)
    print(f"현재 URL: {target_page.url}")
    print(f"현재 타이틀: {target_page.title()}")
    # 카카오로 시작하기 버튼 탐색
    kakao_loc = target_page.locator(':text("카카오")').first
    if kakao_loc.count() > 0:
        print(f"'카카오' 텍스트 요소 발견 (count={target_page.locator(':text(\"카카오\")').count()})")
        try:
            with context.expect_page(timeout=8000) as kakao_page_info:
                human_click(target_page, kakao_loc)
            login_page = kakao_page_info.value
            login_page.wait_for_load_state("networkidle", timeout=30000)
            print(f"카카오 로그인 새창: {login_page.url}")
        except Exception as e:
            print(f"카카오 클릭 후 새창 없음({type(e).__name__}) — 동일창 확인")
            target_page.wait_for_timeout(2000)
            login_page = target_page
            print(f"동일창 URL: {login_page.url}")
    else:
        print("'카카오' 텍스트 요소를 찾지 못함 — 현재 페이지가 이미 로그인 필요 상태가 아닐 수 있음")
        login_page = target_page

    login_page.wait_for_timeout(2000)
    print(f"로그인 페이지 URL: {login_page.url}")

    # 카카오 로그인 폼 필드 후보 탐색 (id/name 속성만 출력, 값은 절대 출력 안 함)
    print("\n--- input 요소 후보 (id/name/type만, value 출력 안함) ---")
    for loc in login_page.locator("input").all():
        try:
            print(f"  id={loc.get_attribute('id')!r} name={loc.get_attribute('name')!r} type={loc.get_attribute('type')!r} placeholder={loc.get_attribute('placeholder')!r}")
        except Exception:
            pass

    # 흔한 카카오 로그인 셀렉터 후보로 시도
    id_selectors = ["#loginId--1", "input[name='loginId']", "input[type='email']", "input[type='text']"]
    pw_selectors = ["#password--1", "input[name='password']", "input[type='password']"]

    id_field = None
    for sel in id_selectors:
        loc = login_page.locator(sel).first
        if loc.count() > 0 and loc.is_visible():
            id_field = loc
            print(f"아이디 필드로 선택된 셀렉터: {sel}")
            break

    pw_field = None
    for sel in pw_selectors:
        loc = login_page.locator(sel).first
        if loc.count() > 0 and loc.is_visible():
            pw_field = loc
            print(f"비밀번호 필드로 선택된 셀렉터: {sel}")
            break

    if id_field and pw_field:
        id_field.fill(login_id)
        pw_field.fill(login_pw)
        print("아이디/비밀번호 입력 완료 (값은 출력하지 않음)")

        submit = login_page.locator('button:has-text("로그인")').first
        if submit.count() == 0:
            submit = login_page.locator('button[type="submit"]').first

        if submit.count() > 0:
            try:
                with context.expect_page(timeout=8000) as after_login_info:
                    submit.click()
                result_page = after_login_info.value
                result_page.wait_for_load_state("networkidle", timeout=30000)
            except Exception:
                login_page.wait_for_timeout(3000)
                result_page = login_page
            result_page.wait_for_timeout(2000)
            print(f"로그인 시도 후 URL: {result_page.url}")
            # 로그인 확인 후, 다시 예매하기를 눌러 실제 좌석/인원선택 화면까지 진입 시도
            login_check = result_page.locator('text=/님$/').first
            print(f"로그인 상태로 보이는 요소('~님') 존재: {login_check.count() > 0}")

            # 좌석창을 열기 전에 listener를 연결해야 초기 fetch/XHR도 관찰할 수 있다.
            # query/header/cookie/본문은 저장하지 않고 JSON 구조만 기록한다.
            seat_network = SeatResponseDiagnostics().install(context)
            seat_network.arm()
            btn2 = result_page.locator('a.sideBtn:has-text("예매하기")').first
            if btn2.count() > 0:
                try:
                    with context.expect_page(timeout=8000) as seat_page_info:
                        human_click(result_page, btn2)
                    seat_page = seat_page_info.value
                    seat_page.wait_for_load_state("networkidle", timeout=30000)
                    print(f"좌석선택 새창: {seat_page.url}")
                except Exception as e:
                    print(f"새창 안 열림({type(e).__name__}) — 동일창 확인")
                    result_page.wait_for_timeout(3000)
                    seat_page = result_page
                    print(f"동일창 URL: {seat_page.url}")

                seat_page.wait_for_timeout(2000)

                image_path, text_locator, complete_button = save_image_if_visible(seat_page)
                if image_path:
                    print(f"=== image 문자 영역 저장: {image_path} ===")
                    image_text = extract_image_text_with_codex(image_path)
                    if image_text:
                        print(f"=== image 텍스트 추출 결과: {image_text!r} ===")
                        text_locator.fill(image_text)
                        complete_button.click()
                else:
                    print("image 모달이 보이지 않거나 저장하지 못했습니다.")

                manual_ready = wait_for_manual_image_completion(
                    seat_page,
                    IMAGE_INPUT_SELECTOR,
                )
                seat_network.drain()
                seat_network.uninstall()
                dom_report = None
                if manual_ready:
                    print(f"URL: {seat_page.url}")
                    print(f"타이틀: {seat_page.title()}")
                    dom_report = inspect_seat_page(seat_page)

                diagnostics_path = save_seat_diagnostics(
                    seat_network.records,
                    dom_report,
                )
                print(f"=== 좌석 구조 진단 저장: {diagnostics_path} ===")
            else:
                print("재클릭용 예매하기 버튼을 찾지 못함")
        else:
            print("로그인 제출 버튼을 찾지 못함")
    else:
        print("아이디/비밀번호 필드를 자동으로 찾지 못함 — 수동 확인 필요")

    print("\nEnter를 누르면 종료합니다.")
    input()
    browser.close()
