# TrackSound local API

The web UI talks to a Python server running on the same machine (`tracksound serve`,
default `http://127.0.0.1:8000`). Everything is under `/api`. Interactive docs are served
at `/api/docs`.

This file is the contract between the frontend and the backend. If you change an endpoint,
change this file in the same commit.

## Conventions

- **Recording time:** seconds from the start of the recording, as a float. Field names are
  `t`, `t_start`, `t_cpa` and so on.
- **UTC time:** unix seconds, as a float. Field names end in `_utc`, for example `start_utc`.
- **Coordinates:** WGS-84 degrees in `lat` and `lon`. Distances in metres, field names end
  in `_m`.
- **Region / bbox:** `{ "lat_min": number, "lat_max": number, "lon_min": number, "lon_max": number }`.
- **IDs:** short strings with a prefix: `c_` case, `j_` job, `ae_` aircraft event, `as_`
  aircraft search, `lo_` thunder onset, `lf_` flash, `st_` strike or known event, `ls_`
  lightning solve, `em_` ENF match, `er_` imported ENF reference, `sh_` alert (SAME) header.
- **Errors:** JSON `{ "detail": ... }`.
  - 400: bad input.
  - 404: missing.
  - 409: conflict, for example a job is already running.
  - 422: request validation failed. `detail` is then a list.
- **Long operations** return a **Job**. Poll `GET /api/jobs/{id}` about once a second until
  `status` is `done`, `error` or `cancelled`, then refetch the resource it changed. A job's
  `result` holds only small data such as ids and counts.
- **Speed of sound** is `c = 331.3 * sqrt(1 + T / 273.15)` m/s, where
  `T = case.temperature_c ?? 15`.

## Types

```ts
type BBox = { lat_min: number; lat_max: number; lon_min: number; lon_max: number };

type Case = {
  id: string;
  name: string;
  notes: string;
  created_at: number;            // unix s
  updated_at: number;
  date: string | null;           // "YYYY-MM-DD": the UTC day the recording was made (used for ADS-B)
  timezone: string | null;       // IANA name, for display, e.g. "America/New_York"
  region: BBox | null;           // search region (optional when countries are set)
  countries: string[];           // where the recording is known to be from: ISO 3166-1 alpha-2 codes; [] = anywhere
  start_utc: number | null;      // recording start time (unix s) if known or estimated
  start_utc_source: "user" | "aircraft" | "lightning" | "enf" | "alerts" | null;
  temperature_c: number | null;  // air temperature; null means 15 °C
  recording: Recording | null;
};

type Recording = {
  filename: string;
  size_bytes: number;
  uploaded_at: number;
  status: "processing" | "ready" | "error";
  error: string | null;
  duration_s: number | null;
  has_video: boolean;
  video_fps: number | null;
  spectrogram: SpectrogramInfo | null;
};

type SpectrogramInfo = {
  n_frames: number;   // STFT frames (columns) at level 0
  n_bins: number;     // frequency bins (rows)
  t0: number;         // recording time of frame 0, s
  dt: number;         // frame spacing, s (0.25)
  f0: number;         // frequency of bin 0, Hz (about 10; older cases 40)
  df: number;         // bin spacing, Hz (about 0.49)
  tile_frames: number;// columns per tile (1024)
  levels: number;     // pyramid levels; level z has one column per 2**z frames
  db_min: number;     // byte 0 means db_min (whitened dB above the per-bin median)
  db_max: number;     // byte 255 means db_max
};

type Job = {
  id: string;
  case_id: string | null;
  kind: string;                  // e.g. "recording.process", "aircraft.detect", "lightning.solve"
  status: "queued" | "running" | "done" | "error" | "cancelled";
  progress: number;              // 0..1
  message: string;               // human-readable status
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  result: any | null;
  error: string | null;
};
```

