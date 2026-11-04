# ENF: dating recordings by their mains hum

Electrical Network Frequency (ENF) analysis is an established audio-forensics technique. Anything
recorded near mains-powered equipment, or by a mains-powered recorder, picks up hum at the grid
frequency (nominally 50 or 60 Hz) and its harmonics. The grid frequency wanders by tens of mHz
in a pattern shared by a whole synchronous grid and logged by grid operators. Following the hum's
frequency second by second and correlating it with those logs tells:

- **when** the recording was made, often to the second, which feeds the aircraft search (it needs
  the date, and a known start time narrows it enormously);
- **which grid**: 50 vs 60 Hz alone separates continents, and comparing several grids' logs tells
  e.g. Great Britain from the Nordic grid.

Code: `backend/src/tracksound/sources/enf/`, API: `backend/src/tracksound/api/enf.py`, contract:
docs/API.md ("ENF source").

## How it works here

### 1. Finding the hum (`extract.py`)

For each harmonic h = 1..6 of 50 and of 60 Hz, the 4 kHz case audio is resampled to 1 kHz, the
band around h·f_nom is shifted to 0 Hz (complex demodulation; the oscillator is tiled from one
short period) and decimated to a 20 Hz complex baseband. Short-time spectra use a **16 s Hann
window with a 1 s hop**. The search band |δ| ≤ 0.25 Hz at the fundamental (h·0.25 Hz at harmonic
h; GB operates within ±0.2 Hz) is evaluated by an exact DFT on a 1 mHz grid of δ; the noise
floor of each frame is the median power of the neighbouring bins (0.6 Hz beyond the band to 7 Hz).

**Detection** averages each harmonic's band power over the recording and compares it with the
noise floor. Noise alone gives a ratio of 1 with a spread that shrinks with recording length, so
a hum below the noise in every single frame is still found; each harmonic gets a z-score. A grid
is called when the sum of positive z over its discriminating harmonics reaches 6 and one harmonic
reaches 4.5 (300 Hz, shared by 6×50 and 5×60, is ignored for the decision). `confidence` combines
the margin over the other family with the strength of the evidence.

