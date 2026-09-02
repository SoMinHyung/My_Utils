import configparser
import logging
import os
import sys

# crawler/ 를 import 경로에 추가 → `from common...` 사용 가능 (프로젝트 폴더에서 실행해도 동작)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from monitor import DoosanMonitor
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
        "reserve_url": config['Doosan']['reserve_url'],
        "login_id": config['Doosan']['login_id'],
        "login_pw": config['Doosan']['login_pw'],
        "target_date": config['Doosan']['target_date'],
        "opponent": config['Doosan']['opponent'],
        "poll_min": float(config['Doosan']['poll_interval_min']),
        "poll_max": float(config['Doosan']['poll_interval_max']),
        "headless": config['Doosan'].getboolean('headless'),
        "storage_state_path": config['Doosan'].get('storage_state_path', 'storage_state.json'),
    }


def run():
    cfg = load_config()
    notifier = TelegramNotifier()  # crawler/secrets.ini 에서 자격증명 로드
    monitor = DoosanMonitor(
        reserve_url=cfg["reserve_url"],
        login_id=cfg["login_id"],
        login_pw=cfg["login_pw"],
        target_date=cfg["target_date"],
        opponent=cfg["opponent"],
        storage_state_path=cfg["storage_state_path"],
        headless=cfg["headless"],
    )

    run_seat_monitor(
        monitor,
        notifier,
        start_message=(
            f"[두산베어스 모니터링 시작]\n"
            f"경기일: {cfg['target_date']}\n"
            f"상대: {cfg['opponent']}\n"
            f"폴링 간격: {cfg['poll_min']}~{cfg['poll_max']}초"
        ),
        end_message="[두산베어스 모니터링 종료]",
        reserve_url=cfg["reserve_url"],
        poll_min=cfg["poll_min"],
        poll_max=cfg["poll_max"],
    )


if __name__ == "__main__":
    run()