## General

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/api/health` | | `{ ok: true, version, data_dir }` |
| GET | `/api/jobs?case_id=` | | `Job[]`, newest first |
| GET | `/api/jobs/{job_id}` | | `Job` |
| POST | `/api/jobs/{job_id}/cancel` | | `Job` |

## Cases and recordings

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/api/cases` | | `Case[]`, most recently updated first |
| POST | `/api/cases` | `{ name }` | `Case` (201) |
| GET | `/api/cases/{id}` | | `Case` |
| PATCH | `/api/cases/{id}` | any of `name, notes, date, timezone, region, countries, start_utc, start_utc_source, temperature_c` | `Case` |
| GET | `/api/cases/{id}/area` | | `SearchArea`: the area every search covers (below) |
| DELETE | `/api/cases/{id}` | | 204 |
| POST | `/api/cases/{id}/recording` | multipart form, field `file` | `{ case: Case, job: Job }`; the job decodes the file and builds the spectrogram |
| GET | `/api/cases/{id}/spectrogram/tile?level=z&index=i` | | binary tile (below); tiles are cached for an hour, so the UI adds `&v=<recording.uploaded_at>` (ignored by the server) to bust the cache after a re-upload |
| GET | `/api/cases/{id}/audio?t0=&t1=&speed=1` | at most 600 s | `audio/wav` |
| GET | `/api/cases/{id}/report` | | JSON with the case, every source's state and best results (for export) |
| GET | `/api/cases/{id}/weather` | needs `date` and `region` | `{ source, lat, lon, date, hourly: { t_utc, temperature_c }[], suggested_c, basis, sound_speed_mps, attribution }` from the Open-Meteo archive. The UI offers "use suggested_c" and PATCHes `temperature_c`. 502 if unavailable |

Notes:

- Setting `start_utc` without `start_utc_source` records `"user"` as the source. Setting
  `start_utc: null` clears both.
- **Spectrogram tile format:**
  - The body is raw `uint8` in row-major order, with `X-Rows` rows (frequency bins, row 0 is
    the lowest frequency) and `X-Cols` columns (time).
  - Tile `i` at level `z` covers level-0 frames `[i * tile_frames * 2**z, (i + 1) * tile_frames * 2**z)`.
    The header `X-Frame0` is its first level-0 frame and `X-Frames-Per-Col` is `2**z`.
  - Each column at level `z` is the max over its `2**z` frames.
  - Column `k` of the tile sits at recording time `t0 + (X-Frame0 + k * 2**z) * dt`.
  - Bytes map linearly to dB: `db = db_min + byte / 255 * (db_max - db_min)`.
- **Audio playback:** `speed` from 0.25 to 16 resamples the clip for playback. `speed=4`
  plays 4× faster and two octaves higher, which makes 100 Hz aircraft tones and mains hum
  audible.

### Ideal Doppler curve (for drawing aircraft events)

Port of `dopplertrack.doppler.ideal_curve`. For an event with `f0, v, d, t_cpa_emit`:

```
for te in t_cpa_emit + linspace(-600, 600, 1201):
    r     = sqrt(d^2 + (v * (te - t_cpa_emit))^2)
    rr    = v^2 * (te - t_cpa_emit) / r        // range rate
    f     = f0 * c / (c + rr)
    t_obs = te + r / c                          // recording time at which f is heard
```

Draw the polyline `(t_obs, h * f)` for each harmonic `h = 1..harmonics`, clipped to
`[t_start - 60, t_end + 60]`.

## Countries (the "recorded in" filter)

If the user knows the country a recording was made in, they set `case.countries`. Every source
then looks only there:

- **Search area.** It is the region, the countries, or the region inside the countries.
  - With countries and no region, searches cover the whole countries.
  - Countries larger than 1,000,000 km² need a region drawn inside them. Searches and
    downloads return 400 otherwise.
  - Outlines are Natural Earth 1:10m, simplified, with a 2 km margin, so coasts and borders
    are not cut off.
- **Aircraft.**
  - Grid points lie only inside the area.
  - The flight download defaults to the area's bbox plus 15 km.
  - The cost estimate also returns `area_km2` and `suggested_spacing_m`.
  - Searches over 2,000,000 grid points are refused with a suggested spacing.
- **Lightning and events.**
  - The strike download defaults to the area's bbox plus 50 km.
  - The solver only places the listener inside the area.
- **Mains hum.**
  - References whose power grid covers the countries are flagged `in_area`.
  - Matching against a reference from another grid adds a note.
  - The country's nominal frequency (50/60 Hz) is in the country data.
- **Alerts.** A note appears when the countries are outside the US and Canada, where SAME is
  used.

