"""
4/30 SportsBooking 클릭 후 좌석 페이지 구조 확인용
"""
import configparser
import random
from playwright.sync_api import sync_playwright

config = configparser.ConfigParser()
config.read("config.ini")
url = config['Doosan']['reserve_url']
login_id = config['Doosan']['login_id']
login_pw = config['Doosan']['login_pw']
target_date = config['Doosan']['target_date']   # 예: "2026.04.30"


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


def date_matches(row_text: str, target: str) -> bool:
    """target='2026.04.30' → 페이지 '04.30' 패턴 매칭"""
    parts = target.split(".")
    if len(parts) != 3:
        return target in row_text
    _, month, day = parts
    import re
    m = re.search(r'(\d{2})\.(\d{2})', row_text)
    if m:
        return int(m.group(1)) == int(month) and int(m.group(2)) == int(day)
    return False


with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(locale="ko-KR")
    page = context.new_page()

    # 1. 로그인
    page.goto(url, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(2000)
    if page.locator('#id').is_visible(timeout=3000):
        page.fill('#id', login_id)
        page.wait_for_timeout(300)
        page.fill('#pw', login_pw)
        page.wait_for_timeout(300)
        human_click(page, page.locator('button.btn-bg.bg-primary').first)
        page.wait_for_load_state("networkidle", timeout=15000)
        page.wait_for_timeout(2000)
        print("로그인 완료")

    # 2. 티켓예매 → 새창
    btn = page.locator('button.btn-bg.bg-primary:text-is("티켓예매")').first
    with context.expect_page(timeout=8000) as new_page_info:
        btn.click()
    ip = new_page_info.value
    ip.wait_for_load_state("networkidle", timeout=30000)
    ip.wait_for_timeout(2000)
    print(f"Interpark: {ip.url}")

    # 3. 팝업 JS로 닫기
    ip.evaluate("$('#AuthLayer').hide()")
    ip.wait_for_timeout(1000)
    ip.screenshot(path="inspect_popup_hidden.png")
    print("팝업 숨김 완료")

    # 4. 4/30 행에서 SportsBooking 링크 클릭
    target_row = None
    for row in ip.locator("tr").all():
        try:
            txt = row.inner_text()
        except Exception:
            continue
        if date_matches(txt, target_date):
            target_row = row
            print(f"대상 행 발견: {txt.strip()[:80]}")
            break

    if target_row is None:
        print(f"ERROR: {target_date} 경기 행을 찾지 못함")
        input("Enter로 종료")
        browser.close()
        exit()

    booking_link = target_row.locator('a[onclick*="SportsBooking"]').first
    print(f"SportsBooking 링크 발견: {booking_link.count() or 'count unavailable'}")

    # 새창 여부 확인하며 클릭
    try:
        with context.expect_page(timeout=6000) as seat_page_info:
            booking_link.click()
        seat_page = seat_page_info.value
        seat_page.wait_for_load_state("networkidle", timeout=30000)
        seat_page.wait_for_timeout(2000)
        print(f"좌석 새창: {seat_page.url}")
    except Exception:
        ip.wait_for_load_state("networkidle", timeout=15000)
        ip.wait_for_timeout(2000)
        print(f"동일창 이동: {ip.url}")
        seat_page = ip

    # 6. 좌석 페이지 팝업 닫기 (fnBookNoticeShowHide JS 호출)
    try:
        seat_page.evaluate("fnBookNoticeShowHide('')")
        seat_page.wait_for_timeout(1500)
        print("좌석 페이지 팝업 닫기 완료")
    except Exception as e:
        print(f"팝업 닫기 실패(무시): {e}")

    # 7. ifrmSeat 프레임 탐색
    seat_frame = seat_page.frame(name="ifrmSeat")
    if seat_frame is None:
        print("ERROR: ifrmSeat 프레임을 찾지 못함")
    else:
        print(f"ifrmSeat URL: {seat_frame.url}")
        # ifrmSeat 로딩 대기
        seat_page.wait_for_timeout(2000)

        # "잠깐 접어두기" 클릭
        try:
            clicked = seat_frame.evaluate("""
                () => {
                    const all = Array.from(document.querySelectorAll('a, button, span, td, div'));
                    const el = all.find(e => e.textContent.trim() === '잠깐 접어두기');
                    if (el) { el.click(); return el.outerHTML.substring(0, 200); }
                    return null;
                }
            """)
            seat_page.wait_for_timeout(1500)
            print(f"잠깐 접어두기 클릭 결과: {clicked}")
        except Exception as e:
            print(f"잠깐 접어두기 실패: {e}")

        seat_page.screenshot(path="inspect_seat_frame.png")
        print("\n=== seat_frame 스크린샷 저장 ===")

        # iframe 내부 구조 출력
        print("\n--- ifrmSeat tr 전체 ---")
        for i, tr in enumerate(seat_frame.locator("tr").all()):
            try:
                txt = tr.inner_text().strip().replace("\n", " | ")
                if txt:
                    print(f"  [tr#{i}] {txt[:200]}")
            except Exception:
                pass

        print("\n--- ifrmSeat a/button 태그 ---")
        for loc in seat_frame.locator("a, button").all():
            try:
                txt = loc.inner_text().strip()
                href = loc.get_attribute("href") or ""
                onclick = loc.get_attribute("onclick") or ""
                cls = loc.get_attribute("class") or ""
                if txt or onclick:
                    print(f"  text={txt!r}  href={href!r}  onclick={onclick[:80]!r}  class={cls!r}")
            except Exception:
                pass

        print("\n--- ifrmSeat onclick 요소 ---")
        for loc in seat_frame.locator("[onclick]").all():
            try:
                txt = loc.inner_text().strip()
                onclick = loc.get_attribute("onclick") or ""
                tag = loc.evaluate("el => el.tagName")
                print(f"  <{tag}> text={txt[:60]!r}  onclick={onclick[:100]!r}")
            except Exception:
                pass

        print("\n--- ifrmSeat div/td (3~200자) ---")
        for sel in ["td", "div"]:
            for loc in seat_frame.locator(sel).all():
                try:
                    txt = loc.inner_text().strip()
                    cls = loc.get_attribute("class") or ""
                    i_d = loc.get_attribute("id") or ""
                    if 3 < len(txt) < 200:
                        print(f"  <{sel}> id={i_d!r} class={cls!r}  text={txt[:150]!r}")
                except Exception:
                    pass


    seat_page.screenshot(path="inspect_seat_page.png")
    print("=== seat_page 스크린샷 저장 ===\n")

    # 5. 좌석 페이지 구조 출력
    print("--- 모든 tr ---")
    for i, row in enumerate(seat_page.locator("tr").all()):
        try:
            txt = row.inner_text().strip().replace("\n", " | ")
            if txt:
                print(f"  [tr#{i}] {txt[:200]}")
        except Exception:
            pass

    print("\n--- a 태그 ---")
    for loc in seat_page.locator("a").all():
        try:
            txt = loc.inner_text().strip()
            href = loc.get_attribute("href") or ""
            onclick = loc.get_attribute("onclick") or ""
            cls = loc.get_attribute("class") or ""
            if txt or onclick:
                print(f"  text={txt!r}  href={href!r}  onclick={onclick[:80]!r}  class={cls!r}")
        except Exception:
            pass

    print("\n--- onclick 요소 ---")
    for loc in seat_page.locator("[onclick]").all():
        try:
            txt = loc.inner_text().strip()
            onclick = loc.get_attribute("onclick") or ""
            cls = loc.get_attribute("class") or ""
            tag = loc.evaluate("el => el.tagName")
            print(f"  <{tag}> text={txt[:60]!r}  onclick={onclick[:100]!r}  class={cls!r}")
        except Exception:
            pass

    # 이미지 버튼 등 src/alt 속성도 출력
    print("\n--- img 태그 (alt/onclick 있는 것) ---")
    for loc in seat_page.locator("img").all():
        try:
            alt = loc.get_attribute("alt") or ""
            src = loc.get_attribute("src") or ""
            onclick = loc.get_attribute("onclick") or ""
            if alt or onclick:
                print(f"  alt={alt!r}  onclick={onclick[:80]!r}  src={src[-50:]!r}")
        except Exception:
            pass

    print("\n--- 좌석 관련 div/td (5~200자) ---")
    for sel in ["td", "div"]:
        for loc in seat_page.locator(sel).all():
            try:
                txt = loc.inner_text().strip()
                cls = loc.get_attribute("class") or ""
                i_d = loc.get_attribute("id") or ""
                if 3 < len(txt) < 200:
                    print(f"  <{sel}> id={i_d!r} class={cls!r}  text={txt[:150]!r}")
            except Exception:
                pass

    print("\nEnter를 누르면 종료합니다.")
    input()
    browser.close()
