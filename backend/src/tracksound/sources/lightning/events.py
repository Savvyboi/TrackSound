"""Known-time impulsive events: their kinds and how their sound reaches the listener.

The lightning machinery works for any sharp sound whose source time and place are recorded
somewhere: thunder, quarry and mining blasts, explosions, sonic booms, and felt earthquakes.

- **Air waves** (thunder, blasts, explosions, booms) travel at the speed of sound c from the
  case temperature: arrival = t0 + distance / c.
- **Earthquakes** (and similar ground events) are heard from the moment the P wave arrives
  (Hill et al. 1976: 50-70 Hz sound within 0.02 s of P for M2-3 events 7-8 km away). The P
  wave travels through the crust at about 6 km/s from the hypocentre:
  arrival = t0 + sqrt(distance^2 + depth^2) / Vp. Its arrival time depends only weakly on
  distance (1 km = 0.17 s), so an earthquake fixes the clock far better than the position.

Moving sources (sonic booms, meteors) are treated as points with a wide tolerance; their
real arrival pattern depends on the trajectory.
"""
from __future__ import annotations

VP_M_S = 6000.0                 # crustal P-wave speed
SEISMIC_REL = 0.05              # relative travel-time error through the ground (Vp is uncertain)
ACOUSTIC_REL = 0.01             # ... through the air (temperature, wind)

SEISMIC_KINDS = frozenset({"earthquake", "induced or triggered event", "ice quake", "rock burst"})

# spelling variants used by USGS ComCat -> one name
KIND_ALIASES = {
    "eq": "earthquake", "quarry": "quarry blast", "quarry_blast": "quarry blast", "sonic_boom": "sonic boom",
    "sonicboom": "sonic boom", "mining_explosion": "mining explosion", "chemical_explosion": "chemical explosion",
    "nuclear_explosion": "nuclear explosion", "rock_burst": "rock burst", "meteorite": "meteor",
    "mine_collapse": "mine collapse", "other_event": "other event", "acoustic_noise": "acoustic noise",
    "snow_avalanche": "snow avalanche", "rockslide": "rock slide",
}

# Extra tolerance (m) for early arrivals: the first sound comes from the nearest part of the
# source. Lightning channels and GLM flash centroids can be km from that; a blast is a point.
EARLY_EXTRA_M = {"cg": 250.0, "ic": 1500.0, "flash": 1500.0, "event": 100.0,
                 "quarry blast": 150.0, "mining explosion": 150.0, "explosion": 150.0, "chemical explosion": 150.0,
                 "experimental explosion": 150.0, "industrial explosion": 150.0, "accidental explosion": 150.0,
                 "nuclear explosion": 300.0, "sonic boom": 3000.0, "meteor": 3000.0}
EARLY_EXTRA_DEFAULT_M = 800.0

# Position-error floor (m) for sources that are not really points.
MIN_SIGMA_M = {"sonic boom": 3000.0, "meteor": 5000.0}


def canonical_kind(kind) -> str | None:
    """Lower-case kind with ComCat's spelling variants merged ('quarry_blast' -> 'quarry blast')."""
    if kind is None:
        return None
    k = str(kind).strip().lower()
    if not k:
        return None
    return KIND_ALIASES.get(k, k)


def is_seismic(kind) -> bool:
    return canonical_kind(kind) in SEISMIC_KINDS
