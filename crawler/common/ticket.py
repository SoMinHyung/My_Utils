"""티켓 좌석 모니터 공용 코드.

interpark_monitor 와 doosan_ticket_monitor 는 좌석 조회 폴링 루프가 사실상 동일하다.
차이는 사이트별 monitor/status 구현과, 시작/종료 메시지·예매 URL 뿐이라 그 부분만 인자로 받는다.

이 모듈은 3.12 프로젝트에서만 import 된다(kofia 3.7 은 import 하지 않음).
"""
import logging
import random
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SeatInfo:
    grade: str       # 좌석 등급명 (예: "VIP석", "1루 네이비석")
    available: int   # 잔여석 수 (0 = 매진, -1 = 알 수 없음)


def seats_changed(prev, curr) -> bool:
    """좌석 구성/잔여석이 직전 상태와 달라졌는지."""
    if prev is None:
        return True
    if len(prev.seats) != len(curr.seats):
        return True
    for p, c in zip(prev.seats, curr.seats):
        if p.grade != c.grade or p.available != c.available:
            return True
    return False


def run_seat_monitor(monitor, notifier, *, start_message, end_message,
                     reserve_url, poll_min, poll_max, max_failures=5):
    """좌석 모니터링 폴링 루프.

    monitor: start_browser()/stop_browser()/check_seats() 를 가진 객체.
             check_seats() 는 status(또는 None)를 반환하며, status 는
             summary()/has_available_seats() 를 제공해야 한다.
    notifier: send(text) 를 가진 객체(common.notifier.TelegramNotifier).
    """
    notifier.send(start_message)

    monitor.start_browser()
    previous = None
    failures = 0

    try:
        while True:
            status = monitor.check_seats()

            if status is None:
                failures += 1
                logger.warning("조회 실패 (%d/%d)", failures, max_failures)
                if failures >= max_failures:
                    notifier.send(
                        "[오류] 연속 %d회 조회 실패. 브라우저를 재시작합니다." % max_failures
                    )
                    failures = 0
                    monitor.stop_browser()
                    time.sleep(5)
                    monitor.start_browser()
            else:
                failures = 0
                logger.info("좌석 조회 결과:\n%s", status.summary())

                if seats_changed(previous, status):
                    notifier.send(status.summary())

                    # 매진 -> 잔여석 발생 시 강조 알림
                    if (previous is not None
                            and not previous.has_available_seats()
                            and status.has_available_seats()):
                        notifier.send("잔여석이 발생했습니다! 지금 확인하세요!\n" + reserve_url)

                previous = status

            interval = random.uniform(poll_min, poll_max)
            logger.info("다음 조회까지 %.1f초 대기", interval)
            time.sleep(interval)

    except KeyboardInterrupt:
        logger.info("사용자에 의해 모니터링 종료")
    finally:
        monitor.stop_browser()
        notifier.send(end_message)
