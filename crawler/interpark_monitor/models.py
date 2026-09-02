from dataclasses import dataclass, field
from datetime import datetime

from common.ticket import SeatInfo  # 공용 좌석 정보(재-export: monitor.py 가 여기서 import)

__all__ = ["SeatInfo", "ShowStatus"]


@dataclass
class ShowStatus:
    show_date: str
    showtime: str
    seats: list[SeatInfo]
    checked_at: datetime = field(default_factory=datetime.now)

    def has_available_seats(self) -> bool:
        return any(s.available > 0 for s in self.seats)

    def summary(self) -> str:
        lines = [
            "[인터파크 티켓 알림]",
            f"공연일: {self.show_date}",
            f"회차: {self.showtime}",
            f"조회시간: {self.checked_at.strftime('%H:%M:%S')}",
            "",
        ]
        for s in self.seats:
            status = f"{s.available}석" if s.available > 0 else "매진"
            lines.append(f"  {s.grade}: {status}")
        return "\n".join(lines)