**Tracking** (spectrum combining, Hajj-Ahmad, Garg & Wu 2013): the per-harmonic spectra in noise
units are weighted by s²/(1+s²) (s² = the harmonic's line SNR; the log-likelihood-ratio weight)
and summed. The most likely smooth path through this combined spectrogram is found by dynamic
programming (Viterbi; steps ≤ 6 mHz/s, Gaussian prior σ = 2 mHz/s), refined to sub-mHz by
quadratic interpolation of the log spectrum. Seconds whose evidence along the path (averaged over
8 s) is under 2 dB above noise, or on the band edge, are null; valid runs shorter than 10 s are
dropped. Long files are read in 600 s blocks, so memory does not grow with duration.

Accuracy on synthetic hum following real GB data: 0.13 mHz RMS against the Hann-smoothed truth at
high SNR, about 2 mHz at moderate SNR. Compared with the first version (8 s windows, per-frame
maxima plus outlier rejection), DP tracking raised the usable fraction at a weak hum from about
0.4 to 0.98. The recorder's clock error (±50 ppm) scales every frequency by the same factor
(±2.5 mHz at 50 Hz): a constant offset that correlation ignores; it also stretches time by
0.18 s per hour, negligible at 1 s resolution.

### 2. Matching (`match.py`)

Both series are on 1 s grids with NaN gaps. The reference is smoothed with the same 16 s Hann
window (the kernel that best reproduced extracted traces: 0.13 mHz vs 0.76 for a box), then both
are preprocessed by a method chosen from the trace quality **before** matching:

| Trace | Method | Why |
|---|---|---|
| ≥ 75 % usable, or ≥ 45 % and ≥ 600 usable s | `diff`: first differences, winsorised at 5 robust σ | ENF wanders like a random walk; differences are nearly white, so they give the sharpest, most unique peak |
| ≥ 30 % usable | `highpass`: minus a 300 s moving average | more robust on short patchy traces |
| otherwise | `raw`: mean removed | still finds the offset from slow drifts when the hum is weak, but many offsets look alike |

On synthetic 30 min recordings against 3 days of real GB data, `diff` gave true-match z ≈ 10–11
versus 8–9 for a 60 s high-pass and 7–8 for 120 s; with a weak, patchy trace it still matched
where short high-passes failed. Winsorising matters: in one case five tracking glitches carried
97 % of a wrong match's correlation.

For every candidate start in [t0_utc, t1_utc] the Pearson correlation is computed over the jointly
valid samples (at least 120 s and half the recording's usable seconds), all offsets at once with
FFT cross-correlations of the masked sums (n, Σx, Σy, Σx², Σy², Σxy): an hour against a month
takes about a second. The best peak is refined to sub-second by a parabola. **z** compares the best
peak with all offsets outside its main lobe (where r falls below half the peak, at least 60 s), on
Fisher's scale (atanh r, whose spread does not depend on r, so r = 0.995 against 0.65 counts as the
large gap it is). Chance alone over n offsets gives about sqrt(2 ln(n/lobe)); the verdict is "stands
out clearly" at ≥ chance + 3 and ≥ 2 above the runner-up, "probably real" at ≥ chance + 1.5,
otherwise "not significant". `top` lists distinct peaks.

Evaluation (synthetic hum following real GB data from NESO, 3 days = 259 k offsets; room noise
plus rumble; the noise-only "null" uses a hum following ENF that is not in the reference):

| Recording | Hum | Usable | Result |
|---|---|---|---|
| 30 min | moderate (≈ +9 dB at 100 Hz per bin) | 98 % | 4/4 within 1 s, z 11–12, "clear" |
| 30 min | weak | 55 % | 3/4 within 1 s ("clear"/"probable"); the 4th fixed by winsorising |
| 10 min | moderate | 98 % | 4/4 within 1.3 s; 2 clear, 1 probable, 1 not significant |
| 10–30 min | very weak | 15–25 % | raw method; offset sometimes right, never certified |
| null, 10–30 min | | | z ≤ 4.4, all "not significant" |
| demo, 20 min | real GB slice, 6 h window | 100 % | 0.3 s error, "clear" |

## Reference data (`references/`)

Every source is served as a 1 s series (sub-second data averaged into bins centred on whole UTC
seconds, coarser data interpolated across gaps of up to 2.5 samples), cached per UTC day under
`<data>/cache/enf/<id>/`. Runs of 30 s or more of identical values are blanked everywhere: NESO's
September 2026 file has a 3.1 h stretch where the meter froze, while natural plateaus last ≤ 13 s.

| id | Grid | Source (verified from this machine, Nov 2026) | Resolution | Coverage | Licence |
|---|---|---|---|---|---|
| `gb-neso` | Great Britain | NESO "System Frequency", CKAN `https://api.neso.energy/api/3/action/package_show?id=system-frequency-data`; days sliced by SQL (`datastore_search_sql`), whole months downloaded where needed | 1 s | Jan 2014 → last published month (≈ 1 month late) | NESO Open Data Licence |
| `gb-elexon` | Great Britain | Elexon BMRS `https://data.elexon.co.uk/bmrs/api/v1/datasets/FREQ` (≤ 1 day per request, no key) | 15 s | ~2021 → minutes ago | Elexon BMRS data licence (attribution) |
| `nordic-fingrid` | Nordic (FI, SE, NO, east DK) | Fingrid dataset 339, `https://data.fingrid.fi/files/339/<YYYY>/<YYYY-MM>.7z` (no key for files; the REST API would need one, unused) | 0.1 s | Jun 2023 → last month (≈ 2 weeks late) | CC BY 4.0, Fingrid |
| `au-nem-aemo`, `au-tas-aemo` | Australia mainland NEM / Tasmania | AEMO NEMWEB `PUBLIC_FPP_RUN` reports, `REGION_FREQ_MEASURE` (NSW1 / TAS1): `https://nemweb.com.au/Reports/Archive/FPPRUN/`, last 2 days from `.../Current/FPPRUN/` | 4 s | rolling ~12 months (FPP began Jun 2025) | AEMO copyright permissions (attribution) |
| `pgf-*` (16) | US Western & Texas, Continental Europe (FR, HR, IT, PL, PT), GB, Nordic, Baltic, Russia, South Africa, Iceland, Faroe, Gran Canaria | Power Grid Frequency database (Jumar et al.), OSF project by5hu, `https://osf.io/<id>/download` | 1 s | fixed campaigns of days–weeks, 2017–2019 | CC BY 4.0 |
| `er_*` | any | CSV import (`time,frequency`), stored under `<data>/enf/references/` | any | as imported | user's |

Things verified the hard way:

- **NESO time stamps are inconsistent.** Cross-correlating every month against Elexon (UTC): 2022–
  2024 and 2026 files are UTC, but April–October 2025 files are UTC+1 for the whole month (the
  offset in force on the 1st). 2014–2018 summer files start at `01:00:00` (UTC+1); 2016-01,
  2018-01 and the 2019 files carry explicit offsets (`+0100`). The code calibrates naive months
  against Elexon (from mid-2021) or infers +1 h from a `01:00` first label; after correction
  2025-07 matches Elexon at lag 0 (r = 0.998). The 9 Aug 2019 blackout dip appears at 15:52:38 UTC
  (event 15:52:33 UTC) and NESO agrees with the GPS-synchronised PGF London recording to within a
  second, so labels mark the second to ±0.5 s.
- **NESO's datastore mangled some months** (March 2019 loaded with day and month swapped: min
  `2019-01-03`, max `2019-12-03`). A month whose datastore min/max lie outside the month is read
  from the original file instead.
