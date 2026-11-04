// Types of the TrackSound local API. Mirrors docs/API.md (the contract with the backend).

export type BBox = { lat_min: number; lat_max: number; lon_min: number; lon_max: number };

export type StartSource = "user" | "aircraft" | "lightning" | "enf";

export type Case = {
  id: string;
  name: string;
  notes: string;
  created_at: number;
  updated_at: number;
  date: string | null;
  timezone: string | null;
  region: BBox | null;
  start_utc: number | null;
  start_utc_source: StartSource | null;
  temperature_c: number | null;
  recording: Recording | null;
};

export type CasePatch = Partial<
  Pick<Case, "name" | "notes" | "date" | "timezone" | "region" | "start_utc" | "start_utc_source" | "temperature_c">
>;

export type Recording = {
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

export type SpectrogramInfo = {
  n_frames: number;
  n_bins: number;
  t0: number;
  dt: number;
  f0: number;
  df: number;
  tile_frames: number;
  levels: number;
  db_min: number;
  db_max: number;
};

export type JobStatus = "queued" | "running" | "done" | "error" | "cancelled";

export type Job = {
  id: string;
  case_id: string | null;
  kind: string;
  status: JobStatus;
  progress: number;
  message: string;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  result: any | null;
  error: string | null;
};

export type Health = { ok: boolean; version: string; data_dir: string };

// ---- aircraft ---------------------------------------------------------------------------

export type EventStatus = "candidate" | "accepted" | "rejected";

export type AircraftEvent = {
  id: string;
  origin: "detected" | "manual";
  status: EventStatus;
  harmonics: number;
  band: [number, number];
  t_cpa: number;
  t_cpa_emit: number;
  f0: number;
  v: number;
  d: number;
  t_start: number;
  t_end: number;
  score: number;
  /** heuristic from score: noise fits score ~1, real flyovers 3+ */
  quality?: EventQuality;
  f_pre: number;
  f_post: number;
};

export type EventQuality = "strong" | "possible" | "weak";

export type FlightSummary = {
  idx: number;
  icao: string;
  reg: string | null;
  type: string | null;
  desc: string | null;
  engine: string | null;
  t_start_utc: number;
  t_end_utc: number;
  n_points: number;
};

export type FlightSet = {
  status: "none" | "ready";
  date: string | null;
  bbox: BBox | null;
  fetched_at: number | null;
  n_flights: number;
  flights: FlightSummary[];
  /** data licence notice, e.g. "adsb.lol (ODbL)" (not in API.md; sent by the server) */
  attribution?: string | null;
};

export type FlightFeatureProps = {
  idx: number;
  icao: string;
  reg: string | null;
  type: string | null;
  desc: string | null;
  t_start_utc: number;
  t_end_utc: number;
  times: number[];
};

export type FlightsGeoJSON = {
  type: "FeatureCollection";
  features: {
    type: "Feature";
    geometry: { type: "LineString"; coordinates: [number, number, number?][] };
    properties: FlightFeatureProps;
  }[];
};

export type SearchMode = "blind" | "point" | "single";

export type AircraftSearchRequest = {
  mode: SearchMode;
  events: string[];
  spacing_m?: number;
  tol_s?: number;
  point?: { lat: number; lon: number };
  event_utc?: number;
  use_dem?: boolean;
  silence?: boolean;
};

export type SearchEstimate = { n_points: number; n_segments: number; est_seconds: number };

export type CandidateFlight = {
  event_id: string;
  flight_idx: number;
  icao: string;
  reg: string | null;
  type: string | null;
  desc: string | null;
  t_heard_utc: number;
  d_m: number;
  v_mps: number;
  score: number;
};

export type AircraftCandidate = {
  rank: number;
  lat: number;
  lon: number;
  elev_m: number;
  score: number;
  start_utc: number;
  shift_s: number;
  flights: CandidateFlight[];
};

export type HeatPoint = { lat: number; lon: number; score: number };

export type AircraftSearch = {
  id: string;
  created_at: number;
  mode: SearchMode | "refine";
  parent_id: string | null;
  request: Record<string, unknown>;
  status: "done" | "error";
  error: string | null;
  bbox: BBox | null;
  stats: { n_points: number; n_segments: number; n_cpa: number; n_candidates: number; elapsed_s: number };
  best: AircraftCandidate | null;
  margin: number | null;
  candidates: AircraftCandidate[];
  heat?: HeatPoint[];
  /** plain-language caveats (not in API.md; sent by the server) */
  notes?: string[];
};

export type CandidateCurves = {
  events: { event_id: string; flight_idx: number; icao: string; harmonics: number; t: number[]; f: number[] }[];
};

// ---- lightning --------------------------------------------------------------------------

export type ThunderOnset = {
  id: string;
  t: number;
  sigma_s: number;
  strength: number;
  duration_s: number | null;
  origin: "detected" | "manual";
  status: EventStatus;
};

export type Flash = {
  id: string;
  t: number;
  strength: number;
  origin: "video" | "manual";
  status: EventStatus;
};

export type Strike = {
  id: string;
  t_utc: number;
  lat: number;
  lon: number;
  sigma_m: number;
  energy: number | null;
  kind: string | null;
  source: string;
};

export type StrikeSet = {
  source: string;
  t0_utc: number;
  t1_utc: number;
  bbox: BBox | null;
  count: number;
  fetched_at: number;
};

export type LightningBest = {
  lat: number;
  lon: number;
  start_utc: number;
  score: number;
  n_matched: number;
  rms_s: number;
  uncertainty_m: number;
};

export type LightningSolveSummary = { id: string; created_at: number; mode: string; best: LightningBest | null };

export type LightningSolve = {
  id: string;
  created_at: number;
  request: Record<string, unknown>;
  mode: "known_start" | "flash_sync" | "free";
  best: LightningBest | null;
  candidates: { lat: number; lon: number; start_utc: number; score: number; n_matched: number }[];
  heat: HeatPoint[];
  matches: { onset_id: string; strike_id: string; residual_s: number; distance_m: number }[];
  flash_matches: { flash_id: string; strike_id: string; residual_s: number }[];
  ranges: { strike_id: string; lat: number; lon: number; distance_m: number }[];
  notes: string[];
};

export type LightningState = {
  onsets: ThunderOnset[];
  flashes: Flash[];
  strikes: StrikeSet | null;
  solves: LightningSolveSummary[];
};

export type ThunderEnvelope = {
  t: number[];
  bands: { name: string; lo_hz: number; hi_hz: number; db: number[] }[];
  onset_strength: number[];
};

// ---- ENF --------------------------------------------------------------------------------

export type EnfAnalysis = {
  created_at: number;
  nominal_hz: 50 | 60 | null;
  confidence: number;
  harmonics: { n: number; freq_hz: number; snr_db: number; used: boolean }[];
  trace: { t0: number; dt: number; f: (number | null)[] };
  quality: number;
  notes: string[];
};

export type EnfReference = {
  id: string;
  name: string;
  grid: string;
  nominal_hz: 50 | 60;
  source: "builtin" | "import";
  resolution_s: number;
  coverage: { t0_utc: number; t1_utc: number }[] | null;
  licence: string | null;
  url: string | null;
};

export type EnfMatch = {
  id: string;
  created_at: number;
  reference_id: string;
  t0_utc: number;
  t1_utc: number;
  best: { start_utc: number; corr: number; z: number } | null;
  top: { start_utc: number; corr: number }[];
  overlay?: { t: number[]; rec: (number | null)[]; ref: (number | null)[] };
  notes: string[];
};

export type EnfState = { analysis: EnfAnalysis | null; matches: EnfMatch[] };

export type GridMapGeoJSON = {
  type: "FeatureCollection";
  features: {
    type: "Feature";
    geometry: unknown;
    properties: { name: string; iso_a3: string; mains_hz: 50 | 60 | "50/60" | null };
  }[];
};

// ---- weather (speed of sound) -----------------------------------------------------------

export type Weather = {
  source: string;
  lat: number;
  lon: number;
  date: string;
  hourly: { t_utc: number; temperature_c: number | null }[];
  suggested_c: number | null;
  basis: string;
  sound_speed_mps: number | null;
  attribution: string | null;
};
