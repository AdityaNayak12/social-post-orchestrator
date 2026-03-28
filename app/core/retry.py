import random
import time
from typing import Callable, Literal, Optional, TypeVar

from app.core.exception import TransientError

T = TypeVar("T")


def _jitter(delay: float) -> float:
    return delay * (0.5 + random.random() * 0.5)


def retry_on_transient(
    fn: Callable[[], T],
    *,
    max_retries: int,
    backoff: Literal["fixed", "exponential"] = "exponential",
    delay_seconds: float = 2.0,
    exponential_base: float = 2.0,
    jitter: bool = False,
    on_retry: Optional[Callable[[int, float, TransientError], None]] = None,
    on_exhausted: Optional[Callable[[TransientError], None]] = None,
) -> T:
    total_attempts = max_retries + 1
    for attempt in range(total_attempts):
        try:
            return fn()
        except TransientError as e:
            if attempt < max_retries:
                if backoff == "fixed":
                    raw_delay = delay_seconds
                else:
                    raw_delay = exponential_base**attempt
                sleep_time = _jitter(raw_delay) if jitter else raw_delay
                if on_retry is not None:
                    on_retry(attempt, sleep_time, e)
                time.sleep(sleep_time)
            else:
                if on_exhausted is not None:
                    on_exhausted(e)
                raise


def retry_once(func, *args, **kwargs):
    return retry_on_transient(
        lambda: func(*args, **kwargs),
        max_retries=1,
        backoff="fixed",
        delay_seconds=2.0,
        jitter=False,
    )
