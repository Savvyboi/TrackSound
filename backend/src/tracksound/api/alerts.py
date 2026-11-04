"""Alerts source API: EAS / NOAA Weather Radio SAME headers decoded from the recording (docs/API.md).

State lives in ``<case>/alerts/scan.json``: the decoded headers with their raw fields, the
end-of-message bursts, attention signals and the IPAWS cross-checks of the last scan. Values
that depend on the case (issue year, implied start time, county names) are worked out when the
state is read, so changing the case date updates them without a new scan.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import get_jobs, get_store
from ..jobs import JobContext, JobManager
from ..sources.alerts import codes, ipaws, issue_time, scan, same
from ..store import Store, new_id

router = APIRouter(prefix="/api", tags=["alerts"])

SCAN = "alerts/scan.json"
JOB_KIND = "alerts.scan"
STANDALONE_ATTENTION_S = 6.0


class ScanBody(BaseModel):
    crosscheck: bool = True       # also look decoded headers up in FEMA's IPAWS archive (network)


# ---- reading the state ------------------------------------------------------------------------

def _utc(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _canadian(station: str) -> bool:
    """Environment Canada's Weatheradio sends EC/GC/CA; Canadian location codes are not FIPS."""
    s = station.strip().upper()
    return s.startswith("EC/GC") or s.endswith("/CA")


def _locations(h: dict) -> list[dict]:
    locs = h["fields"]["locations"]
    if _canadian(h["fields"]["station"]):
        return [{"code": c, "part": codes.COUNTY_PARTS.get(c[0], c[0]), "fips": c[1:], "state": None, "county": None,
                 "lat": None, "lon": None} for c in locs]
    return [codes.describe_location(c) for c in locs]


def _confidence(h: dict, locations: list[dict]) -> float:
    """Posterior that the text is exactly right, lowered for weaker corroboration."""
    c = float(h["p_correct"])
    if h["repeats"] == 1 and not h.get("attention"):
        c *= 0.85                                  # nothing to cross-check a lone burst against
    if h["fields"]["event"] not in codes.EVENTS:
        c *= 0.8
    if any(loc["county"] is None for loc in locations) and not _canadian(h["fields"]["station"]):
        c *= 0.9
    return round(min(max(c, 0.0), 1.0), 3)


def _header_view(h: dict, case: dict, doc: dict) -> tuple[dict, str]:
    """(SameHeader, year basis) for a stored header."""
    f = h["fields"]
    issued, basis = issue_time.resolve(f["jjj"], f["hh"], f["mm"], case.get("date"),
                                       (doc.get("source") or {}).get("creation_time"), doc["scanned_at"])
    locs = _locations(h)
    issued = issued if issued is not None else 0.0
    view = {
        "id": h["id"], "t": h["t"], "repeats": h["repeats"], "raw": h["raw"],
        "originator": f["originator"], "originator_name": codes.originator_name(f["originator"]),
        "event": f["event"], "event_name": codes.event_name(f["event"]), "locations": locs,
        "purge_minutes": f["purge_minutes"], "issued_utc": issued, "station": f["station"].rstrip(),
        "implied_start_utc": round(issued - h["t"], 3), "confidence": _confidence(h, locs),
    }
    return view, basis


def _year_note(basis: str, case: dict, doc: dict) -> str:
    if basis == "case date":
        return f"Issue years come from the case date ({case['date']})."
    if basis == "file creation time":
        return (f"Issue years come from the file's creation time ({doc['source']['creation_time']}), as the case has "
                "no date. Set the case date if the file was copied or converted later.")
    return ("The headers carry no year, and neither the case date nor the file metadata gives one: the most recent "
            "matching day before the scan is assumed. Set the case date to be sure.")


def _header_notes(h: dict, view: dict) -> list[str]:
    where = f"{view['event']} header at {view['t']:.1f} s"
    out = []
    if h["repeats"] < 3:
        out.append(f"{where}: {h['repeats']} of the usual 3 bursts decoded.")
    elif h["n_agree"] < 3:
        out.append(f"{where}: the 3 bursts disagreed in places; bit errors were corrected by combining them "
                   f"({h['n_agree']} of 3 decoded alone to the final text).")
    if h.get("snapped"):
        out.append(f"{where}: weak bits in {', '.join(h['snapped'])} were corrected to the nearest known code.")
    att = h.get("attention")
    if att:
        name = "EAS attention signal (853 + 960 Hz)" if att["kind"] == "eas" else "NOAA Weather Radio alarm tone (1050 Hz)"
        out.append(f"{where} is followed by the {name} at {att['t']:.1f} s for {att['duration_s']:.0f} s.")
    if _canadian(view["station"]):
        out.append(f"{where}: station {view['station']} is Canadian (Environment Canada Weatheradio or a Canadian "
                   "broadcaster). Its location codes are Canadian, not US FIPS, and are not mapped.")
    x = h.get("ipaws")
    if x and abs((x.get("issued_utc") or 0) - view["issued_utc"]) < 1:
        if x["status"] == "match":
            m = x["matches"][0]
            shared = f"{m['common_locations']} shared location code{'s' if m['common_locations'] != 1 else ''}"
            out.append(f"{where}: FEMA's IPAWS archive has a matching {view['event']} alert sent {_utc(m['sent_utc'])} by "
                       f"{m['sender']} ({shared}, {m['dt_s']:+.0f} s from the decoded issue time).")
        elif x["status"] == "no_match":
            out.append(f"{where}: no matching alert in FEMA's IPAWS archive within ±{x['window_s'] // 60} min of the issue "
                       "time. That is normal for tests generated by stations or NOAA Weather Radio (RWT, RMT, DMO).")
        else:
            out.append(f"{where}: the IPAWS archive could not be checked ({x.get('error') or 'unavailable'}).")
    elif x:
        out.append(f"{where}: the case date changed since the IPAWS cross-check; scan again to repeat it.")
    return out


