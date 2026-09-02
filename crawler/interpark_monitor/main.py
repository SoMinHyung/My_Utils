import configparser
import logging
import os
import sys

# crawler/ 를 import 경로에 추가 → `from common...` 사용 가능 (프로젝트 폴더에서 실행해도 동작)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from monitor import InterparkMonitor
from common.notifier import TelegramNotifier
from common.ticket import run_seat_monitor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def load_config(path: str = "config.ini") -> dict:
    config = configparser.ConfigParser()
    config.read(path)
    return {
        "url": config['Interpark']['url'],
        "target_date": config['Interpark']['target_date'],
        "target_showtime": int(config['Interpark']['target_showtime']),
        "poll_min": float(config['Interpark']['poll_interval_min']),
        "poll_max": float(config['Interpark']['poll_interval_max']),
        "headless": config['Interpark'].getboolean('headless'),
    }


def run():
    cfg = load_config()
    notifier = TelegramNotifier()  # crawler/secrets.ini 에서 자격증명 로드
    monitor = InterparkMonitor(
        url=cfg["url"],
        target_date=cfg["target_date"],
        target_showtime=cfg["target_showtime"],
        headless=cfg["headless"],
    )

    run_seat_monitor(
        monitor,
        notifier,
        start_message=(
            f"[인터파크 모니터링 시작]\n"
            f"공연일: {cfg['target_date']}\n"
            f"회차: {cfg['target_showtime']}회\n"
            f"폴링 간격: {cfg['poll_min']}~{cfg['poll_max']}초"
        ),
        end_message="[인터파크 모니터링 종료]",
        reserve_url=cfg["url"],
        poll_min=cfg["poll_min"],
        poll_max=cfg["poll_max"],
    )


if __name__ == "__main__":
    run()

#claude --resume "interpark-seat-monitor"