- **Fingrid times are Finnish local time** (UTC+2/+3); the repeated hour in October is resolved by
  order. The archive is 7z; the project has no 7z dependency, so `sevenzip.py` reads the subset
  Fingrid uses (LZMA/LZMA2/BZip2/Deflate/Copy, solid, encoded header) with the standard library
  (a month: 63 MB → 877 MB in 8 s; a full month is ingested in about 30 s).
- **AEMO times are NEM time (UTC+10, no DST)**; daily archives cover NEM calendar days.

Checked and not usable: Fingrid's REST API (needs a key; the files do not), TransnetBW's
1 s Continental Europe files (the old `transnetbw.de/files/bis/netzfrequenz/` URLs now 404), the
Zenodo copy of them (record 5105820; TransnetBW data is not openly licensed), RTE and Swissgrid
(no open second-level history found), AEMO `FPPDAILY` (unit MW only, 85 MB/day), and no open,
ongoing North American source (FNET/GridEye data is by request). The 1.3 GB/218 MB PGF archives
and its 324 MB Mallorca and 135 MB London sets are left out as too large.

## Grid map

`tracksound/data/countries_mains.geojson` (176 KB): Natural Earth 1:110m Admin-0 countries (public
domain), coordinates rounded to 0.01°, `iso_a3` from `ISO_A3_EH` (Natural Earth's `ISO_A3` is
`-99` for France and Norway). `mains_hz` from Wikipedia "Mains electricity by country"
(revision 1380969207, CC BY-SA 4.0, citing IEC World Plugs / IEC TR 60083): 147 countries 50 Hz,
28 at 60 Hz, Japan "50/60" (east 50, west 60), Antarctica null; W. Sahara, N. Cyprus, Somaliland
and S. Sudan set to 50 Hz by hand.

## Demo

`demo.make_demo_case(store)` builds "Demo: mains hum (ENF)": 20 min at 8 kHz of room noise, rumble
and soft transients with a faint hum (mostly 100/150 Hz) following real GB data, recorder clock
12 ppm fast. Six hours of NESO 1 s data (2026-09-14 09:00–15:00 UTC, 20 KB as int16 mHz) are
bundled in `tracksound/data/enf_demo_gb_2026-09-14.npz` and registered as an imported reference,
so the demo matches offline; with network, `gb-neso` finds the same time. The true start is in the
case notes; `start_utc` and `date` stay null.

## Limitations

- **No hum, no result.** Outdoor and battery-powered recordings far from mains equipment often
  have none; phones' noise suppression and low-cut filters often remove it (the 2nd and 3rd
  harmonics survive more often than the fundamental). Inverters, UPSs and generators hum at a
  fixed, quartz-controlled frequency (the analysis notes a suspiciously steady hum).
- **Short recordings are ambiguous.** Under ~10 minutes, a day-long search often gives "probably"
  or "not significant"; narrow the window (file metadata, other sources) or use longer audio.
- **Coverage gaps:** no open, ongoing Continental Europe or North American 1 s source; NESO and
  Fingrid publish monthly with a delay (use `gb-elexon` for recent GB days); AEMO keeps ~12 months.
- A wrongly resampled file (e.g. 44.1 kHz audio labelled 48 kHz) shifts the hum by 9 % and hides it.
- Matching assumes one continuous take; edited or spliced audio shows as several partial matches
  (the overlay helps spot it).

## Literature

- C. Grigoras, "Digital audio recording analysis: the Electric Network Frequency (ENF) Criterion",
  *Int. J. Speech, Language and the Law* 12(1), 2005. https://doi.org/10.1558/sll.2005.12.1.63
- C. Grigoras, "Applications of ENF criterion in forensic audio, video, computer and
  telecommunication analysis", *Forensic Science International* 167, 2007.
  https://doi.org/10.1016/j.forsciint.2006.06.033
- M. Huijbregtse, Z. Geradts, "Using the ENF criterion for determining the time of recording of
  short digital audio recordings", *Computational Forensics (IWCF 2009)*, LNCS 5718.
- O. Ojowu, J. Karlsson, J. Li, Y. Liu, "ENF extraction from digital recordings using adaptive
  techniques and frequency tracking", *IEEE Trans. Inf. Forensics Security* 7(4), 2012.
  https://doi.org/10.1109/TIFS.2012.2197391
- A. Hajj-Ahmad, R. Garg, M. Wu, "Spectrum combining for ENF signal estimation", *IEEE Signal
  Processing Letters* 20(9), 2013. https://doi.org/10.1109/LSP.2013.2272523
- R. Jumar, H. Maass, B. Schäfer, L. Rydin Gorjão, V. Hagenmeyer, "Database of power grid
  frequency measurements", arXiv:2006.01771, 2020. https://arxiv.org/abs/2006.01771
- J. Kruse, B. Schäfer, D. Witthaut, "Predictability of Power Grid Frequency", *IEEE Access* 8, 2020
  (source of the Zenodo dataset).