def _standalone_attention(doc: dict) -> list[str]:
    """EAS two-tone signals not explained by a decoded header."""
    used = {(h.get("attention") or {}).get("t") for h in doc.get("headers", [])}
    out = []
    for s in doc.get("attention", []):
        if s["kind"] == "eas" and s["t"] not in used and s["duration_s"] >= STANDALONE_ATTENTION_S:
            out.append(f"An EAS attention signal (853 + 960 Hz) sounds at {s['t']:.1f} s for {s['duration_s']:.0f} s "
                       "without a decodable header before it: an alert was probably broadcast around then.")
    return out


def view_state(case: dict, doc: dict | None) -> dict:
    """The GET /alerts response for a stored scan document (or none)."""
    if not doc:
        return {"headers": [], "eom": [], "scanned_at": None, "notes": []}
    headers, notes, basis = [], [], None
    for h in sorted(doc.get("headers", []), key=lambda h: h["t"]):
        view, basis = _header_view(h, case, doc)
        headers.append(view)
        notes.extend(_header_notes(h, view))
    if headers:
        notes.insert(0, _year_note(basis, case, doc))
        notes.insert(1, "Implied start times assume each header aired at its issue minute. The issue time is rounded "
                        "down to the minute and relaying stations can air an alert minutes later, so the recording "
                        "most likely started 0-2 min after the implied start (later for relays and rebroadcasts).")
    elif doc.get("n_bursts", 0) == 0:
        notes.append("No SAME data bursts were found in the recording.")
    for u in doc.get("undecoded", []):
        notes.append(f"{u['bursts']} burst{'s' if u['bursts'] != 1 else ''} at {u['t']:.1f} s looked like SAME data but "
                     "could not be decoded reliably (too weak or masked).")
    notes.extend(_standalone_attention(doc))
    notes.extend(doc.get("notes", []))
    return {"headers": headers, "eom": [{"t": e["t"]} for e in doc.get("eom", [])], "scanned_at": doc["scanned_at"],
            "notes": notes}


@router.get("/cases/{case_id}/alerts")
def alerts_state(case_id: str, store: Store = Depends(get_store)):
    case = store.get_case(case_id)
    return view_state(case, store.read_json(case_id, SCAN))


# ---- scanning ---------------------------------------------------------------------------------

def _crosscheck(headers: list[dict], case: dict, doc: dict, ctx: JobContext) -> None:
    for i, h in enumerate(headers):
        ctx.progress(0.92 + 0.07 * i / len(headers), "Cross-checking with FEMA's IPAWS archive")
        f = h["fields"]
        if _canadian(f["station"]):
            continue
        issued, _ = issue_time.resolve(f["jjj"], f["hh"], f["mm"], case.get("date"), doc["source"]["creation_time"],
                                       doc["scanned_at"])
        if issued is not None:
            h["ipaws"] = ipaws.crosscheck(f["event"], f["locations"], issued)


@router.post("/cases/{case_id}/alerts/scan")
def scan_alerts(case_id: str, body: ScanBody | None = None, store: Store = Depends(get_store),
                jobs: JobManager = Depends(get_jobs)):
    body = body or ScanBody()
    case = store.get_case(case_id)
    rec = case.get("recording")
    if not rec:
        raise HTTPException(400, "the case has no recording yet")
    if rec.get("status") == "processing":
        raise HTTPException(409, "the recording is still being processed")
    src = store.original_path(case_id)
    if src is None:
        raise HTTPException(400, "the original upload is missing; upload the recording again")
    if jobs.active(case_id, JOB_KIND):
        raise HTTPException(409, "an alerts scan is already running for this case")

    def work(ctx: JobContext):
        ctx.progress(0.01, "Reading file metadata")
        created = issue_time.creation_time(src)
        ctx.progress(0.02, "Decoding audio at 11 kHz")
        res = scan.scan_file(src, duration_s=rec.get("duration_s"), progress=ctx.sub(0.02, 0.9))
        doc = {"scanned_at": time.time(),
               "source": {"filename": rec.get("filename"), "sample_rate": same.FS, "duration_s": round(res.duration_s, 3),
                          "creation_time": created},
               "headers": [dict(h, id=new_id("sh")) for h in res.headers], "eom": res.eoms,
               "attention": res.attention, "undecoded": res.undecoded, "n_bursts": res.n_bursts, "notes": []}
        if body.crosscheck and doc["headers"]:
            _crosscheck(doc["headers"], store.get_case(case_id), doc, ctx)
        with store.lock(case_id):
            store.write_json(case_id, SCAN, doc)
        store.touch(case_id)
        n = len(doc["headers"])
        ctx.progress(1.0, f"Decoded {n} alert header{'s' if n != 1 else ''}")
        return {"count": n}

    return jobs.submit(JOB_KIND, case_id, work)
