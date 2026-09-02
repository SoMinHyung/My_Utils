# crawler

텔레그램으로 알림을 보내는 웹 모니터링/크롤러 유틸 모음.
**공통 uv 환경 하나(Python 3.12)** 를 모든 하위 프로젝트가 공유하고,
공통 코드는 `common/` 에, 공용 텔레그램 자격증명은 `secrets.ini` 한 곳에 둔다.

## 구조

```
crawler/
├── pyproject.toml         # 공통 uv 프로젝트(모든 의존성 정의, Python 3.12)
├── uv.lock                # 공통 잠금 파일 (커밋)
├── .python-version        # 3.12
├── .venv/                 # 공통 가상환경 (uv sync 로 생성, gitignore)
├── secrets.ini            # 공용 텔레그램 자격증명 [Telegram] bot, chat_id  (gitignore)
├── secrets.ini.example    # 템플릿
├── common/                # 프로젝트 공용 코드
│   ├── notifier.py        #   TelegramNotifier — secrets.ini 에서 읽어 메시지 전송 (bool 반환)
│   └── ticket.py          #   SeatInfo, seats_changed(), run_seat_monitor() (좌석 모니터 공용 루프)
├── interpark_monitor/     # 인터파크 좌석 모니터 (Playwright)
├── doosan_ticket_monitor/ # 두산베어스 좌석 모니터 (Playwright)
└── kofia_notice_bot/      # KOFIA 공지 감시 봇 (requests)
```

## 설치 (최초 1회)

```bash
cd crawler
uv sync                         # .venv 생성 + 의존성 설치 (Python 3.12)
uv run playwright install chromium   # 좌석 모니터(interpark/doosan)용 브라우저
cp secrets.ini.example secrets.ini   # 텔레그램 자격증명 채우기
```

`secrets.ini`:

```ini
[Telegram]
bot = <봇_토큰>
chat_id = <chat_id>
```

## 실행

모두 공통 환경에서 `uv run` 으로 실행한다(프로젝트 폴더 안/밖 어디서든 crawler 의 env 를 찾는다).

```bash
# KOFIA 공지봇
cd crawler/kofia_notice_bot && uv run python kofia_notice_bot.py --loop

# 인터파크 / 두산 좌석 모니터 (playwright)
cd crawler/interpark_monitor && uv run python main.py
cd crawler/doosan_ticket_monitor && uv run python main.py
```

## 공용 코드 (`common/`)

- **`common.notifier.TelegramNotifier`** — 인자 없이 생성하면 `crawler/secrets.ini` 에서
  자격증명을 읽는다. `send(text, disable_preview=False) -> bool`.
- **`common.ticket`** — 좌석 모니터 두 개(interpark/doosan)가 공유하는 `SeatInfo`,
  `seats_changed(prev, curr)`, `run_seat_monitor(monitor, notifier, ...)`.

각 진입 스크립트는 자기 상위(`crawler/`)를 `sys.path` 에 넣어 `from common... import ...`
로 공용 코드를 가져온다.

## 의존성 변경

`pyproject.toml` 의 `dependencies` 를 편집한 뒤 `uv sync` (또는 `uv add <패키지>` / `uv remove <패키지>`).
