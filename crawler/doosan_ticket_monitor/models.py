from dataclasses import dataclass, field
from datetime import datetime

from common.ticket import SeatInfo  # 공용 좌석 정보(재-export: monitor.py 가 여기서 import)

__all__ = ["SeatInfo", "GameStatus"]


@dataclass
class GameStatus:
    game_date: str            # "2026.04.25"
    opponent: str             # "kt"
    seats: list[SeatInfo]
    checked_at: datetime = field(default_factory=datetime.now)

    def has_available_seats(self) -> bool:
        return any(s.available > 0 for s in self.seats)

    def summary(self) -> str:
        lines = [
            "[두산베어스 티켓 알림]",
            f"경기일: {self.game_date}",
            f"상대: {self.opponent}",
            f"조회시간: {self.checked_at.strftime('%H:%M:%S')}",
            "",
        ]
        for s in self.seats:
            status = f"{s.available}석" if s.available > 0 else "매진"
            lines.append(f"  {s.grade}: {status}")
        return "\n".join(lines)