```ts
type CountrySummary = {
  code: string;                  // ISO 3166-1 alpha-2 (a few disputed or special areas use Natural Earth's 3-letter code)
  iso3: string; name: string; short_name: string; sovereign: string | null; type: string | null;
  continent: string | null;
  mains_hz: 50 | 60 | "50/60" | null;
  bbox: BBox; n_parts: number; area_km2: number;
  grids: string[];               // synchronous power grids (names match EnfReference.grid), e.g. ["Continental Europe"]
};

type SearchArea = {
  countries: string[]; region: BBox | null;
  bbox: BBox | null;             // what downloads and the map use
  center: { lat: number; lon: number } | null;   // representative point (largest landmass when no region)
  area_km2: number;              // land area searched
  too_large: boolean;            // countries without a region and over 1,000,000 km²: draw a region
  description: string;           // e.g. "Portugal", "the search region inside Portugal"
  grids: string[];               // power grids of the countries (on the case date)
  mains_hz: (50 | 60 | "50/60" | null)[];
};
```

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/api/countries` | | `CountrySummary[]`, sorted by name |
| GET | `/api/countries/{code}` | | GeoJSON `Feature` (the outline), properties `CountrySummary` |
| GET | `/api/enf/references?country=PT&country=ES&date=YYYY-MM-DD` | | `EnfReference[]`, each with `in_area: boolean` when countries are given; matching references first |

## Aircraft source (prop Doppler curves vs ADS-B)

```ts
type AircraftEvent = {
  id: string;
  origin: "detected" | "manual";
  status: "candidate" | "accepted" | "rejected";
  harmonics: number;
  band: [number, number];        // Hz searched for the fundamental
  t_cpa: number;                 // heard closest-approach time (recording s)
  t_cpa_emit: number;            // emission-time CPA (t_cpa - d/c)
  f0: number;                    // rest fundamental, Hz
  v: number;                     // speed, m/s
  d: number;                     // CPA slant distance, m
  t_start: number; t_end: number;// window the curve was fitted on
  score: number;                 // mean whitened dB along the curve
  quality: "strong" | "possible" | "weak";  // heuristic from score: noise fits score ~1, real flyovers 3+
  f_pre: number; f_post: number; // approach / recede asymptotes, Hz
};

type FlightSummary = {
  idx: number;                   // index within this case's flight set
  icao: string; reg: string | null; type: string | null; desc: string | null;
  engine: string | null;         // ICAO Doc 8643 engine class, e.g. "Piston", "Turboprop/Turboshaft"
  category: string | null;       // ICAO Doc 8643 description: "LandPlane", "Helicopter", "Gyrocopter", "SeaPlane", ...
  t_start_utc: number; t_end_utc: number; n_points: number;
};

type FlightSet = {
  status: "none" | "ready";
  date: string | null; bbox: BBox | null; fetched_at: number | null;
  n_flights: number;
  flights: FlightSummary[];
  attribution?: string;          // present when ready: show it wherever flights are drawn (ODbL)
};

type AircraftSearchRequest = {
  mode: "blind" | "point" | "single";
  events: string[];              // blind/point: two event ids; single: one
  spacing_m?: number;            // grid spacing, default 200
  tol_s?: number;                // pairing tolerance, default 6 (blind) / time tolerance 30 (single)
  point?: { lat: number; lon: number };   // mode "point"
  event_utc?: number;            // mode "single": heard CPA time of the event, UTC. Default case.start_utc + event.t_cpa
  use_dem?: boolean;             // use terrain elevation for the listener, default true
  silence?: boolean;             // blind: drop candidates that would have heard another close prop between the events, default true
};

type CandidateFlight = {
  event_id: string;
  flight_idx: number; icao: string; reg: string | null; type: string | null; desc: string | null;
  t_heard_utc: number;           // when this point heard the flight's closest approach
  d_m: number; v_mps: number;    // CPA slant distance and speed
  score: number;                 // this event's score
};

type AircraftCandidate = {
  rank: number;                  // 1 = best
  lat: number; lon: number; elev_m: number;
  score: number;
  start_utc: number;             // implied recording start time
  shift_s: number;
  flights: CandidateFlight[];
};

