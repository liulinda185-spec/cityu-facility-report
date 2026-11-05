"""In-memory abuse guard — no database, no IP stored on disk.

Ticket codes are sequential, so an unauthenticated lookup endpoint can be walked
from 000001 upwards. We do not want to keep visitor addresses, so the guard
lives in process memory only: it disappears on restart and never reaches the
database or a log line.

Good enough for a single Render instance; if the service is ever scaled out,
move this to Redis (the call sites do not change).
"""
import threading
import time

_LOCK = threading.Lock()
_HITS: dict[str, list[float]] = {}


def allow(key: str, limit: int, window_seconds: int) -> bool:
    """Record one hit for `key`; return False when the window is already full."""
    now = time.time()
    with _LOCK:
        stamps = [t for t in _HITS.get(key, []) if now - t < window_seconds]
        if len(stamps) >= limit:
            _HITS[key] = stamps
            return False
        stamps.append(now)
        _HITS[key] = stamps
        if len(_HITS) > 5000:  # keep memory bounded under a scan
            for k in [k for k, v in _HITS.items() if not v or now - v[-1] > window_seconds]:
                _HITS.pop(k, None)
    return True


def reset() -> None:
    """Test hook."""
    with _LOCK:
        _HITS.clear()
