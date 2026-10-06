"""자연어 질의 하루 호출 상한 - 공개 서버에서 방문자가 LLM 무료 한도를 다 써 버리지 않게 한다.

- KST 날짜별로 질문 수를 API 프로세스 메모리에서 센다 (api replica 1 기준).
- 재시작하면 0부터 다시 세지만, 그 이상은 제공자 자체 한도(Gemini 무료 등급 등)가 막는다.
- limit 0은 상한 없음 (로컬 개발·평가 기본값).
"""
import threading
from datetime import datetime, time, timedelta
from typing import Optional

from nlq_providers import KST


class DailyQuota:
    def __init__(self, limit: int):
        self.limit = max(limit, 0)
        self._lock = threading.Lock()
        self._day = None
        self._used = 0

    def _roll(self, now: datetime) -> None:
        day = now.astimezone(KST).date()
        if day != self._day:
            self._day, self._used = day, 0

    def try_acquire(self, now: Optional[datetime] = None) -> bool:
        """1회 사용. 상한에 닿았으면 False (세지 않음)"""
        now = now or datetime.now(KST)
        with self._lock:
            self._roll(now)
            if self.limit and self._used >= self.limit:
                return False
            self._used += 1
            return True

    def release(self, now: Optional[datetime] = None) -> None:
        """try_acquire로 센 1회를 되돌린다 (제공자 오류로 답을 못 준 질문은 세지 않기 위해).
        그 사이 KST 날짜가 바뀌었으면 새 날의 카운트를 건드리지 않는다"""
        now = now or datetime.now(KST)
        with self._lock:
            day = now.astimezone(KST).date()
            if day == self._day and self._used > 0:
                self._used -= 1

    def status(self, now: Optional[datetime] = None) -> dict:
        now = now or datetime.now(KST)
        with self._lock:
            self._roll(now)
            reset = datetime.combine(self._day + timedelta(days=1), time(0), tzinfo=KST)
            return {
                "limit": self.limit or None,
                "used": self._used,
                "remaining": max(self.limit - self._used, 0) if self.limit else None,
                "resets_at": reset,
            }