type AircraftSearch = {
  id: string;
  created_at: number;
  mode: "blind" | "point" | "single" | "refine";
  parent_id: string | null;      // refine: the search it refined
  request: object;
  status: "done" | "error";
  error: string | null;
  bbox: BBox | null;
  stats: { n_points: number; n_segments: number; n_cpa: number; n_candidates: number; elapsed_s: number };
  best: AircraftCandidate | null;
  margin: number | null;         // (best - best other flight pair) / best; null if no runner-up.
                                 // refine: the parent search's margin (neighbouring fine-grid points are not alternatives)
  candidates: AircraftCandidate[];// blind/point/single: best point per flight pair (or flight), ranked. refine: top fine-grid points
  heat: { lat: number; lon: number; score: number }[];   // best score per grid point, at most 20 000 points
  notes: string[];               // plain-language caveats (e.g. the silence filter was switched off); show them
};
```

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/api/cases/{id}/aircraft/events` | | `AircraftEvent[]` sorted by `t_cpa` |
| POST | `/api/cases/{id}/aircraft/detect` | `{ band?: [60, 300], harmonics?: 1, top?: 12, window_s?: 240 }` (band within 10–450 Hz; helicopter main rotors: about [10, 60] with harmonics 3) | `Job`; result `{ count }`. Replaces earlier detected candidates and rejected events; keeps accepted and manual ones |
| POST | `/api/cases/{id}/aircraft/fit` | `{ t_start, t_end, f_lo, f_hi, harmonics?: 1 }` | `Job`; result `{ event_id }`. Adds an accepted manual event |
| PATCH | `/api/cases/{id}/aircraft/events/{eid}` | `{ status?, harmonics? }` | `AircraftEvent` |
| DELETE | `/api/cases/{id}/aircraft/events/{eid}` | | 204 |
| GET | `/api/cases/{id}/aircraft/flights` | | `FlightSet` |
| POST | `/api/cases/{id}/aircraft/flights/fetch` | `{ date?, bbox?, keep_jets?: false }` | `Job`; result `{ n_flights }`. Defaults to `case.date` and `case.region` padded by 15 km. Streams one UTC day of adsb.lol history (about 4 GB; minutes), cached per day and region |
| GET | `/api/cases/{id}/aircraft/flights/geojson?t0_utc=&t1_utc=` | | GeoJSON `FeatureCollection` of `LineString`s, coordinates `[lon, lat, alt_m]`, properties `{ idx, icao, reg, type, desc, category, t_start_utc, t_end_utc, times: number[] }` (unix s per vertex, thinned to about one per 10 s) |
| GET | `/api/cases/{id}/aircraft/flights/{idx}` | | `{ idx, icao, reg, type, t: number[], lat: number[], lon: number[], alt_m: number[] }` full track |
| POST | `/api/cases/{id}/aircraft/search/estimate` | `AircraftSearchRequest` | `{ n_points, n_segments, est_seconds }` |
| POST | `/api/cases/{id}/aircraft/search` | `AircraftSearchRequest` | `Job`; result `{ search_id }` |
| GET | `/api/cases/{id}/aircraft/searches` | | `AircraftSearch[]` without `heat` and with at most 5 candidates, newest first |
| GET | `/api/cases/{id}/aircraft/searches/{sid}` | | `AircraftSearch` |
| DELETE | `/api/cases/{id}/aircraft/searches/{sid}` | | 204 |
| POST | `/api/cases/{id}/aircraft/searches/{sid}/refine` | `{ rank?: 1, spacing_m?: 50, half_m?: 600, extra_events?: string[] }` | `Job`; result `{ search_id }`, a new search with `mode: "refine"` |
| GET | `/api/cases/{id}/aircraft/searches/{sid}/candidates/{rank}/curves` | | `{ events: { event_id, flight_idx, icao, harmonics, t: number[], f: number[] }[] }`: modelled fundamental (Hz) at recording times, for overlaying on the spectrogram |

## Lightning source (thunder, blasts and other known-time events)

The tab is called "Lightning", but the source handles any sharp sound whose time and place
are recorded somewhere: thunder (GLM or an imported catalogue), quarry and mining blasts,
explosions, sonic booms and felt earthquakes (USGS ComCat), and events the user enters (a
reported explosion). Onsets and flashes are matched to these "strikes".

