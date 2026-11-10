import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Optional

MAX_EVENTS = 50


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


class Metrics:
    """Thread-safe in-memory runtime metrics. Resets when the process restarts."""

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._started_at = _now()
            self._runs = 0
            self._posted = 0
            self._failed = 0
            self._timed_runs = 0
            self._total_duration_ms = 0.0
            self._events: deque = deque(maxlen=MAX_EVENTS)
            self._poller = {"last_run_at": None, "next_run_at": None, "rows_found": None, "last_error": None}

    def record_run(self, row_id: int, status: str, stage: Optional[str], duration_ms: float) -> None:
        event = {
            "row_id": row_id,
            "status": status,
            "stage": stage,
            "duration_ms": round(duration_ms, 1),
            "finished_at": _iso(_now()),
        }
        with self._lock:
            self._runs += 1
            if status in ("posted", "failed"):
                # Only real attempts count toward the average; skipped rows return instantly.
                self._timed_runs += 1
                self._total_duration_ms += duration_ms
                if status == "posted":
                    self._posted += 1
                else:
                    self._failed += 1
            self._events.appendleft(event)

    def record_poll(self, rows_found: Optional[int], error: Optional[str], interval_seconds: float) -> None:
        now = _now()
        with self._lock:
            self._poller = {
                "last_run_at": _iso(now),
                "next_run_at": _iso(now + timedelta(seconds=interval_seconds)),
                "rows_found": rows_found,
                "last_error": error,
            }

    def snapshot(self) -> dict:
        with self._lock:
            now = _now()
            return {
                "started_at": _iso(self._started_at),
                "uptime_seconds": int((now - self._started_at).total_seconds()),
                "runs_since_start": self._runs,
                "posted_since_start": self._posted,
                "failed_since_start": self._failed,
                "avg_duration_ms": round(self._total_duration_ms / self._timed_runs, 1) if self._timed_runs else None,
                "poller": dict(self._poller),
                "events": list(self._events),
            }


metrics = Metrics()
