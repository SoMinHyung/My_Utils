#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KOFIA 공지사항 게시판(m_17)을 감시해 특정 키워드 공지가 올라오면 텔레그램으로 알린다.

대상 공지: 매월 올라오는 "YYYY년 M월 삼성전자 등 및 SK하이닉스 시가총액 비중안내"
매칭 기준: 제목에 KEYWORDS 가 모두 포함되면 대상(월 문자열이 아니라 키워드로 매칭 → 매달 자동 대응).
중복 방지: 게시글 seq 기준(제목이 아님).

사용 예(공통 uv 환경, Python 3.12):
    uv run python kofia_notice_bot.py --test      # 텔레그램 연결 확인용 메시지 1건
    uv run python kofia_notice_bot.py --once       # 1회 폴링(첫 실행은 조용히 시드)
    uv run python kofia_notice_bot.py --loop        # 상시 감시(기본 60초 주기, ±10초 지터)
    uv run python kofia_notice_bot.py --loop --interval 120

봇 토큰/chat_id 는 crawler/secrets.ini 의 [Telegram] bot / chat_id 에서 공용으로 읽는다.
"""

import argparse
import html
import json
import logging
import os
import random
import re
import sys
import time

import requests

# crawler/ 를 import 경로에 추가 → 공용 텔레그램 알림기 사용
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.notifier import TelegramNotifier

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
LIST_URL = "https://www.kofia.or.kr/brd/m_17/list.do"
# 상세 페이지: seq 만으로도 정상 동작(HTTP 200) 확인됨 → 깔끔한 링크로 알림에 넣는다.
VIEW_URL_TMPL = "https://www.kofia.or.kr/brd/m_17/view.do?seq={seq}"

# 제목에 아래 키워드가 "모두" 포함되면 알림 대상. 필요 시 여기만 수정하면 된다.
KEYWORDS = ["삼성전자", "SK하이닉스"]

# 정부 서버가 기본 User-Agent 를 차단할 수 있어 브라우저 UA 를 사용한다.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

# 상태 파일은 CWD 가 아니라 스크립트 위치 기준 절대경로로 둔다
# (cron/launchd 처럼 CWD 가 달라도 상태 파일이 하나로 유지되도록).
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "seen_seqs.json")

DEFAULT_INTERVAL = 60  # 폴링 기본 간격(초)
JITTER_SECONDS = 10    # 매 주기 ±지터(초): 규칙적 요청 완화용
HTTP_TIMEOUT = 20

log = logging.getLogger("kofia")


# ---------------------------------------------------------------------------
# KOFIA 목록 조회 / 파싱
# ---------------------------------------------------------------------------
def fetch_list_html():
    """목록 1페이지(최신순) HTML 을 반환한다. 서버 렌더링이라 단순 GET 으로 충분."""
    resp = requests.get(LIST_URL, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text


# 이 정규식은 KOFIA m_17 목록 페이지의 행 구조에 결합되어 있다:
#   <tr> ... <td class="first num">번호</td>
#            ... <span><a href="./view.do?seq=SEQ&...">\n</span>\n제목</a> ...
#            ... <td class="num">YYYY-MM-DD</td> ...  </tr>
# 제목 텍스트가 </span> 와 </a> "사이"에 오는 (다소 비정상적인) 마크업이라 아래처럼 잡는다.
_ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S)
_NUM_RE = re.compile(r'class="first num">\s*(\d+)')
_ANCHOR_RE = re.compile(r'<a\s+href="(\./view\.do\?seq=(\d+)[^"]*)"[^>]*>\s*</span>\s*(.*?)</a>', re.S)
_DATE_RE = re.compile(r'class="num">\s*(\d{4}-\d{2}-\d{2})')


def parse_posts(html_text):
    """목록 HTML 을 게시글 dict 리스트로 파싱한다.

    각 항목: {"no": str, "seq": str, "title": str, "date": str, "url": str}
    """
    posts = []
    for row in _ROW_RE.findall(html_text):
        anchor = _ANCHOR_RE.search(row)
        if not anchor:
            continue
        seq, raw_title = anchor.group(2), anchor.group(3)
        title = html.unescape(re.sub(r"\s+", " ", raw_title)).strip()
        if not title:
            continue
        num = _NUM_RE.search(row)
        date = _DATE_RE.search(row)
        posts.append({
            "no": num.group(1) if num else "",
            "seq": seq,
            "title": title,
            "date": date.group(1) if date else "",
            # seq 기반 깔끔한 상세 URL(불필요한 빈 파라미터 제거).
            "url": VIEW_URL_TMPL.format(seq=seq),
        })
    return posts


def matches(title):
    """제목에 KEYWORDS 가 모두 포함되면 True."""
    return all(kw in title for kw in KEYWORDS)


# ---------------------------------------------------------------------------
# 상세 페이지 본문(내용) 추출
# ---------------------------------------------------------------------------
MAX_BODY_CHARS = 1500  # 텔레그램 메시지 상한(4096자) 대비 본문 절단 길이

# 본문은 상세 페이지의 <div id="write"> 안에 들어 있다(<br/> 로 줄바꿈, 일부 글자는 숫자 엔티티).
_WRITE_RE = re.compile(r'<div id="write"[^>]*>(.*?)</div>', re.S)
# 상단 표에서 제목/작성일(목록에 없는 임의 seq 도 지원하려고 view 페이지에서 직접 파싱).
_VIEW_TITLE_RE = re.compile(r'>제목</th>\s*<td[^>]*>(.*?)</td>', re.S)
_VIEW_DATE_RE = re.compile(r'>작성일</th>\s*<td[^>]*>(.*?)</td>', re.S)


def fetch_view_html(seq):
    """상세 페이지(view.do?seq=…) HTML 을 반환한다."""
    resp = requests.get(VIEW_URL_TMPL.format(seq=seq),
                        headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text


def _clean_html_text(fragment):
    """HTML 조각 → 읽는 텍스트: <br> 은 줄바꿈, 태그 제거, 엔티티 복원, 빈 줄 정리."""
    text = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line and (not out or not out[-1]):  # 연속 빈 줄은 하나로
            continue
        out.append(line)
    return "\n".join(out).strip()


def extract_body(view_html, max_chars=MAX_BODY_CHARS):
    """상세 페이지 HTML 에서 본문(내용) 텍스트를 추출한다. 못 찾으면 빈 문자열."""
    m = _WRITE_RE.search(view_html)
    if not m:
        return ""
    body = _clean_html_text(m.group(1))
    if len(body) > max_chars:
        body = body[:max_chars].rstrip() + " …"
    return body


def parse_view_meta(view_html, seq):
    """상세 페이지에서 제목/작성일을 뽑아 post dict 로 만든다(목록에 없는 seq 도 지원)."""
    t = _VIEW_TITLE_RE.search(view_html)
    d = _VIEW_DATE_RE.search(view_html)
    return {
        "seq": str(seq),
        "title": _clean_html_text(t.group(1)) if t else "(제목 없음)",
        "date": _clean_html_text(d.group(1)).split(" ")[0] if d else "",
        "url": VIEW_URL_TMPL.format(seq=seq),
    }


# ---------------------------------------------------------------------------
# 상태 파일 (이미 알림 보낸 seq 집합)
# ---------------------------------------------------------------------------
def load_seen():
    if not os.path.exists(STATE_FILE):
        return set()
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return set(str(s) for s in json.load(fh))
    except (ValueError, OSError) as exc:
        log.warning("상태 파일을 읽지 못해 빈 집합으로 시작합니다: %s", exc)
        return set()


def save_seen(seen):
    """임시 파일에 쓰고 os.replace 로 원자적 교체(반쪽 쓰기 방지)."""
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(sorted(seen, key=int), fh, ensure_ascii=False, indent=0)
    os.replace(tmp, STATE_FILE)


# ---------------------------------------------------------------------------
# 텔레그램
# ---------------------------------------------------------------------------
_notifier = None


def send_telegram(text):
    """공용 알림기로 메시지 전송. 성공 True / 실패 False(로그 남김).

    반환값으로 seen 기록 여부를 결정하므로(전송 성공한 글만 seen 에 추가) bool 을 유지한다.
    자격증명은 crawler/secrets.ini 에서 읽는다.
    """
    global _notifier
    try:
        if _notifier is None:
            _notifier = TelegramNotifier()
    except Exception as exc:  # secrets.ini 누락 등
        log.error("%s", exc)
        return False
    return _notifier.send(text, disable_preview=False)


def format_message(post, body=""):
    """알림 메시지: 제목 + 작성일 + (본문) + 링크."""
    lines = [
        "\U0001F4E2 KOFIA 신규 공지",
        post["title"],
        "\U0001F5D3 작성일: {}".format(post["date"] or "-"),
    ]
    if body:
        lines.append("")
        lines.append(body)
    lines.append("")
    lines.append("\U0001F517 {}".format(post["url"]))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 폴링 1회
# ---------------------------------------------------------------------------
def _fetch_body(seq):
    """상세 본문을 가져온다. 실패해도 알림은 나가도록 예외를 삼키고 빈 문자열 반환."""
    try:
        return extract_body(fetch_view_html(seq))
    except Exception as exc:
        log.warning("본문 조회 실패(제목/링크만 전송): %s", exc)
        return ""


def run_once(seen, first_run, notify_on_first_run=False):
    """목록을 1회 조회해 신규 매칭 공지를 알린다.

    예외는 여기서 잡아 로그만 남기고 (루프가 죽지 않도록) 넘어간다.
    seen 집합은 in-place 로 갱신되고, 변경 시 상태 파일에 저장된다.
    """
    try:
        posts = parse_posts(fetch_list_html())
    except Exception as exc:  # 네트워크/파싱 등 모든 오류 → 다음 주기 재시도
        log.error("목록 조회/파싱 실패, 이번 주기 건너뜀: %s", exc)
        return

    if not posts:
        log.warning("게시글을 하나도 파싱하지 못했습니다(페이지 구조 변경 의심).")
        return

    matched = [p for p in posts if matches(p["title"])]
    log.info("게시글 %d건 중 매칭 %d건 발견.", len(posts), len(matched))

    if first_run:
        # 첫 실행: 과거 공지로 스팸하지 않도록 현재 매칭분을 조용히 시드한다.
        for p in matched:
            seen.add(p["seq"])
        save_seen(seen)
        if matched:
            latest = matched[0]  # 목록은 최신순
            log.info("현재 최신 매칭: [%s] %s (%s)", latest["seq"], latest["title"], latest["date"])
            if notify_on_first_run:
                if send_telegram(format_message(latest, _fetch_body(latest["seq"]))):
                    log.info("첫 실행 알림 전송: %s", latest["seq"])
        log.info("첫 실행 시드 완료. 이후 신규 공지부터 알립니다.")
        return

    # 오래된 → 최신 순으로 전송(여러 건이 한꺼번에 뜬 경우 순서 보장)
    new_posts = [p for p in matched if p["seq"] not in seen]
    if not new_posts:
        log.info("신규 매칭 공지 없음.")
        return

    for p in sorted(new_posts, key=lambda x: int(x["seq"])):
        if send_telegram(format_message(p, _fetch_body(p["seq"]))):
            log.info("알림 전송: [%s] %s", p["seq"], p["title"])
            seen.add(p["seq"])
            save_seen(seen)  # 건별 저장 → 중간 실패해도 이미 보낸 건 재전송 안 함
        else:
            log.warning("전송 실패로 seen 미기록(다음 주기 재시도): %s", p["seq"])


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(description="KOFIA 공지 → 텔레그램 알림 봇")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="1회만 폴링하고 종료")
    mode.add_argument("--loop", action="store_true", help="주기적으로 폴링(상시 감시)")
    mode.add_argument("--test", action="store_true", help="텔레그램 테스트 메시지 1건 전송 후 종료")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL,
                        help="--loop 폴링 주기(초). 기본 %(default)s초, 매 주기 ±{}초 지터".format(JITTER_SECONDS))
    parser.add_argument("--notify-on-first-run", action="store_true",
                        help="첫 실행 때 현재 최신 매칭 1건도 알림(이미 게시된 회차까지 잡고 싶을 때)")
    parser.add_argument("--send-seq", metavar="SEQ",
                        help="지정 seq 의 제목+본문+링크를 조립해 즉시 전송(테스트용, seen 무시)")
    parser.add_argument("--dry-run", action="store_true",
                        help="--send-seq 와 함께: 전송하지 않고 조립된 메시지를 화면에만 출력")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.send_seq:
        try:
            view_html = fetch_view_html(args.send_seq)
            post = parse_view_meta(view_html, args.send_seq)
            msg = format_message(post, extract_body(view_html))
        except Exception as exc:
            log.error("상세 페이지 조회/파싱 실패: %s", exc)
            return 1
        if args.dry_run:
            print(msg)
            return 0
        if send_telegram(msg):
            log.info("전송 성공: seq=%s", args.send_seq)
            return 0
        log.error("전송 실패: seq=%s", args.send_seq)
        return 1

    if args.test:
        ok = send_telegram("✅ KOFIA 알림 봇 테스트 메시지입니다.")
        if ok:
            log.info("테스트 메시지 전송 성공.")
            return 0
        log.error("테스트 메시지 전송 실패. 토큰/chat_id 를 확인하세요.")
        return 1

    if not (args.once or args.loop):
        parser.print_help()
        return 2

    first_run = not os.path.exists(STATE_FILE)
    seen = load_seen()

    if args.once:
        run_once(seen, first_run, args.notify_on_first_run)
        return 0

    # --loop
    log.info("상시 감시 시작 (주기 %d초 ±%d초). 대상 키워드: %s",
             args.interval, JITTER_SECONDS, " + ".join(KEYWORDS))
    while True:
        run_once(seen, first_run, args.notify_on_first_run)
        first_run = False  # 이후 주기는 항상 일반 모드
        # 기본 간격에 ±JITTER_SECONDS 무작위 지터를 더한다(음수 방지).
        delay = max(1.0, args.interval + random.uniform(-JITTER_SECONDS, JITTER_SECONDS))
        log.info("다음 확인까지 %.0f초 대기", delay)
        try:
            time.sleep(delay)
        except KeyboardInterrupt:
            log.info("종료합니다.")
            return 0


if __name__ == "__main__":
    sys.exit(main())
