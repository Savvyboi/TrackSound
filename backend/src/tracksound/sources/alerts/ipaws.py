"""Cross-check a decoded header against FEMA's archive of IPAWS alerts (OpenFEMA).

https://www.fema.gov/api/open/v1/IpawsArchivedAlerts holds the CAP messages that went through
IPAWS: NWS warnings sent to EAS and Wireless Emergency Alerts, alerts from state and local
authorities, and national tests. It does not hold weekly or monthly tests that stations and
NOAA Weather Radio generate locally, so "no match" is normal for RWT and RMT.

Only a time window is sent to FEMA (no locations); the event and location codes are matched
here. The archive's ``sent`` is the CAP send time in UTC; a SAME header's JJJHHMM is that
time to the minute for alerts relayed from IPAWS.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import requests

API = "https://www.fema.gov/api/open/v1/IpawsArchivedAlerts"
WINDOW_S = 15 * 60
PAGE = 1000
MAX_PAGES = 3


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _same_values(alert: dict, key: str) -> set[str]:
    """SAME event codes (key "eventCode") or SAME location codes (key "geocode") of a CAP alert."""
    out = set()
    for info in alert.get("info") or []:
        if key == "eventCode":
            items = info.get("eventCode") or []
        else:
            items = [g for area in info.get("area") or [] for g in area.get("geocode") or []]
        for item in items:
            if (item.get("name") or item.get("valueName")) == "SAME" and item.get("value"):
                out.add(str(item["value"]).strip())
    return out


def fetch_window(t0: float, t1: float, timeout: float = 15.0) -> list[dict]:
    """IPAWS alerts sent between t0 and t1 (unix s)."""
    out: list[dict] = []
    for page in range(MAX_PAGES):
        params = {"$filter": f"sent ge '{_iso(t0)}' and sent le '{_iso(t1)}'",
                  "$select": "identifier,sender,sent,status,msgType,info", "$top": str(PAGE), "$skip": str(page * PAGE)}
        r = requests.get(API, params=params, timeout=timeout)
        r.raise_for_status()
        batch = r.json().get("IpawsArchivedAlerts") or []
        out.extend(batch)
        if len(batch) < PAGE:
            break
    return out


def match(alerts: list[dict], event: str, locations: list[str], issued_utc: float) -> list[dict]:
    """Archived alerts with the same SAME event code, best first (most shared counties, then
    closest in time). ``locations`` are PSSCCC codes; the county part (SSCCC) is compared."""
    wanted = {c[1:] for c in locations}
    out = []
    for a in alerts:
        if event not in _same_values(a, "eventCode"):
            continue
        geo = {g[-5:] for g in _same_values(a, "geocode") if len(g) >= 5}
        common = len(wanted & geo)
        try:
            sent = datetime.fromisoformat(a["sent"].replace("Z", "+00:00")).timestamp()
        except (KeyError, ValueError, AttributeError):
            continue
        if common == 0 and wanted and geo:
            continue
        out.append({"identifier": a.get("identifier"), "sender": a.get("sender"), "sent_utc": sent,
                    "dt_s": round(sent - issued_utc, 1), "common_locations": common, "n_locations": len(geo),
                    "status": a.get("status"), "msg_type": a.get("msgType")})
    out.sort(key=lambda m: (-m["common_locations"], abs(m["dt_s"])))
    return out


def crosscheck(event: str, locations: list[str], issued_utc: float, timeout: float = 15.0) -> dict:
    """Look for the decoded header in the IPAWS archive (never raises)."""
    rec = {"checked_at": time.time(), "issued_utc": issued_utc, "window_s": WINDOW_S, "status": "error",
           "matches": [], "n_alerts": 0, "error": None}
    if issued_utc > time.time() + 3600:
        rec["error"] = "issue time is in the future"
        return rec
    try:
        alerts = fetch_window(issued_utc - WINDOW_S, issued_utc + WINDOW_S, timeout)
    except (requests.RequestException, ValueError) as e:
        rec["error"] = f"{type(e).__name__}: {e}"[:200]
        return rec
    found = match(alerts, event, locations, issued_utc)
    rec.update(status="match" if found else "no_match", matches=found[:3], n_alerts=len(alerts))
    return rec
