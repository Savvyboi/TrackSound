# Lightning as a geolocation source: research notes

Status: first version implemented (thunder and flash detection, GLM and CSV catalogues,
solver in three clock modes). See [Implementation](#implementation) at the end for what
was built, how it behaves on synthetic and real data, and its limits. The questions below
are still open for real recordings.

Goal of the M5 spike: decide whether lightning can locate a recording, how
accurately, and with which data.

## The idea

- Storm recordings contain thunder.
- Lightning-detection networks record each strike's time (milliseconds or
  better) and position.
- Sound travels at about 343 m/s (about 3 s per km). Light and radio arrive
  effectively instantly.

So once the recording's clock is known, each thunder clap matched to a
catalogued strike gives the distance from the listener to that strike. With
enough matched strikes, the clock itself falls out of the solution. This is the
same maths as GPS, which solves position from ranges with an unknown receiver
clock. Here the "satellites" are strikes and the signal is sound.

It fits the plugin model in [PLAN.md section 7](../PLAN.md#7-designed-for-more-sources)
directly: a detector (thunder onsets), a catalogue (strikes), a forward model
(arrival times) and a scorer.

## Observation equations

The unknowns are the listener position **x** (2D, with elevation from the
terrain model) and the offset **T₀** between recording time and UTC.

| Observation in the recording | Equation | What it gives |
|---|---|---|
| Thunder onset at recording time `a_k`, from strike *j* at position `p_j`, time `t_j` | `a_k + T₀ = t_j + ‖x − p_j‖ / c + ε` | Range to the strike (once T₀ is known) |
| Lightning flash visible in video at frame time `f_k` | `f_k + T₀ = t_j` | T₀ directly; flash-to-thunder delay gives the range with no catalogue at all |
| Sferic click (radio pulse picked up by the recorder's electronics), if present | `s_k + T₀ = t_j` | T₀ directly |

How many observations are needed:

- **Thunder only:** at least 3 matched onsets solve for **x** and T₀.
- **With a flash or sferic:** that fixes T₀, so 2 thunder onsets are enough.
- More observations always add robustness.

**Open question:** how often ordinary devices record sferics at all. This is
unverified; check it in the spike.

## Pipeline sketch

1. **Detector:** thunder-onset detection.
   - Look for broadband low-frequency transients with a sharp energy rise,
     followed by a long rumble.
   - In video, also detect flash frames (sudden global brightness jumps).
   - Output onset times, an uncertainty for each, and loudness.
2. **Catalogue:** strokes or flashes for the region ± a time window. Each has a
   location error, a type (cloud-to-ground or in-cloud) and peak current where
   available.
3. **Forward model:** predicted arrival time = `t_j + range / c`, with c taken
   from temperature (and later wind).
4. **Association and solve.** This is the hard part. An active storm produces
   several strikes a minute, and thunder claps overlap. Options:
   - RANSAC over (onset, strike) triples: solve for (**x**, T₀) and count
     inliers.
   - A grid over **x** with a T₀ sweep and a robust (Huber) residual score. This
     mirrors the aircraft search and reuses its grid and map UI.
   - Loudness versus distance as a weak prior.
   - **First-arrival bias.** The onset comes from the *nearest* part of the
     lightning channel. For long or in-cloud channels that can be km closer
     than the catalogued point, so the model should allow for it.

## Accuracy budget

The catalogue's location error usually dominates.

| Network | Coverage | Access | Location error (approx., published) | Notes |
|---|---|---|---|---|
| GOES GLM (GOES-18 West, GOES-19 East; GOES-16 for older dates) | Americas and the adjacent oceans | Free, AWS Open Data, `GLM-L2-LCFA` NetCDF files every 20 s | GOES-19 provisional review: most probable ~3.7 km; requirement 5 km; parallax near the edges of the view | Optical, total lightning; flash/group centroids rather than ground strike points; timing error about 1 ms |
| MTG-I1 Lightning Imager (LI-2-LFL / LGR) | Europe, Africa, Atlantic | EUMETSAT Data Store, registration required; licence still to check | Similar class to GLM (km) | Distributed since July 2024 |
| Blitzortung.org | Global volunteer network, densest in Europe | Public maps show only about the last 2 h. The archive is limited to station operators or granted by permission, non-commercial | Depends on network density | Not an "official" source; would need an agreement |
| Commercial ground networks (Vaisala NLDN/GLD360, Earth Networks ENTLN, nowcast LINET) | Regional or global | Paid licences | Sub-km for dense regional networks | Best accuracy, at a cost |
| WWLLN | Global | Academic licence | Several km | |

Other error terms:

- **Onset picking:** about 0.1–1 s, which is about 30–300 m of range.
- **Speed of sound:** about 0.6 m/s per °C, plus wind of several m/s. That's a
  1–3 % range error, or 100–300 m at 10 km.
- **Audible range:** thunder is usually audible to about 10–15 km, up to about
  25 km in quiet conditions. Only nearby strikes contribute.

**Takeaway:**

- With free satellite data (GLM, MTG-LI), lightning should give a fix to a few
  km, plus the recording's absolute time.
- Sub-km accuracy needs a ground network (commercial, or Blitzortung by
  agreement).
- The strongest early use may be as a complement to aircraft. Lightning supplies
  the clock and a region prior, which turns the aircraft search into the cheap
  single-event, known-time mode.

## Questions for the spike

1. How often do ordinary recordings (phones, outdoor cams, dashcams) capture
   thunder onsets clearly enough to time them? How often do they capture flashes
   or sferics?
2. How complete is GLM for strikes within about 15 km of a listener, compared
   with ground networks?
3. How robust is association in an active storm? Simulate it with real GLM data
   and synthetic onsets.
4. Can Blitzortung grant archive access for this use, and on what terms?
5. Where do we get ground truth? Our own storm recordings with GPS and an
   accurate clock, and public weather cams with known positions and timestamps.

## Data access to check first

- **GLM:** public S3 buckets `noaa-goes18`, `noaa-goes19` (and `noaa-goes16`
  for older dates), prefix `GLM-L2-LCFA/`. No account needed.
  [NOAA catalogue entry](https://catalog.data.gov/dataset/noaa-goes-r-series-geostationary-lightning-mapper-glm-level-2-lightning-detection-events-g),
  [GOES-19 GLM provisional readme](https://noaasis.noaa.gov/pdf/ps-pvr/goes19/GLM/Provisional/GOES-19_GLM_Provisional_ReadMe.pdf).
- **MTG LI:** EUMETSAT Data Store, collections LI-2-LFL (flashes) and LI-2-LGR
  (groups). See the MTG-LI Level 2 data guide on user.eumetsat.int.
- **Blitzortung:** contact the project. The forum discussions of historical data
  access are [thread 3588](https://forum.blitzortung.org/showthread.php?tid=3588)
  and [thread 2628](https://forum.blitzortung.org/showthread.php?tid=2628).

## Implementation

Code: `backend/src/tracksound/sources/lightning/` (pure modules) and
`backend/src/tracksound/api/lightning.py` (the endpoints in [../API.md](../API.md#lightning-source-thunder-blasts-and-other-known-time-events)).
Tests: `backend/tests/test_lightning_*.py`.

| Module | What it does |
|---|---|
| `thunder.py` | Thunder onset detection in the case audio; envelope data for `GET /envelope` |
| `flashes.py` | Flash detection in video (ffmpeg `signalstats`) |
| `glm.py` | GOES GLM L2 flashes from NOAA's AWS buckets: listing, download, cache, decoding |
| `catalogue.py` | Strike records, CSV import, array views for the solver |
| `flash_sync.py` | Recording clock from flashes coinciding with strike times |
| `solve_core.py` | Observation model and score, vectorised over grid points |
| `solve.py` | Modes, coarse-to-fine search, polish, uncertainty, notes |
| `state.py` | JSON state under `<case>/lightning/` |
| `synth.py`, `demo.py` | Synthetic storms (tests) and the demo case |

### Thunder onsets (`thunder.py`)

1. **Envelopes**, streamed in 2-minute chunks (constant memory): 4th-order Butterworth
   band-passes "low" 20–200 Hz and "mid" 200–1000 Hz on the 4 kHz audio, mean square per
   50 ms frame. Cached in `lightning/envelope.npz` and rebuilt when `audio.wav` changes.
   A 3-hour file takes about 1 s and 120 MB.
2. **Onset strength** (dB), for the low band and the broadband (low + mid) level: mean level
   over the next 1 s minus the background. The background is the lower of the median over
   the previous 6 s (robust to clicks) and the mean over the previous 0.75 s (follows a
   decaying rumble, so a new clap during it still shows as a step). The detection function
   is the larger of the two steps.
3. **Peaks** above `max(min_db, median + k·MAD)` with `k = 2 + 6·(1 − sensitivity)` and
   `min_db = 3 + 6·(1 − sensitivity)` (6 dB at the default 0.5), non-maximum suppression
   within `min_gap_s`. Each peak is moved back to the start of its plateau (the step stays
   high for seconds after an onset).
4. **Thunder checks**: the low band must rise by at least `max(2 dB, 0.4·threshold)` (birds,
   speech and rain sit higher) and stay up: the median low-band level 0.25–1.5 s after the
   onset must be `max(2 dB, 0.3·step)` above the background (rejects clicks, drops, slams).
5. **Refinement** on the raw audio at 10 ms resolution with zero-phase filters: from the
   loudest point near the detection, walk back through the half-rise point to where the level
   falls to background + `max(1.5 dB, 15 % of the rise)`.
   - `strength` = rise in dB above the background.
   - `sigma_s = clip(0.06 + 0.25·rise_time + 0.3·max(0, (12 − strength)/12), 0.06, 2)`:
     sharp, loud claps are timed to about 0.1 s, slow or faint rises get up to 2 s.
   - `duration_s` = time until the 1 s smoothed level is back within
     `max(3 dB, 25 % of the rise)` of the background for over 1 s (at most 90 s).

On synthetic storms (rain, wind, drop clicks, door slams, a passing truck, 25–30 thunders
from 1.5–18 km) about 80 % of the onsets that are not overlapped by an earlier rumble are
found, timed to 0.03–0.06 s RMS. Door slams and drops are rejected. Slow low-frequency
rumbles (trucks) and strong wind gusts can pass, as can thunder-like sounds in general:
detections are *candidates* for the user to review, and the solver tolerates unexplained
onsets. A clap arriving within the loud part of an earlier rumble is usually missed.

### Flashes (`flashes.py`)

- ffmpeg decodes the first video stream at 64 px width; `signalstats` gives each frame's mean
  luma (YAVG) and 90th-percentile luma (YHIGH, which reacts when only the sky lights up).
  The trace is cached in `lightning/luma.npz`.
- A flash frame jumps above the trailing median of the previous 1.5 s by more than
  `max(floor, k·noise)` in YAVG (or 1.5 × that in YHIGH), with `k = 4 + 8·(1 − sensitivity)`,
  `floor = 2 + 6·(1 − sensitivity)` luma units, and noise from the frame-to-frame MAD.
- Bright frames less than 0.3 s apart are one flash (strokes flicker). The flash is timed at
  its first bright frame. `strength` is the largest jump.
- A flash must fade: within 2 s the frame must be back below half of the jump, and a flash
  lasts at most 3 s. This rejects lamps switched on and scene cuts.
- **Clock alignment:** recording time 0 is the first decoded audio sample, so frame times are
  shifted by `audio start_time − earliest stream start_time` (ffprobe). This was checked with
  PCM and AAC files whose audio was delayed by 0.5 s (AAC's encoder priming included).

Limits: daylight flashes are faint; auto-exposure can stretch or split a flash; photo flashes
and headlights look the same (the solver ignores flashes that match no strike).

### GLM (`glm.py`)

- **Listing:** plain HTTPS ListObjectsV2 on
  `https://noaa-goesNN.s3.amazonaws.com/?list-type=2&prefix=GLM-L2-LCFA/YYYY/DDD/HH/`, with
  continuation tokens. File start and end times come from the `_s…_e…` name parts. Listings
  of hours that ended more than 3 h ago are cached.
- **Download:** 8 threads, one `requests.Session` per thread, retries with back-off, atomic
  writes to `<cache_dir>/glm/<bucket>/<key>`, sizes checked against the listing. About 3 s
  for 30 minutes (92 files, ~30 MB) through the proxy here; a cached refetch takes 0.5 s.
- **Decoding** with h5py, applying `_Unsigned`, `_FillValue`, `scale_factor`, `add_offset`
  by hand. Checked against real files:
  - Current files: `flash_time_offset_of_first_event` is int16 with `_Unsigned = true`,
    scale 0.00038148 s, offset −5 s, units `seconds since <file start>`.
  - 2018-era files: int16 without `_Unsigned`, scale 2, units
    `milliseconds since <file start>`. They also contain junk "flashes" timed ~25 s before
    the file near the equator, so flashes more than 10 s outside the file's own span are
    dropped.
  - `product_time` is seconds since 2000-01-01 12:00:00 UTC without leap seconds
    (`unix = product_time + 946728000`, checked against `time_coverage_start`). It can be
    −999 in empty files, so the epoch is read from the variable's `units` instead.
- **Strikes:** one per GLM flash, `kind: "flash"`, `sigma_m` 4000 m (quality flag 0) or
  6000 m (degraded flags), `energy` in joules (typically tens of femtojoules),
  `source: "glm-G16"` and so on.
- **Satellite "auto":** the slot whose sub-satellite point is nearer the region (East
  75.2° W, West 137.2° W), then the operational satellite of that slot for the date, then its
  other satellite, then the other slot if it still sees the region (±64° of longitude, ±57° of
  latitude). It is chosen per hour, so a missing hour falls back to the next candidate.
  `east`, `west` and `G16`…`G19` force a choice. Archive coverage, from listing the buckets in
  November 2026:

  | Satellite | Slot | GLM files on AWS | Operational |
  |---|---|---|---|
  | GOES-16 | East | 2018-02-13 to 2025-04-07 | East until 2025-04-07 |
  | GOES-17 | West | 2018-08-22 (sparse), 2019-02-07 to 2023-01-10 | West 2019-02 to 2023-01-04 |
  | GOES-18 | West | from 2022-09-19 | West from 2023-01-04 |
  | GOES-19 | East | from 2025-01-15 | East from 2025-04-07 |

  A region outside the field of view, a date before 2018 or an hour range with no files at
  all gives a clear 400 or job error ("no GLM files found for … (GOES-19)…").

### CSV catalogues (`catalogue.py`)

- **Required:** `time` (ISO-8601, naive = UTC, or unix s/ms/µs), `lat`, `lon`.
- **Optional:** `sigma_m` (or `sigma_km`; default 500 m), `energy` (or a peak current), `kind`.
- **Headers:** case and units in brackets are ignored, and common aliases are understood:
  latitude/longitude, timestamp, peak_current, type, a separate `date` column joined to a
  time of day, and a cloud flag 0/1 meaning cg/ic.
- **Values:** longitudes 180–360 are wrapped. The delimiter is sniffed.
- **Bad rows:** rows with an unreadable time or position are skipped; a file with no usable
  row is a 400.

### Solver (`solve_core.py`, `solve.py`)

**Model.** Unknowns are the position x (elevation ignored) and the clock T = UTC of recording
time 0. For onset k (time `a_k`, `sigma_k`) and strike j (`t_j`, `p_j`, `sigma_j`), with
`c` from the case temperature:

```
r = a_k + T − t_j − |x − p_j| / c
σ_late²  = σ_k² + (σ_j/c)² + (0.01·|x − p_j|/c)² + σ_grid² + 0.05²
σ_early² = σ_late² + (e_j/c)²      e_j = 250 m (cg), 1500 m (ic, GLM flash), 800 m (unknown)
K = exp(−u²/2) · taper(d)          u = r/σ_late if r ≥ 0 else r/σ_early
```

The early side is wider because the first sound comes from the nearest part of the channel,
which can be km closer than a GLM centroid. `taper` is a logistic roll-off at
`max_range_km` with width max(300 m, 3 % of the range).

**Score.** `S = min(Σ_k max_j K, Σ_j max_k K) − W·Σ_j missed_j` is a soft count of
explained onsets. The `min` keeps one strike from explaining two onsets, or one onset two
strikes.

The second term is the "silence" check. `missed_j = close_j · inside_j · (1 − heard_j)`
counts strikes that are surely close, whose thunder should have started inside the
recording, but which have no onset near their predicted arrival:

- `close`: 1 if the distance plus `sigma_j` is under 3 km, falling to 0 at 8 km.
- `inside`: the predicted arrival is more than 3 s from either end of the recording.
- `heard`: a Gaussian with 2 s tolerance on the distance to the nearest onset.

`W` is 0.5 when the onsets come from the detector, and 0.1 when every onset was placed by
hand, since a user may mark only the clearest few.

**Onsets used:** accepted ones. If none are accepted, all candidates are used (with a note).
Rejected onsets are never used.

**Modes and clock intervals:**

- `known_start` searches T in start ± `start_tol_s`, with a Gaussian prior of that width
  in the polish.
- `flash_sync` takes candidate clocks from flash–strike coincidences (`flash_sync.py`):
  - **Votes:** each (flash, strike) pair votes `t_j − f_k`. The count for an offset is the
    number of *distinct* flashes with a strike within 0.25 s.
  - **Candidates:** offsets with the best count (or one less), one per cluster, ranked by
    spread. Each gets σ = spread/√(n−1), at least 20 ms.
  - **One usable flash:** the nearest 40 strikes in the region are each tried as its source.
  - **Choosing between them:** each candidate is searched within ±2σ. A coinciding flash
    adds 1 to the score, like a matched onset, so the thunder decides between candidates
    that fit the flashes equally well. With a dense catalogue, chance offsets can match as
    many flashes as the true one.
- `free` searches T over the whole catalogue span, limited to the case date ± 1 h if set.

**Search.**

1. **Coarse grid:** at most 6000 / 3000 / 2500 points (known / flash / free) over the
   region, with `σ_grid = 0.5·spacing/c`.
2. **Clock voting per point** (a Hough transform): every (onset, strike) pair votes for the
   interval of T where it would match within 1 σ. Box votes are added with a cumulative sum
   over bins of about σ/2. The best 1 (or 3 in free mode) peaks are scored exactly, then one
   mean-shift step on T is tried.
3. **Refine:** each distinct peak (at least 2 km or 5 s apart, at most 4 or 6 of them) is
   searched again on a fine grid of `spacing_m` (at most 70 × 70 points, window
   ±max(2.5 coarse cells, 1.5 km)).
4. **Polish:** Gauss-Newton weighted least squares on the matched pairs with Huber weights,
   re-associating between 3 rounds. The soft count with a 1σ kernel finds the basin but is a
   poor estimator; least squares is the efficient one. The polished point is kept unless the
   score drops by more than max(0.5, 5 %).
5. **Pair pruning:** pairs are limited by time and space (strikes within range of a block of
   points) and blocks are sized to about 1.5 M point-pair elements. A 6-hour, 20 000-strike
   catalogue solves in free mode in about 15 s, a demo-size problem in under 1 s.

**Outputs.**

- `best` and the distinct refined `candidates`.
- `heat`: the coarse and fine grid scores above 0.05, at most 20 000 points.
- `matches`: one-to-one pairs within 2σ at the best point; their count is `n_matched` and
  their RMS residual is `rms_s`.
- `flash_matches`: the strike nearest in time within 0.25 s of each accepted flash.
- `ranges`: c × (onset − flash) for flashes whose strike is also matched to an onset.
- `notes`.

**Uncertainty** (`uncertainty_m`, a radius the truth is probably within) is the larger of:

- the least-squares 1σ semi-major axis × (1 + mean number of *other* strikes that fit each
  matched onset);
- the farthest fine-grid point scoring within 1.15 of the best;
- half the grid spacing.

It is capped at the region's half-diagonal. When matched onsets have on average 1.5 or more
alternative strikes, the matches are "not distinctive": the coarse-grid extent within 1 of
the best is used too, and a note says so. In 20-storm simulations the radius covered the
true position in 70–90 % of cases, both for 300 m and for 4 km catalogues. The
least-squares axis alone covered only 30 % for 4 km catalogues, because of misassociations.

**Notes** also flag:

- the GLM accuracy;
- fewer than 3 matches (or fewer than 5 with a free clock);
- unexplained onsets;
- close strikes that were not heard;
- a runner-up candidate at another place or clock with ≥ 85 % of the score;
- a best point at the region edge;
- the estimated start time.

### Results

Synthetic storms: 40 strikes over 20 minutes drifting past the listener, about 27 onsets
picked to 0.1 s plus 2 false ones, and 20 random storms per row.

| Catalogue | Clock | Median error | 90th percentile |
|---|---|---|---|
| 300 m (ground network) | known ±5 s | 136 m | 240 m |
| 300 m | free | 137 m | 240 m |
| 4 km (GLM-like) | known ±5 s | 2.7 km | 6.1 km |
| 4 km | free | 2.7 km | 7.4 km |
| 4 km, true associations given (least squares, free clock) | — | 2.0 km | 3.2 km |

The recording clock comes out too:

- **300 m strikes:** median error 0.2 s, 90 % within 0.35 s.
- **GLM-like strikes:** median error 3–4 s, 90 % within 5–10 s (4 km is 12 s of sound).

**Demo case** (`demo.py`, via the HTTP API): 24 onsets detected, 21 matched; the free-mode
solve lands **75 m** from the true position with the clock **0.56 s** off, in 0.6 s.

**Real GLM through the API:**

- GOES-16, 2024-07-15 20:00–20:30 UTC, near Jacksonville, FL: 2 060 flashes in the region
  padded by 50 km, 892 inside the region (about 30 per minute).
- GOES-19, 2025-07-19 20:00–20:30 UTC, all of Florida: 1 909 flashes; the busiest 1° cell
  had 27 per minute.

Times and positions were inside the window and box. A cached refetch takes 0.5 s.

**The main limitation, shown with real data.** A listener was simulated inside the real
2024-07-15 storm. The thunder came from the GLM flashes, with the true strike placed 4 km
from the centroid and audibility falling with distance. Solving against the real GLM
catalogue did not localise: with about 30 flashes a minute nearby and 12 s of timing
tolerance per flash, each onset also fits 10–17 other strikes almost anywhere. The solver now
reports this case as ambiguous, with an uncertainty the size of the region, instead of a
confident wrong answer.

What the source can expect:

- **GLM in an active storm:** a known start time narrows the clock but not the 12 s per-strike
  tolerance, so it does not fix this.
- **GLM in isolated cells:** a few km, as in the table.
- **Ground-network catalogues:** a few hundred metres.

Weighting matches by a distance-dependent detection probability was tried and did not help.

### Known-time events beyond lightning

The same detector, forward model and solver handle any sharp sound with a recorded time and
place (survey sections 4.1–4.3; `events.py`, `usgs.py`). The tab is still "Lightning".

- **USGS ComCat** (`source: "usgs"`): quarry and mining blasts, explosions, sonic booms,
  meteors and earthquakes from the FDSN event service.
  - `format=csv` is used because, as checked on 2026-11-04, only the CSV carries the per-event
    `horizontalError`; the GeoJSON summary has no error fields.
  - Friendly type names expand to ComCat's spelling variants (`quarry_blast`, `sonicboom` …).
  - Results are paged by 20 000. `sigma_m` is the horizontal error (at least 100 m), else
    1000 m. Windows of up to 31 days are allowed, since these events are sparse.
  - A live query of southern California, 2024-06-01 to 06-08, returned 7 quarry blasts with
    0.38–0.41 km errors.
- **Manual events:** `POST /strikes` adds a user-supplied event (a reported explosion) with
  `kind: "event"` (a point source) and `sigma_m` 250 m by default. Catalogue fetches and
  imports keep manual events.
- **Propagation by kind:**
  - Air waves (thunder, blasts, explosions, booms) arrive at `t₀ + d/c`.
  - Earthquakes are heard from the P-wave arrival, at `t₀ + √(d² + depth²)/6 km/s` with a
    5 % speed tolerance. They fix the clock well and the position only weakly (1 km is
    0.17 s), so they are left out of the silence check and the flash-to-bang ranges.
  - Sonic booms and meteors get a 3–5 km position floor and a wide early tolerance, since
    they are not point sources.
- **One event:** a single event with a flash (or a known start time) fixes the absolute clock
  and the distance: a range ring.
  - With the flash, the clock is taken from it exactly.
  - `uncertainty_m` grows to the ring's extent (about its diameter), and a note explains that
    more events or a smaller region are needed.
  - Tested: a reported explosion 6 km away gave the start time to 0.02 s and a ring 65 m from
    the true radius.

### Known limits and next steps

- Elevation, wind and temperature profiles are ignored (1 % travel-time tolerance only).
- The first-arrival allowance is a fixed early-side widening per kind; it does not use GLM
  flash extent or event-level geometry.
- No loudness-to-distance prior yet. `strength` and the rise time carry distance information
  that could cut the ambiguity in dense storms.
- Sferics are not detected. MTG-LI (Europe and Africa), Blitzortung, EMSC, launch/landing and
  fireball catalogues are not wired in.
- Thunder onsets inside an earlier loud rumble are mostly missed; trucks and gusts can be
  detected. Review the candidates before solving.