```ts
type ThunderOnset = {
  id: string; t: number;         // recording time of the onset (thunder or another bang)
  sigma_s: number;               // timing uncertainty
  strength: number;              // dB above the local background
  duration_s: number | null;
  origin: "detected" | "manual";
  status: "candidate" | "accepted" | "rejected";
};

type Flash = {
  id: string; t: number;         // recording time of a flash seen in the video (lightning, explosion)
  strength: number;              // brightness jump (0..255 luma units)
  origin: "video" | "manual";
  status: "candidate" | "accepted" | "rejected";
};

type Strike = {                  // a catalogued or user-entered event
  id: string; t_utc: number; lat: number; lon: number;
  sigma_m: number;               // location uncertainty
  energy: number | null;         // GLM: optical energy (J); USGS: magnitude; CSV: as given
  kind: string | null;           // "flash" (GLM), "cg", "ic", "quarry blast", "mining explosion", "explosion",
                                 // "sonic boom", "earthquake", "event" (manual default) ...
  source: string;                // "glm-G16" | "glm-G17" | "glm-G18" | "glm-G19" | "usgs" | "csv" | "manual"
  depth_m?: number;              // source depth when known (USGS); earthquakes use it
  ref?: string;                  // the catalogue's own event id (USGS, CSV "id" column)
};

// source: the catalogue ("glm-G19", "usgs", "csv"), with "+manual" when user-entered events are kept beside it
type StrikeSet = { source: string; t0_utc: number; t1_utc: number; bbox: BBox | null; count: number; fetched_at: number };

type LightningSolveSummary = { id: string; created_at: number; mode: string; best: LightningSolve["best"] };

type LightningSolve = {
  id: string; created_at: number; request: object;
  mode: "known_start" | "flash_sync" | "free";
  best: { lat: number; lon: number; start_utc: number; score: number; n_matched: number;
          rms_s: number; uncertainty_m: number } | null;   // uncertainty_m: radius the truth is probably within
  candidates: { lat: number; lon: number; start_utc: number; score: number; n_matched: number }[];  // distinct peaks, best first
  // Mixed resolution: a coarse grid over the region (cells of a few hundred m to ~1 km)
  // plus finer cells (spacing_m) around each peak. Draw each point as a cell_m square,
  // finer cells over coarser. At most 20 000 points, only scores above 0.05.
  heat: { lat: number; lon: number; score: number; cell_m: number }[];
  matches: { onset_id: string; strike_id: string; residual_s: number; distance_m: number }[];      // at the best point
  flash_matches: { flash_id: string; strike_id: string; residual_s: number }[];
  ranges: { strike_id: string; lat: number; lon: number; distance_m: number }[];  // flash-to-bang ranges, drawn as circles
  notes: string[];               // caveats in plain language
};
```

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/api/cases/{id}/lightning` | | `{ onsets: ThunderOnset[], flashes: Flash[], strikes: StrikeSet \| null, solves: LightningSolveSummary[] }` |
| GET | `/api/cases/{id}/lightning/envelope?t0=&t1=&max_points=2000` | | `{ t: number[], bands: { name, lo_hz, hi_hz, db: number[] }[], onset_strength: number[] }` (max-pooled to at most `max_points`) |
| POST | `/api/cases/{id}/lightning/detect-thunder` | `{ sensitivity?: 0.5, min_gap_s?: 4 }` | `Job`; result `{ count }`. Replaces detected candidates; keeps manual and accepted |
| POST | `/api/cases/{id}/lightning/detect-flashes` | `{ sensitivity?: 0.5 }` | `Job`; result `{ count }`. 400 if the recording has no video |
| POST | `/api/cases/{id}/lightning/onsets` | `{ t, sigma_s? }` | `ThunderOnset` (manual, accepted) |
| PATCH | `/api/cases/{id}/lightning/onsets/{oid}` | `{ t?, sigma_s?, status? }` | `ThunderOnset` |
| DELETE | `/api/cases/{id}/lightning/onsets/{oid}` | | 204 |
| POST | `/api/cases/{id}/lightning/flashes` | `{ t }` | `Flash` (manual, accepted) |
| PATCH | `/api/cases/{id}/lightning/flashes/{fid}` | `{ t?, status? }` | `Flash` |
| DELETE | `/api/cases/{id}/lightning/flashes/{fid}` | | 204 |
| POST | `/api/cases/{id}/lightning/strikes/fetch` | `{ source: "glm", satellite?: "auto", t0_utc, t1_utc, bbox? }` | `Job`; result `{ count }` (events fetched from the catalogue). GOES GLM from NOAA's public AWS buckets (Americas only); at most 6 h. `satellite`: `auto`, `east`, `west`, `G16`…`G19` |
| POST | `/api/cases/{id}/lightning/strikes/fetch` | `{ source: "usgs", event_types?: string[], t0_utc, t1_utc, bbox? }` | `Job`; result `{ count }`. USGS ComCat events (mostly US); at most 31 days. `event_types` from `quarry blast`, `mining explosion`, `explosion`, `sonic boom`, `earthquake`, `meteor` (or another ComCat type); default the first four. `sigma_m` from ComCat's `horizontalError`, else 1000 m |
| POST | `/api/cases/{id}/lightning/strikes/import` | multipart `file`: CSV with columns `time` (ISO-8601 or unix s), `lat`, `lon`, optional `sigma_m`, `energy`, `kind`. A ComCat CSV export also works | `{ count }` |
| POST | `/api/cases/{id}/lightning/strikes` | `{ t_utc, lat, lon, sigma_m?: 250, kind?: "event" }` | `Strike` with `source: "manual"`: a user-supplied event such as a reported explosion |
| GET | `/api/cases/{id}/lightning/strikes` | | `{ strikes: Strike[] }` |
| DELETE | `/api/cases/{id}/lightning/strikes/{sid}` | | 204. Removes one event |
| DELETE | `/api/cases/{id}/lightning/strikes` | | 204. Removes all events |
| POST | `/api/cases/{id}/lightning/solve` | `{ start_utc?: number \| null, start_tol_s?: 5, use_flashes?: true, spacing_m?: 250, max_range_km?: 25 }` | `Job`; result `{ solve_id }` |
| GET | `/api/cases/{id}/lightning/solves/{sid}` | | `LightningSolve` |
| DELETE | `/api/cases/{id}/lightning/solves/{sid}` | | 204 |

Notes:

- **Fetch and import** replace the earlier catalogue events (GLM, USGS or CSV) but keep the
  user's manual events. For both sources, `bbox` defaults to `case.region` padded by 50 km.
- **How sound travels:** blasts, explosions, sonic booms and thunder are air waves, arriving
  at `t_utc + distance / c`. Earthquakes are heard from the P-wave arrival, at
  `t_utc + hypocentral distance / 6 km/s`.
- **One event** with a flash (or a known start time) fixes the clock and the distance to the
  event: a range ring. `ranges` holds it and the notes say so.

How the solve chooses its mode:

- **`known_start`:** used when `start_utc` is a number, or when it is omitted and the case
  has one. An explicit `start_utc: null` ignores the case's start time.
- **`flash_sync`:** used when there are accepted flashes. The recording clock comes from
  matching flash times to event times.
- **`free`:** otherwise. Searches over the clock offset too.

## ENF source (mains hum vs grid frequency records)

```ts
type EnfAnalysis = {
  created_at: number;
  nominal_hz: 50 | 60 | null;    // null: no mains hum found
  confidence: number;            // 0..1: confidence in nominal_hz (when null: that there is no hum)
  harmonics: { n: number; freq_hz: number; snr_db: number; used: boolean }[];   // n = 1..6; snr_db: hum line vs noise per analysis bin
  trace: { t0: number; dt: number; f: (number | null)[] };   // ENF (Hz, at the fundamental) per step; null where unreliable
  quality: number;               // fraction of usable steps
  notes: string[];
  window_s: number;              // analysis window (default 16 s); the reference is smoothed the same way
  requested_nominal: "auto" | 50 | 60;
};

