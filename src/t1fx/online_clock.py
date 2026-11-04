"""UTC synchronized from HTTPS response dates and advanced with a monotonic clock."""

import secrets
import threading
import time
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.request import Request, urlopen

SOURCES = ('https://www.google.com/generate_204', 'https://www.cloudflare.com/cdn-cgi/trace')
_lock = threading.Lock()
_sample: tuple[datetime, float, str] | None = None


class OnlineClockUnavailable(RuntimeError):
    pass


def online_time() -> dict:
    global _sample
    with _lock:
        elapsed = time.monotonic() - _sample[1] if _sample else float('inf')
        if elapsed >= 60:
            for source in SOURCES:
                try:
                    request = Request(source + '?clock=' + secrets.token_hex(8),
                                      headers={'Cache-Control': 'no-cache'})
                    with urlopen(request, timeout=5) as response:
                        raw = response.headers.get('Date')
                    instant = parsedate_to_datetime(raw)
                    if instant.tzinfo is None:
                        raise ValueError('Online clock returned a naive date')
                    _sample = (instant.astimezone(UTC), time.monotonic(), source)
                    break
                except (OSError, ValueError, TypeError, IndexError):
                    continue
        if _sample is None or time.monotonic() - _sample[1] > 300:
            raise OnlineClockUnavailable('Online time is unavailable. Check your internet connection and retry.')
        elapsed = time.monotonic() - _sample[1]
        return {'utc_now': (_sample[0] + timedelta(seconds=elapsed)).isoformat(),
                'synchronized_at': _sample[0].isoformat(), 'source': _sample[2],
                'stale': elapsed >= 60}
