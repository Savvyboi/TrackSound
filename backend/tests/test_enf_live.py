"""Downloads a small real slice from each built-in ENF reference (network; run with -m slow)."""
from __future__ import annotations

import datetime as dt
import time

import numpy as np
import pytest

from tracksound.sources.enf import references as R

pytestmark = pytest.mark.slow


def _utc(s: str) -> float:
    return dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp()


def _recent(days_ago: int, hour: int = 10) -> float:
    d = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=days_ago)
    return dt.datetime(d.year, d.month, d.day, hour, tzinfo=dt.timezone.utc).timestamp()


CASES = [
    ("gb-neso", _utc("2026-09-14T10:00:00")),
    ("gb-neso", _utc("2025-07-15T11:00:00")),          # a month whose file is on UTC+1
    ("gb-neso", _utc("2019-03-05T11:00:00")),          # a month whose datastore dates are mangled
    ("gb-elexon", _recent(3)),
    ("nordic-fingrid", _utc("2026-09-14T10:00:00")),   # downloads a whole month (about 60 MB)
    ("au-nem-aemo", _recent(5, 2)),
    ("au-tas-aemo", _recent(5, 2)),
    ("pgf-us-texas-2019-05a", _utc("2019-05-15T12:00:00")),
]


@pytest.mark.parametrize("ref_id,t0", CASES)
def test_builtin_reference_downloads(settings, ref_id, t0):
    info = R.get_info(settings, ref_id)
    s = R.load_reference(settings, ref_id, t0, t0 + 3600)
    assert len(s.values) == 3600 and s.t0 == int(t0)
    assert s.valid_fraction() > 0.9, s.notes
    v = s.values[np.isfinite(s.values)]
    assert abs(np.median(v) - info.nominal_hz) < 0.3 and 0.0005 < np.std(v) < 0.3
    t = time.time()
    R.load_reference(settings, ref_id, t0, t0 + 3600)                    # second time: from the cache
    assert time.time() - t < 5


def test_neso_summer_2025_is_corrected_to_utc(settings):
    from tracksound.sources.enf.references import elexon
    t0 = _utc("2025-07-15T11:30:00")
    s = R.load_reference(settings, "gb-neso", t0, t0 + 3600)
    te, fe = elexon.fetch_samples(t0, t0 + 3600)
    fi = np.interp(te, s.t0 + np.arange(len(s.values)), s.values)
    assert np.corrcoef(fi, fe)[0, 1] > 0.98
