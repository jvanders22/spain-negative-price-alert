# Vendored from jvanders22/spain-power-prices (src/spain_power/http.py @ 3910b0b), MIT, same author.
# Copied rather than installed so this repo has no private-repo dependency. Local changes are noted inline.
from __future__ import annotations

import time

import requests


class NotFound(RuntimeError):  # local change: let callers tell "not published yet" from other failures
    pass


UA = {
    "User-Agent": "spain-negative-price-alert/0.1 (+https://github.com/jvanders22/spain-negative-price-alert)"
}


def get_text(
    url: str, *, params: dict | None = None, headers: dict | None = None, retries: int = 4, timeout: int = 30
) -> str:
    last: Exception | None = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, headers={**UA, **(headers or {})}, timeout=timeout)
            if r.status_code == 200 and r.text.strip():
                return r.text
            if r.status_code == 404:  # local change: raise immediately, distinguishable from other errors
                raise NotFound(f"HTTP 404 for {r.url}")
            last = RuntimeError(f"HTTP {r.status_code} for {r.url}")
        except requests.RequestException as e:  # pragma: no cover - network
            last = e
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"failed to fetch {url}: {last}")