type EnfReference = {
  id: string; name: string;      // builtin ids: "gb-neso", "gb-elexon", "nordic-fingrid", "au-nem-aemo", "au-tas-aemo", "pgf-..."; imports: "er_..."
  grid: string;                  // e.g. "Great Britain", "Nordic", "Continental Europe"
  nominal_hz: 50 | 60;
  source: "builtin" | "import";
  resolution_s: number;          // native resolution (everything is matched at 1 s)
  coverage: { t0_utc: number; t1_utc: number }[] | null;   // null for builtin sources fetched on demand
  licence: string | null; url: string | null;
  available: string | null;      // plain-language availability of a builtin source, e.g. "January 2014 to the end of the last published month"; null for imports
};

type EnfMatch = {
  id: string; created_at: number;
  reference_id: string; t0_utc: number; t1_utc: number;
  best: { start_utc: number; corr: number; z: number } | null;
  top: { start_utc: number; corr: number }[];   // distinct peaks, best first (top[0] is the best; top[1] the runner-up)
  overlay?: { t: number[]; rec: (number | null)[]; ref: (number | null)[]; rec_shift_hz: number };   // detail endpoint only
  notes: string[];               // verdict ("stands out clearly" / "probably real" / "not significant") and caveats
  reference_name: string; method: "diff" | "highpass" | "raw" | null;
  n_offsets: number; overlap_s: number; reference_valid_fraction: number; analysis_created_at: number | null;
};
```

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/api/cases/{id}/enf` | | `{ analysis: EnfAnalysis \| null, matches: EnfMatch[] }` (no overlays) |
| POST | `/api/cases/{id}/enf/extract` | `{ nominal?: "auto" \| 50 \| 60 }` | `Job`; result `{ nominal_hz }`. 400 if the recording is not processed; 409 if one is running |
| POST | `/api/cases/{id}/enf/match` | `{ reference_id, t0_utc, t1_utc }` | `Job`; result `{ match_id }`. 400 before extraction, without hum, for a reference of the other nominal frequency, or for a window over 31 days |
| GET | `/api/cases/{id}/enf/matches/{mid}` | | `EnfMatch` with `overlay` |
| DELETE | `/api/cases/{id}/enf/matches/{mid}` | | 204 |
| GET | `/api/enf/references` | | `EnfReference[]` (builtins first, then imports) |
| GET | `/api/enf/references/{rid}` | | `EnfReference`; 404 if unknown |
| POST | `/api/enf/references/import` | multipart: `file` (CSV `time,frequency`), `name`, `grid`, `nominal_hz` | `EnfReference` |
| DELETE | `/api/enf/references/{rid}` | | 204; imports only (400 for builtins) |
| GET | `/api/enf/grid-map` | | GeoJSON `FeatureCollection` of countries, properties `{ name, iso_a3, mains_hz: 50 \| 60 \| "50/60" \| null }` |

