import time
from app.core.exception import TransientError


def retry_once(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except TransientError:
        time.sleep(2)
        return func(*args, **kwargs)


