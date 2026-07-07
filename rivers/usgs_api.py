"""Thin, resilient client for the USGS Water Services REST API.

Only the pieces this project needs are wrapped:

* the Site Service (``/site``)          -> station metadata
* the Daily Values service (``/dv``)    -> historical daily statistics
* the Instantaneous Values service (``/iv``) -> latest observations

The client adds ret/backoff, a shared session with a proper User-Agent, and an
optional on-disk cache. It returns *raw* payloads (RDB text or JSON dicts);
parsing into tidy tables lives in ``normalize.py`` so fetching and shaping stay
separable and testable.

Reference: https://waterservices.usgs.gov/docs/
"""
from __future__ import annotations

import time
from typing import Any

import requests

from . import cache
from .config import (
    HTTP_BACKOFF,
    HTTP_MAX_RETRIES,
    HTTP_TIMEOUT,
    USER_AGENT,
    USGS_BASE,
)

_session: requests.Session | None = None


def _sess() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": USER_AGENT})
    return _session


class USGSAPIError(RuntimeError):
    """Raised when the USGS service returns a non-retryable error."""


def _request(path: str, params: dict[str, Any], *, use_cache: bool,
             cache_max_age_s: float | None, is_json: bool) -> str:
    """GET ``USGS_BASE/path`` with retries; return the response text.

    A 404 from these services usually means "no sites/data match the query",
    which is a normal, non-exceptional outcome. We surface it to the caller as
    an empty string so fetchers can skip cleanly.
    """
    url = f"{USGS_BASE}/{path.lstrip('/')}"
    suffix = ".json" if is_json else ".rdb"

    if use_cache:
        cached = cache.get(url, params, max_age_s=cache_max_age_s, suffix=suffix)
        if cached is not None:
            return cached

    last_exc: Exception | None = None
    for attempt in range(HTTP_MAX_RETRIES):
        try:
            resp = _sess().get(url, params=params, timeout=HTTP_TIMEOUT)
            if resp.status_code == 404:
                return ""  # no matching sites/data
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"retryable {resp.status_code}")
            resp.raise_for_status()
            text = resp.text
            if use_cache:
                cache.put(url, params, text, suffix=suffix)
            return text
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
            last_exc = exc
            if attempt < HTTP_MAX_RETRIES - 1:
                time.sleep(HTTP_BACKOFF * (2 ** attempt))
            continue
    raise USGSAPIError(f"USGS request failed after {HTTP_MAX_RETRIES} tries: "
                       f"{url} params={params}") from last_exc


# --------------------------------------------------------------------------- #
# Public endpoints
# --------------------------------------------------------------------------- #
def get_sites_rdb(state_cd: str, *, parameter_cd: str | None = None,
                  site_type: str = "ST", has_data_type: str = "dv",
                  use_cache: bool = True) -> str:
    """Return RDB text of sites in a state (optionally filtered by parameter)."""
    params: dict[str, Any] = {
        "format": "rdb",
        "stateCd": state_cd.lower(),
        "siteType": site_type,
        "hasDataTypeCd": has_data_type,
        "siteStatus": "all",
    }
    if parameter_cd:
        params["parameterCd"] = parameter_cd
    # Site metadata changes slowly -> cache for a week.
    return _request("site", params, use_cache=use_cache,
                    cache_max_age_s=7 * 86400, is_json=False)


def get_daily_values_json(state_cd: str, parameter_cd: str, start: str, end: str,
                          *, stat_cd: str = "00003", use_cache: bool = True) -> str:
    """Return WaterML/JSON text of daily values for a state+parameter+range.

    ``stat_cd`` 00003 is the daily mean. Dates are ISO ``YYYY-MM-DD``.
    """
    params = {
        "format": "json",
        "stateCd": state_cd.lower(),
        "parameterCd": parameter_cd,
        "statCd": stat_cd,
        "startDT": start,
        "endDT": end,
        "siteType": "ST",
        "siteStatus": "all",
    }
    return _request("dv", params, use_cache=use_cache,
                    cache_max_age_s=86400, is_json=True)


def get_latest_values_json(state_cd: str, parameter_cd: str,
                           *, period: str = "P7D", use_cache: bool = True) -> str:
    """Return instantaneous-values JSON for the recent ``period`` (ISO 8601).

    We request a short trailing window (default 7 days) and keep the most recent
    reading per site in ``normalize.py``; this is more reliable than the strict
    "current" endpoint when some gauges report irregularly.
    """
    params = {
        "format": "json",
        "stateCd": state_cd.lower(),
        "parameterCd": parameter_cd,
        "period": period,
        "siteType": "ST",
        "siteStatus": "active",
    }
    # Latest data should be fresh -> short cache.
    return _request("iv", params, use_cache=use_cache,
                    cache_max_age_s=1800, is_json=True)
