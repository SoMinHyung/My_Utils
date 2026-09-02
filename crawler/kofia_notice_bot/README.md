# KOFIA 공지 → 텔레그램 알림 봇

KOFIA(금융투자협회) 공지사항 게시판
[`/brd/m_17/list.do`](https://www.kofia.or.kr/brd/m_17/list.do) 을 주기적으로 확인해,
제목에 **`삼성전자` + `SK하이닉스`** 가 모두 들어간 신규 공지
(매월 올라오는 *"YYYY년 M월 삼성전자 등 및 SK하이닉스 시가총액 비중안내"*)가 뜨면
텔레그램으로 알림을 보냅니다.

- 목록은 서버 렌더링 HTML → 단순 GET 으로 조회(로그인/JS 불필요).
- 매칭은 월 문자열이 아니라 **키워드**로 하므로 매달 자동 대응됩니다.
- 중복 알림 방지는 게시글 `seq` 기준이며, 상태는 `seen_seqs.json` 에 저장됩니다.
- 알림에는 **제목 + 작성일 + 본문(내용) + 링크**가 함께 담깁니다. 본문은 상세 페이지
  `view.do` 의 `<div id="write">`(내용 영역)에서 추출하며, 최대 1500자까지 넣습니다.

## 설치

의존성과 실행은 **공통 uv 환경**(`crawler/`)을 씁니다. 최초 1회:

```bash
cd /Users/so/PycharmProjects/My_Utils/crawler
uv sync
cp secrets.ini.example secrets.ini   # 텔레그램 자격증명 채우기
```

`crawler/secrets.ini` 의 `[Telegram] bot / chat_id` 를 모든 크롤러가 공용으로 읽습니다:

```ini
[Telegram]
bot = 123456:ABC...
chat_id = -1001234567890
```

> `crawler/secrets.ini` 는 `crawler/.gitignore` 에 포함되어 커밋되지 않습니다.

## 사용법

```bash
uv run python kofia_notice_bot.py --test        # 텔레그램 연결 확인 메시지 1건
uv run python kofia_notice_bot.py --once         # 1회 폴링
uv run python kofia_notice_bot.py --loop         # 상시 감시(기본 60초 주기, 매 주기 ±10초 지터)
uv run python kofia_notice_bot.py --loop --interval 120    # 120초 주기(±10초)
```

### 첫 실행 동작
상태 파일(`seen_seqs.json`)이 없는 **첫 실행**에는 현재 게시된 매칭 공지를
**조용히 기록만** 하고 알림은 보내지 않습니다(과거 공지 스팸 방지).
이후 새로 올라오는 공지부터 알립니다.

이미 게시된 최신 회차까지 한 번 받고 싶다면:

```bash
uv run python kofia_notice_bot.py --once --notify-on-first-run
```

## 상시 실행 (인프로세스 루프)

별도 터미널에서 실행하거나 백그라운드로 띄웁니다:

```bash
nohup uv run python kofia_notice_bot.py --loop > kofia_bot.log 2>&1 &   # 기본 60초 ±10초
```

중지는 `Ctrl-C`(포그라운드) 또는 해당 프로세스 종료.

## 동작 테스트

특정 게시글(seq)의 실제 알림 메시지를 조립해 바로 확인/전송할 수 있습니다:

```bash
uv run python kofia_notice_bot.py --send-seq 2381 --dry-run   # 전송 없이 메시지만 출력(미리보기)
uv run python kofia_notice_bot.py --send-seq 2381             # 그 메시지를 실제로 텔레그램 전송
```

또는 신규 게시를 시뮬레이션하려면, `seen_seqs.json` 에서 최신 매칭 `seq` 하나(예: `2381`)를
지운 뒤 `--once` 를 다시 실행하면 폴링 경로로 해당 공지 알림이 옵니다.

## 커스터마이즈
- 감시 키워드: `kofia_notice_bot.py` 상단 `KEYWORDS` 수정.
- 폴링 주기: `--interval` 옵션(기본 60초). 매 주기 ±10초 무작위 지터가 붙습니다(`JITTER_SECONDS`).