Notes:

- **Match window:** `t0_utc`..`t1_utc` bounds the recording's *start* time; the reference is loaded
  for that window plus the recording's length. At most 31 days. Builtin references download on
  first use (a NESO day takes seconds, a Fingrid month about a minute) and are cached.
- **Significance:** `z` compares the best peak with the correlations at all offsets outside its
  main lobe, on Fisher's scale (atanh r). Chance alone over n offsets gives about
  `sqrt(2 ln(n / lobe))`; the notes state that level and the verdict. `top` peaks are distinct
  (each excludes the previous peaks' main lobes, at least 60 s).
- **Overlay units:** `t` is recording time (s); `rec` and `ref` are in Hz. `ref` is the reference
  smoothed with the analysis window; `rec` is the recording's ENF shifted by `rec_shift_hz` (the
  median difference, i.e. the recorder's constant clock offset) so the two lie on top of each
  other. Long recordings are block-averaged to at most 4000 points.
- **CSV import:** `time` is ISO-8601 (an offset is honoured; none means UTC) or unix seconds;
  comma, semicolon or tab separated; any resolution (averaged or interpolated to 1 s); at most
  120 days per file.

## Alerts source (US emergency-alert data bursts, EAS/SAME)

US radio and TV stations, and NOAA Weather Radio, transmit alerts with a digital header (SAME:
AFSK at 520.83 baud, sent three times) that names the originator, event, affected counties
(FIPS codes), the issue time to the minute and the sending station. Decoded from a recording,
it gives a county-level region and a clock anchor.

```ts
type SameLocation = {
  code: string;                 // PSSCCC as sent, e.g. "139069"
  part: string;                 // P as a word: "all" (0), "northwest" (1) ... "southeast" (9)
  fips: string;                 // SSCCC, e.g. "39069"
  state: string | null;         // USPS abbreviation; null for marine zones and all-US codes
  county: string | null;        // "Henry County"; "Entire state" (CCC 000); "All of the United States" (000000);
                                // marine zone name with its NWS ID, e.g. "... (ANZ530)"; null if unknown
  lat: number | null; lon: number | null;   // county internal point (US Census Gazetteer), land-weighted
                                            // state centroid, or NWS marine-zone centroid
};
type SameHeader = {
  id: string;                   // "sh_..." (new ids on every scan)
  t: number;                    // recording time where the first decoded burst (its preamble) starts
  repeats: number;              // how many of the (normally 3) bursts decoded
  raw: string;                  // e.g. "ZCZC-WXR-TOR-029095+0030-1281917-KEAX/NWS-"
  originator: string; originator_name: string;   // "WXR" → "National Weather Service"
  event: string; event_name: string;             // "TOR" → "Tornado Warning", "RWT" → "Required Weekly Test"
  locations: SameLocation[];
  purge_minutes: number;
  issued_utc: number;           // JJJHHMM; year from the case date, else the file's creation-time tag, else the
                                // latest such day before the scan (recomputed on every GET, so a date change applies)
  station: string;              // e.g. "KEAX/NWS"
  implied_start_utc: number;    // issued_utc - t: assumes the burst aired at the issue minute (usually 0-2 min
                                // earlier than the truth; relays and rebroadcasts can be much later)
  confidence: number;           // 0..1: estimated probability the decoded text is exactly right, lowered for a
                                // lone burst, an unknown event or an unmapped location. Below 0.2: notes only
};
```

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/api/cases/{id}/alerts` | | `{ headers: SameHeader[], eom: { t: number }[], scanned_at: number \| null, notes: string[] }`. Headers sorted by `t`; one `eom` entry per group of up to 3 `NNNN` bursts (time of the first) |
| POST | `/api/cases/{id}/alerts/scan` | `{ crosscheck?: true }` | `Job` (kind `alerts.scan`); result `{ count }` (headers decoded). Replaces the previous scan. Decodes the original upload at 11,025 Hz (the 4 kHz copy is too low for the 1562.5/2083.3 Hz tones), streaming it, so long files are fine. `crosscheck` also looks each header up in FEMA's IPAWS archive (network; only a ±15 min time window is sent). 400 if there is no recording or the original upload is missing; 409 while the recording is processing or a scan is running |

`notes` are plain-language lines:
- which reference gave the issue year;
- the broadcast-delay caveat;
- per header: bursts missing or corrected by combining, codes corrected to the nearest known
  code, the attention signal that followed (EAS 853 + 960 Hz or NOAA Weather Radio 1050 Hz),
  the IPAWS cross-check result, and Canadian stations (location codes not mapped);
- bursts that looked like SAME but could not be decoded;
- EAS attention signals with no decodable header.

## Writing a router (backend developers)

- Get shared services with FastAPI dependencies:
  `store: Store = Depends(get_store)`, `jobs: JobManager = Depends(get_jobs)` from
  `tracksound.deps`.
- **Case files:** use `store.read_json(case_id, "lightning/onsets.json", default)` and
  `store.write_json(...)`. The write is atomic. Wrap read-modify-write sequences in
  `with store.lock(case_id):`.
- **Paths:** `store.audio_path(case_id)` is the standard 4 kHz mono float32 WAV.
  `store.original_path(case_id)` is the upload (for video frames).
  `tracksound.spectrogram.load(store.spectrogram_path(case_id))` is the cached
  dopplertrack `Spectrogram`.
- **Long work:** `jobs.submit(kind, case_id, fn)`, where `fn(ctx)` calls
  `ctx.progress(fraction, message)`. That call raises `JobCancelled` when the user cancels.
  Return a small JSON-able result. Write real output to the case folder before returning.
- **Missing cases:** `store.get_case(case_id)` raises `NotFound`, which becomes a 404.
  A `ValueError` becomes a 400 with its message.
- **Downloaded public data** goes under `store.settings.cache_dir / "<source>"`.
