# Audio geolocation techniques: a survey for TrackSound

Status: survey, 2026-11-04. Input for choosing the sources that come after prop
aircraft (v1), lightning (M5) and ENF (in progress). Read it alongside
[PLAN.md section 7](../PLAN.md#7-designed-for-more-sources) (the plugin model)
and [lightning.md](lightning.md).

## Contents

1. [Introduction](#1-introduction)
2. [Summary table](#2-summary-table)
3. [Moving sources with public position records](#3-moving-sources-with-public-position-records)
4. [Impulsive events at known times and places](#4-impulsive-events-at-known-times-and-places)
5. [Scheduled and broadcast sounds](#5-scheduled-and-broadcast-sounds)
6. [Mains electricity](#6-mains-electricity)
7. [Wildlife](#7-wildlife)
8. [Signals with little or no location value](#8-signals-with-little-or-no-location-value)
9. [Cross-cutting observations](#9-cross-cutting-observations)
10. [Recommended roadmap](#10-recommended-roadmap)
11. [What could not be verified](#11-what-could-not-be-verified)
12. [Sources](#12-sources)

---

## 1. Introduction

This survey covers ways to locate a recording, or to narrow down where and when
it was made, from its sound and, where relevant, from light events in its video.
It draws on forensic practice, open-source investigation (OSINT) practice and
academic work, and judges each technique against TrackSound's plugin model:

- **Detector**: finds observations in the recording.
- **Catalogue**: a public record of events with a time and place.
- **ForwardModel**: what a listener at point *x* would observe, and when.
- **Scorer**: how well the observations fit a candidate (*x*, clock offset).

Three kinds of evidence come up again and again that the current model does not
name. The core should support all three:

- **Clock anchors.** Evidence about *when*, with little about *where*: a siren
  test, an emergency-alert data burst, ENF. These matter because a known clock
  lets the aircraft search use its cheap single-event mode.
- **Priors.** Evidence about *where* (or which season) with no clock: mains
  frequency, bird species, language. These are score maps over *x* only.
- **User-supplied catalogue entries.** One event the user knows or wants to test
  (an explosion's time and place, a claimed shooter position, a radio station),
  instead of a public database.

Labels used below:

- **Maturity.** *Established*: routine forensic or OSINT practice with published
  casework. *Research*: peer-reviewed methods that are not routine.
  *Speculative*: plausible physics, but no published use found.
- **Effort** for us, assuming the M5 plugin core exists. *S*: up to about two
  person-weeks. *M*: about one to two person-months. *L*: more than that, or it
  needs new data agreements or infrastructure.
- **Recommendation.** *Implement next*, *Later* (sometimes "spike first"), or
  *Not worth it* (for now).

How claims were checked: papers through Crossref and their DOIs, regulations
through eCFR, plus official pages. Data portals were tested with live HTTP
requests on 2026-11-04. Figures marked *(estimate)* are my own rough numbers,
not published results. Section 11 lists what I could not verify. Prop aircraft,
lightning and ENF are covered elsewhere and appear here only for context. ENF is
covered at the literature level only, as agreed.

Video is covered only where it adds a timed light event to the audio: flashes
(4.1, and lightning) and mains flicker (6.1). Purely visual methods, such as sun
and shadow chronolocation, satellite passes and landmark matching, are out of
scope.

## 2. Summary table

| Technique (section) | Yields | Typical accuracy | Reference data and availability | Maturity | Effort | Recommendation |
|---|---|---|---|---|---|---|
| Prop aircraft Doppler vs ADS-B (context) | Point and absolute time | 43 m and 592 m on dopplertrack's two reference clips; independent tests on arbitrary clips mostly find no fix | adsb.lol daily archives (ODbL, free) | Research; several open tools | — | In progress (v1) |
| Helicopter rotor tones vs ADS-B (3.2) | Point and time | Expected to be like props; not validated | Same ADS-B data (helicopter type classes) | Research | S | **Implement next** |
| Jet ground-reflection ("Lloyd's mirror") delay vs ADS-B (3.3) | Point and time | 0.2–5 km in the few real successes one open project reports; often no fix | Same ADS-B data, keeping jets | Research | M | **Implement next** |
| Jet fan tones and buzz-saw noise vs ADS-B (3.4) | Point and time | Unknown; thrust changes break the constant-tone model | Same | Speculative | M | Not worth it |
| Ship horns and engine tones vs AIS (3.5) | Point near water, and time | Unknown; hundreds of m to km *(estimate)* | US MarineCadastre and Danish DMA daily AIS (free, verified); others vary | Speculative in air | M | Later (spike first) |
| Foghorns at fixed stations (3.6) | "Near station X" | About the audible range, a few km | USCG Light List, NGA List of Lights (not reachable by script); many US horns now sound only on request | Speculative | S | Not worth it |
| Train horns and pass-bys vs train-movement records (3.7) | Point on a rail line, and time | 1–2 km along the track with minute-level data *(estimate)* | NL (CC BY 4.0), CH, UK, NYC subway, Amtrak; almost no freight data | Speculative | M | Later |
| Level-crossing warning sounds, horn rules (3.8) | Country cue; "near a crossing" | Coarse | OSM (about 1.0 M crossings), FRA crossing inventory | Speculative | S | Not worth it alone |
| Lightning and thunder (context) | Range rings and absolute time | km-level with satellite data | GLM, MTG-LI and others | Research | — | Planned (M5) |
| Explosions and fireworks: flash-to-bang (4.1) | Distance to a known event; the clock | About 11 m per video frame at 30 fps, plus the event's location error | User-supplied event; news; seismic records | Established | S | **Implement next** (with lightning) |
| Quarry and mining blasts, earthquakes vs seismic catalogues (4.2) | Range ring and absolute time | Limited by catalogue location error (often about 1 km or more for small events) | USGS ComCat and EMSC (free APIs, verified) | Research | S–M | **Implement next** (with lightning) |
| Sonic booms from launches, landings, re-entries and fireballs (4.3) | Clock and coarse region | Seconds; tens of km | Launch Library 2, GCAT, NASA CNEOS (free, verified); trajectories rarely public | Research | M | Later |
| Gunshots: shock wave vs muzzle blast (4.4) | Distance from the shooter to the microphone | About ±5–10 % if the ammunition is known | None (needs a shooter hypothesis) | Established | S–M | Later (analyst tool) |
| Several recordings: synchronisation and TDOA (4.5) | Source position, or camera positions | Metres to tens of metres with good sync *(estimate)* | The other recordings | Established / research | L | Later |
| Civil-defence siren tests and tone patterns (5.1) | Country and local time | About 1 minute; country | Official schedules (8 countries checked); OSM has about 26,000 sirens | Speculative (simple) | S–M | Later (cheap clock anchor) |
| Church bells: tower fingerprint and clock strikes (5.2) | Tower identity (a point); local hour | Exact if the match is unique (untested) | Dove's Guide: 7,262 rings with per-bell frequencies (CC BY-SA 4.0, verified) | Speculative | M | Later (spike first) |
| Call to prayer timing (5.3) | Longitude band if UTC is known; local time if the place is known | About ±1° of longitude for ±5 min *(estimate)* | Prayer-time calculators (Aladhan API, verified); OSM mosques | Speculative | S–M | Later |
| Broadcast audio fingerprinting, time signals (5.4) | Clock to seconds; station coverage area | Seconds, plus the platform's delay | No free broadcast archive; commercial monitoring; user-supplied references | Established technology; data gap | M–L | Later (user-supplied reference only) |
| Emergency-alert data bursts (EAS/SAME) and alert chimes (5.5) | County list, issue time to the minute, station ID | Exact once decoded | 47 CFR 11.31; OpenFEMA IPAWS and IEM archives; JMA EEW history (verified) | Established technology | S | Later (cheap) |
| Language, dialect, spoken place names (5.6) | Country or region prior | Coarse | Whisper, VoxLingua107 | Established ML; weak for location | S | Not core |
| ENF timestamp and grid identification (6.1) | Timestamp; grid; weak position within a grid | Seconds over minutes of signal; grid level | Grid frequency logs (other agent) | Established | — | In progress (other agent) |
| Mains nominal frequency, 50 vs 60 Hz (6.2) | Two-way split of the world | Binary | Public country lists | Established | S | Part of the ENF work |
| Birds and other wildlife vs range maps (7) | Region and season prior | Median error about 1,000 km for a single clip (2025 benchmark) | BirdNET (MIT code, non-commercial models), eBird Status & Trends (access key, terms), GBIF | Research | M | Later (prior and plausibility check) |
| Seismic and infrasound coupling (8.1) | Felt earthquakes only | — | ComCat | — | — | Covered by 4.2; otherwise not worth it |
| Wind turbines (8.2) | "Near turbines" | 1–2 km *(estimate)* | USWTDB (US, verified), OSM | Speculative | M | Not worth it |
| Traffic, acoustic scene classification, city identification (8.3) | Environment type; weak city guess | Weak | DCASE, MediaEval research sets | Research (weak results) | M | Not worth it |
| Reverberation and echoes (8.4) | Room or nearby-wall geometry | No location by itself | — | Research | L | Not worth it |
| Weather sounds vs radar (8.5) | Coarse consistency check | Coarse | NEXRAD Level II on AWS (free, verified) | Speculative | M–L | Not worth it now |

---

## 3. Moving sources with public position records

These work like the v1 aircraft source. A moving source with a published track
produces a sound whose timing, Doppler shift or interference pattern depends on
where the listener was.

### 3.1 Prior art for the aircraft source

**Academic work.** Estimating flight parameters from a ground microphone is an
old defence-research problem, mostly from Australia's DSTO (Ferguson, Lo and
colleagues). They solve the inverse of our problem: the microphone position is
known and the aircraft's speed, altitude and closest-approach time are unknown.
The forward model is the same one TrackSound uses.

- Ferguson (1992), JASA 92(3) 1403–1407: a single microphone just above the
  ground. The Doppler shift of the propeller blade rate gives the altitude and
  speed of a prop aircraft. This is the narrow-band model dopplertrack uses.
- Ferguson & Quinn (1994), JASA 96(2) 821–827: short-time Fourier and
  Wigner–Ville analysis for acoustic localisation of aircraft.
- Reid, Zoubir & Boashash (1996), ICASSP: applies the bootstrap to passive
  acoustic aircraft parameter estimation, which is relevant to our calibration
  work.
- Ferguson & Lo (2000), JASA 108(4) 1763–1771: narrow-band and broadband methods
  on a turboprop and two helicopter types, recorded with a planar array.
- Lo & Ferguson (2000), IEEE TAES 36(1) 163–175: broadband (no tone needed)
  target motion estimation.
- Lo, Perry & Ferguson (2002), IEEE TAES 38(1) 137–151: flight parameters of a
  *jet* from the ground-reflection interference pattern ("acoustical Lloyd's
  mirror") in a single microphone's spectrogram. See 3.3.
- Lo, Ferguson, Gao & Maguer (2003), IEEE TAES 39(1) 259–268: the same idea using
  multipath delays.
- Sadasivan, Gurubasavaraj & Sekar (2001), Defence Science Journal 51(3)
  279–284: altitude and speed of a UAV from spectrogram Doppler contours.

**Operational practice.** Airport noise monitoring systems (for example
Envirosuite's ANOMS and the public WebTrak viewer) already match noise events at
fixed microphones to radar or ADS-B tracks. They flag an event when the level
stays above a threshold for long enough, then look for aircraft near the monitor
at that time. Operators note occasional wrong matches. It is the same
association problem, with the microphone position known.

**Open-source tools.** Since colsto's video, several independent
reimplementations have appeared. These are the projects' own claims, read from
their READMEs on 2026-11-04.

| Project | What it adds | Reported results |
|---|---|---|
| [mjshiggins/dopplertrack](https://github.com/mjshiggins/dopplertrack) (MIT) | Our planned dependency | 43 m at Oyster Bay; 592 m on the eagle cam with 5 flyovers (see PLAN.md) |
| [mojomast/hermes-aircraft-doppler-geolocation](https://github.com/mojomast/hermes-aircraft-doppler-geolocation) | Tonal pipeline plus an experimental broadband jet mode (3.3); a held-out evaluation | Tonal: 1 of 14 held-out clips within 3 km, 6 of 14 with no events at all, median 12.1 km for the rest. Broadband mode: 2 of 18 within 3 km |
| [teamchong/doppler-geo](https://github.com/teamchong/doppler-geo) (MIT) | All signal processing in the browser; includes helicopters | About 40 m on a synthetic two-pass example |
| [AlanBacker/audio-flyover-geolocation](https://github.com/AlanBacker/audio-flyover-geolocation) (MIT) | Agent skill; OpenSky or ADS-B Exchange as alternative data | Synthetic blind tests only: 24–477 m with two events (six seeds); one event with a ±30 s clock gives a 2.1–4.8 km strip |
| [amintt2/flyover-locator](https://github.com/amintt2/flyover-locator) | French-language implementation | No benchmark |
| [1337isnot1337/DOPPLERGEIST](https://github.com/1337isnot1337/DOPPLERGEIST) | Local web app, job handling | 476 m from an inferred reference on one clip; 0 of 5 public field recordings within 1 km |

**What this tells us.** The physics works on clean, close prop passes. On
arbitrary real clips the bottleneck is detection. Livestreams are high-passed at
about 80–120 Hz and compressed, and close jet passes give broadband noise without
tonal lines. The mojomast project also found its 95 % regions badly
overconfident until it widened them by an extra calibration factor. All of this
supports PLAN.md's plans for runner-ups, margins and calibration, and suggests
TrackSound should publish its failure rate as well as its successes.

### 3.2 Helicopters

- **How it works.** Main rotors produce tones at the blade-passing frequency
  (blades × rotor rpm / 60) and its harmonics. Tail rotors produce a higher
  family. Rotor speed is held nearly constant in flight, like a constant-speed
  propeller, so the same Doppler model applies.
- **Typical frequencies.** NASA measured a main-rotor blade-passing frequency of
  39.75 Hz on an MD 530F (5 blades). A UH-1 patent gives about 11 Hz for the main
  rotor and about 56 Hz for the tail rotor's first harmonic. Blade-vortex
  interaction adds impulsive "slap" (Brentner & Farassat 2003). The main-rotor
  fundamental is often below a phone's low-frequency cut-off, so detection should
  rely on harmonics and the tail rotor.
- **Yields.** Point and time, as for props.
- **Accuracy.** No published validation for this use. Hovering or manoeuvring
  helicopters give little Doppler and break the pair search.
- **Reference data.** The same ADS-B archive. Helicopters have their own type
  classes in ICAO Doc 8643, so they are easy to keep or select. Low-flying
  helicopters may have patchier receiver coverage than fixed-wing traffic
  (not checked).
- **Maturity.** Research: Ferguson & Lo (2000) included two helicopter types.
- **Plugin fit.** Detector: dopplertrack's detector with a lower band (about
  10–150 Hz) and harmonic grouping for the two rotor families. Catalogue: the
  same flight store, keeping helicopter types. ForwardModel and Scorer:
  unchanged.
- **Effort.** S. Check first whether dopplertrack's jet filter already keeps
  helicopters.
- **Recommendation.** **Implement next.** It is cheap and reuses everything.

### 3.3 Jets: ground-reflection ("Lloyd's mirror") delay

- **How it works.** A microphone at height *h* hears the direct sound plus a copy
  reflected off the ground, delayed by about τ(t) ≈ 2·*h*·sin(elevation)/c. In
  broadband jet noise this creates evenly spaced notches (spacing 1/τ) in the
  spectrum, and a peak at τ in the cepstrum. As the aircraft passes, τ rises to a
  maximum near closest approach and falls again. The shape of that curve depends
  on the closest-approach distance and speed, much as the Doppler curve does. No
  tone is needed.
- **Prior art.** Lo, Perry & Ferguson (2002) and Lo et al. (2003). The mojomast
  plugin implemented it as an opt-in mode in November 2026. The microphone height
  is fitted as an unknown, and the predicted sin(elevation) from each ADS-B track
  is matched against the measured τ curve.
- **Yields.** Point and time, for the jets that dominate traffic near airports and
  cities, and that ADS-B covers best.
- **Accuracy.** Early and mixed. In mojomast's tests, two clips under the
  Manchester approach improved to 0.19 km and 0.33 km. On 18 held-out clips the
  broadband mode gave an answer for 11, but only 2 were within 3 km, the median
  was 13.2 km, and two clips got worse than with the tonal method alone. It
  needs a clear fringe pattern, which means a close pass and a reflecting ground
  near the microphone.
- **Reference data.** The same ADS-B archive, but jets must be kept. That greatly
  increases the flight store (they are filtered out today) and search cost.
  Restricting to low altitudes (approach and departure) keeps this manageable.
- **Maturity.** Research.
- **Plugin fit.** Detector: cepstral peak tracking (for example with Viterbi) to
  get τ(t). Catalogue: ADS-B including jets below a ceiling. ForwardModel: τ(t)
  from the trajectory at emission time, with *h* as a nuisance parameter. Scorer:
  curve likelihood, combined with the Doppler score when a tone is also present.
- **Effort.** M.
- **Recommendation.** **Implement next**, after helicopters. It addresses the
  main failure mode seen in real clips (no tonal events near airports). Validate
  it on our own ground-truth recordings before trusting it.

### 3.4 Jets: fan tones and buzz-saw noise

- **How it works.** Turbofans radiate tones at the fan blade-passing frequency.
  At take-off and climb, when the fan tips are supersonic, they also radiate
  "buzz-saw" tones at multiples of the shaft rotation rate (McAlpine & Fisher
  2001). These shift in Doppler like a prop tone.
- **Why it is hard.** Shaft speed changes with thrust during climb and approach,
  so the emitted frequency is not constant. That breaks the constant-f0 model
  dopplertrack relies on. The tones are strongly directional and high
  frequencies fade quickly with distance. Two of the open tools (teamchong,
  mojomast) report that jets rarely give usable tonal lines.
- **Yields and accuracy.** Point and time in principle; no evidence on accuracy.
- **Plugin fit.** As for props, but with a time-varying f0 model.
- **Effort.** M.
- **Recommendation.** **Not worth it** now. Revisit only as an extra term for 3.3.

### 3.5 Ships: horns and engine tones vs AIS

- **How it works.** AIS gives each vessel's position, speed, heading, length and
  type, much as ADS-B does for aircraft. Ship sounds are tied to those records in
  two ways:
  - **Whistle (horn) blasts.** The international collision rules fix the
    fundamental frequency by vessel length: 70–200 Hz for 200 m or longer,
    130–350 Hz for 75–200 m, and 250–700 Hz for shorter vessels. Audibility is
    about 0.5–2 nautical miles (33 CFR 86.01). The rules also say *when* horns
    sound. A prolonged blast lasts 4–6 s and a short blast about 1 s (Rule 32).
    US Inland Rule 34(g) requires a prolonged blast when leaving a dock or berth.
    Rule 35 requires one every 2 minutes or less in restricted visibility. A blast
    detected in a recording can therefore be checked against the length of each
    nearby AIS vessel, and against AIS departure events.
  - **Engine and propeller tones** of passing vessels, with Doppler. Ships are
    slow (10 knots is about 5 m/s, a shift of about 1.5 %), so the curves are
    gentle but long.
- **Yields.** A position near water (a point if several vessels or events match)
  and the clock.
- **Accuracy.** No published in-air study found. Underwater acoustics routinely
  uses AIS as ground truth for locating ships (for example Niu et al. 2017), which
  shows the data are good enough. MarineCadastre's points are about one minute
  apart per vessel, which limits event timing unless tracks are interpolated.
- **Reference data (verified 2026-11-04).**
  - US coastal waters, MarineCadastre: daily 2025 files at
    `noaaocm.blob.core.windows.net/ais/csv2/csv2025/` (`ais-YYYY-MM-DD.csv.zst`,
    about 230 MB a day). Columns include `mmsi`, `base_date_time` (seconds),
    position, speed, course, heading, vessel type, length and width. The older
    `coast.noaa.gov` 2024 index still lists files, but a sample file returned
    404.
  - Danish waters, Danish Maritime Authority: `aisdata.ais.dk` (an S3 bucket)
    has monthly files from 2006 and daily files in later years, the latest dated
    2026-11-01 (about 0.5 GB a day, zipped).
  - Elsewhere: national services vary, and global coverage is commercial.
- **Maturity.** Speculative in air.
- **Plugin fit.** Detector: horn-blast detector (a harmonic tone in 70–700 Hz
  lasting about 1 s or 4–6 s, in patterns) plus a slow-Doppler curve fit.
  Catalogue: AIS tracks with length and type, stored like the flight store.
  ForwardModel: arrival time and Doppler from the vessel's position at emission
  time; expected horn band from its length; departure events from speed
  changes. Scorer: timing residuals, band consistency and Doppler brightness.
- **Effort.** M. AIS ingestion mirrors the ADS-B flight store.
- **Recommendation.** **Later, spike first.** Record a ferry terminal or harbour
  with GPS and an accurate clock, and check that blasts and passes line up with
  AIS.

### 3.6 Foghorns and fixed fog signals

- **How it works.** Lighthouse and buoy fog signals have published
  characteristics (blast pattern and period) in the USCG Light List and NGA List
  of Lights. A recorded foghorn cycle could be matched to candidate stations.
- **Problems.** Many US fog signals are now Mariner Radio Activated Sound Signals
  (MRASS). They sound only after a mariner keys a VHF radio several times on a
  set channel, and then run for a limited period. So their absence means little,
  and their timing is not scheduled. I could not reach the Light List (HTTP 403)
  or the NGA publications page (HTTP 503) from a script.
- **Yields and accuracy.** "Within audible range of station X" (a few km), with
  no clock.
- **Effort.** S.
- **Recommendation.** **Not worth it.** It is rare, the data are awkward to get,
  and on-demand operation removes the schedule.

### 3.7 Trains: horns and pass-bys vs train-movement records

- **How it works.** A passing train (rolling noise, horn with Doppler) is heard
  at a time that matches the recorded movement of a train on a nearby line. In
  the US the horn rule is precise: two long, one short and one long blast,
  starting 15–20 s (at most 25 s) before a public crossing, except in quiet zones
  (49 CFR 222.21). The intervals between several passes in a recording can be
  matched against the train record at each point along a line, like the aircraft
  pair search but in one dimension.
- **Yields.** A position along a rail line, and the clock.
- **Accuracy.** Limited by the records. Most give times at stations or timing
  points, to the minute. At about 30 m/s, a one-minute error is about 2 km along
  the track *(estimate)*. Several passes help. Freight is almost absent from
  public data.
- **Reference data (reachability verified 2026-11-04).**
  - Netherlands: [Rijden de Treinen archive](https://www.rijdendetreinen.nl/en/open-data/train-archive).
    Every passenger service since 2019 (scheduled times plus delay in minutes,
    per stop), monthly files up to 2026-10, CC BY 4.0.
  - Switzerland: [Ist-Daten](https://opentransportdata.swiss/en/dataset/istdaten)
    and its [archive](https://archive.opentransportdata.swiss/actual_data_archive.htm).
    Per-stop scheduled and actual or forecast times, with a status flag for
    whether a time is real, and pass-through points. Only trips with real-time
    data are included. Licence terms to be checked.
  - UK: [Realtime Trains API](https://api.rtt.io/) (registration) and
    [Network Rail open data feeds](https://publicdatafeeds.networkrail.co.uk/)
    (live feeds; history needs our own capture).
  - US: [subwaydata.nyc](https://subwaydata.nyc/) (NYC subway stop times built
    from GTFS-realtime since 2021-04-01) and a third-party
    [Amtrak status archive](https://dixielandsoftware.net/Amtrak/status/StatusMaps/).
    GTFS-realtime feeds are live only. Archives exist only where someone records
    them.
  - Crossing locations: the [FRA crossing inventory](https://data.transportation.gov/Railroads/Crossing-Inventory-Data-Current/m2f8-22s6)
    and OSM (about 1.0 M `railway=level_crossing` nodes).
- **Maturity.** Speculative.
- **Plugin fit.** Detector: pass-by and horn detector, plus a horn-pattern
  classifier. Catalogue: per-country train-movement adapters plus OSM rail
  geometry. ForwardModel: interpolate train position along the track, then
  predict the pass time at *x* and, in the US, horn onset 15–20 s before
  crossings. Scorer: robust interval matching.
- **Effort.** M, mostly in the per-country adapters.
- **Recommendation.** **Later.**

### 3.8 Level-crossing warning sounds and horn rules

Crossing warning sounds and horn rules differ by country, so they are a country
cue. OSM tags `crossing:bell` on about 113,000 features. On its own this gives a
coarse region prior and "probably near a crossing". **Not worth it** alone;
could feed a general region-cue classifier later.

---

## 4. Impulsive events at known times and places

Lightning's machinery (onset detection, arrival time = event time + distance / c,
robust residuals) applies to any sharp sound whose source time and place are
recorded somewhere. Video adds the flash, which gives distance with no catalogue
at all.

### 4.1 Explosions and fireworks: flash-to-bang and shock arrival

- **How it works.** Light arrives effectively at once; sound arrives after d/c.
  Counting video frames from the flash to the blast sound gives the distance. For
  large explosions the shock travels faster than sound near the source, so
  blast arrival-time relations are needed instead of d = c·Δt. Rigby et al.
  (2020) did the inverse for the 2020 Beirut port explosion. From 16
  social-media videos with a line of sight to the blast, they estimated arrival
  times at 38 positions and fitted empirical blast laws, giving a best estimate
  of 0.50 kt TNT and an upper limit of 1.12 kt. Other Beirut studies used the
  fireball (Aouad et al. 2021), TNT/ANFO equivalence (Dewey 2021), images (Díaz
  2021), and open seismic and infrasound waveforms (Pilger et al.). Forensic
  Architecture synchronised its Beirut videos by the shape of the smoke plumes,
  not by sound.
- **Yields.** Distance to the event (a range ring), the absolute clock if the
  event time is known, and a point if the event location and the camera's view
  direction are both known.
- **Accuracy.** One frame at 30 fps is 33 ms, about 11 m. Onset picking and any
  audio/video offset in the file add to that; phone offsets of tens of ms are
  plausible but unverified. The speed of sound changes by about 0.6 m/s per °C.
- **Reference data.** None needed for the distance. Event time and place come
  from the user, news reporting or seismic catalogues (4.2). Large firework
  displays have published start times; New Year's Eve at local midnight is also a
  time-zone check.
- **Maturity.** Established in OSINT, with peer-reviewed casework.
- **Plugin fit.** Detector: flash frames (shared with lightning) plus acoustic
  onsets. Catalogue: user-supplied events, or 4.2. ForwardModel: arrival time =
  t₀ + d/c(T), with a shock correction for strong blasts. Scorer: robust
  residuals.
- **Effort.** S once lightning's flash and onset detectors exist.
- **Recommendation.** **Implement next**, together with lightning. Supporting
  user-supplied events serves verification work ("was this filmed 2 km from the
  blast, as claimed?").

### 4.2 Quarry and mining blasts, and earthquakes: seismic catalogues

- **How it works.** Seismic networks catalogue blasts and earthquakes with origin
  times and locations. A blast's air-wave reaches a listener at t₀ + d/c. A felt
  earthquake makes audible sound from the moment the P wave arrives. Hill et al.
  (1976) recorded 50–70 Hz sound starting within 0.02 s of the P arrival for
  magnitude 2.0–2.8 events 7–8 km away. The gap between P and S arrivals gives
  the distance, roughly 8 km per second of S−P at local distances (a rule of
  thumb; real work uses travel-time tables).
- **Yields.** Range ring and absolute clock.
- **Accuracy.** Limited by the catalogue's location error, which ComCat reports
  per event (`horizontalError`). For small events it is often about a kilometre
  or more. Origin times are good to fractions of a second.
- **Reference data (verified 2026-11-04).** The USGS ComCat FDSN API
  (`earthquake.usgs.gov/fdsnws/event/1/`) is free and lists event types
  including `quarry blast` (1,542 events in 2025), `explosion` (879 in 2025),
  `mining explosion` (2,574 since 2015), `chemical explosion` (134 since 2015)
  and `sonic boom` (32 since 2015). The EMSC FDSN service also responds. Blast
  coverage is mostly US.
- **Maturity.** Research (seismo-acoustics); not routine OSINT.
- **Plugin fit.** The same as lightning. Detector: onsets. Catalogue: ComCat and
  EMSC events. ForwardModel: t₀ + d/c through the air, or P and S travel times
  through the ground. Scorer: robust residuals.
- **Effort.** S–M.
- **Recommendation.** **Implement next**, as extra catalogues behind lightning's
  machinery.

### 4.3 Sonic booms: launches, landings, re-entries, fireballs

- **How it works.** A supersonic vehicle lays down a boom "carpet" roughly one
  mile wide per 1,000 ft of altitude (NASA fact sheet; for example about 50 miles
  at 50,000 ft). The relevant sources today are Falcon 9 booster landings at Cape
  Canaveral and Vandenberg, launches, capsule re-entries, and large meteors.
  Concorde's regular booms ended in 2003, so they only matter for old recordings.
  BYU has measured Falcon 9 launch, re-entry and landing booms (Durrant et al.
  2023; Anderson & Gee 2024) and Starship launches (Gee et al. 2024). Their
  abstracts report Falcon 9 landing booms observable at least about 25 km from
  the pad.
- **Yields.** Absolute clock (seconds) and a coarse region (inside the carpet,
  tens of km wide).
- **Accuracy.** Boom arrival time depends on the trajectory and the atmosphere.
  Without a trajectory it gives a clock to seconds and a region to tens of km.
  A modelled carpet would do better (not evaluated).
- **Reference data (verified 2026-11-04).**
  [Launch Library 2](https://ll.thespacedevs.com/2.3.0/launches/previous/) (free;
  rate limits not checked) gives launch times with a stated precision.
  [GCAT](https://planet4589.org/space/gcat/tsv/launch/launch.tsv) (McDowell) is a
  launch list updated 2026-11-02. The
  [CNEOS fireball API](https://ssd-api.jpl.nasa.gov/fireball.api) gives the time
  to the second and the position to 0.1°. Trajectories are rarely public; FAA
  environmental documents sometimes include boom contour maps.
- **Maturity.** Research (measurement campaigns), not yet used for geolocation.
- **Plugin fit.** Catalogue: launch, landing and fireball events. ForwardModel: a
  nominal source point and altitude with wide tolerance, or a full ray-traced
  boom model (L). Scorer: residuals with wide tolerance.
- **Effort.** M.
- **Recommendation.** **Later**, as catalogues inside the 4.2 framework. It is
  mostly a clock anchor for recordings in Florida and California.

### 4.4 Gunshots: shock wave vs muzzle blast ("crack–bang")

- **How it works.** A supersonic bullet's shock wave (the "crack") reaches a
  microphone near its path before the muzzle blast (the "bang"). The gap grows
  with distance: d ≈ Δt / (1/c − 1/V̄), where V̄ is the bullet's average speed
  (Maher 2022). For example, Δt = 0.3 s with V̄ = 762–884 m/s gives 167–186 m. It
  only works when the microphone is near the bullet's path, down range. There is
  no crack with subsonic ammunition or behind the shooter.
- **Casework.**
  - Shireen Abu Akleh, Jenin, 2022:
    - Forensic Architecture's audio report (researcher in charge: Lawrence Abu
      Hamdan): about 300 ms, which at about 860 m/s is about 180 m. The same gap
      on all 13 shots indicates a single position.
    - Bellingcat, analysis by Maher: 177–184 m.
    - Maher's AES paper: 167–186 m, with an uncertainty analysis.
    - The Washington Post (Steven Beck) and the New York Times also used audio
      analysis (reported).
  - Butler, Pennsylvania, 2024: Maher estimated about 130 m to the podium
    microphone from nearly two dozen recordings. Comparing recordings showed
    that the last two of ten shots came from two other locations. AP's
    geolocation put the roof about 147 yards (135 m) from the stage (seen only
    in search results, not opened).
  - Bellingcat, Gaza aid workers, 2025: about 50 ms, which gives about 30 m.
  - Forensic Architecture and Earshot, Hind Rajab, 2024: 13–23 m (reported).
- **Yields.** Distance from the shooter to the microphone. A gap that stays the
  same across shots means a single position, and the firing rate suggests a
  weapon class. It does not locate the recording on its own. Combined with a
  known or claimed shooter position, it gives a range ring for the camera, and
  vice versa.
- **Accuracy.** Mainly limited by the assumed bullet speed: about ±5–10 % for a
  known cartridge. Maher (2022) adds about +2 m per metre of microphone offset
  from the trajectory, about ±0.6 m per °C, and about ±2 m for a 2 m/s wind along
  the path, at about 180 m. Echoes and lossy codecs blur the onsets (Beck et al.
  2011 document waveform variability).
- **Reference data.** None, apart from tables of bullet velocity by cartridge.
- **Maturity.** Established forensic and OSINT practice.
- **Plugin fit.** It only fits the catalogue pattern if the user supplies a
  shooter hypothesis (a user-supplied catalogue entry). It is better treated as
  an analysis panel that outputs a distance with uncertainty.
- **Effort.** S for a calculator with uncertainty; M for automatic detection of
  crack and bang.
- **Recommendation.** **Later**, as an analyst tool, after a responsible-use
  review: it estimates where a shooter was.

### 4.5 Several recordings: synchronisation and TDOA

- **How it works.** Synchronise several recordings of the same events, by shared
  sounds, ENF or visual cues. The differences in arrival time of one impulse at
  known camera positions then locate its source (multilateration). With a known
  source, the same differences constrain the camera positions. CMU's VERA system
  (Liang, Aronson & Hauptmann 2019; open code) located the 2017 Las Vegas
  shooter from crowd videos using crack and bang timing across cameras. Earshot
  describes a "Supersonic Compass" tool that estimates the shooter's position
  from the shock wave and muzzle blast, given the camera's position (no technical
  details published), and has used changes in a drone's pitch and loudness to
  reconstruct its circling.
- **Yields.** Source position, or relative camera geometry.
- **Accuracy.** Metres to tens of metres with good synchronisation and three or
  four known positions *(estimate)*. VERA publishes no error figure.
- **Maturity.** Established in OSINT; research tools exist.
- **Plugin fit.** A different shape: one case with several recordings sharing one
  set of events, where the "catalogue" is the other recordings. It needs
  multi-recording cases in the data model. That would also help aircraft (two
  recordings of the same flyover).
- **Effort.** L.
- **Recommendation.** **Later.** Keep multi-recording cases in mind when the core
  is refactored in M5.

---

## 5. Scheduled and broadcast sounds

These are mostly **clock anchors**: they say when, plus a country or region.

### 5.1 Civil-defence siren tests and siren tone patterns

- **How it works.** Many countries test their sirens on a fixed schedule with
  defined tone patterns. A test in a recording gives the date, the local time and
  the country. The patterns themselves (rising and falling, or steady, and their
  durations) differ by country.
- **Schedules checked (2026-11-04).**

| Country | Schedule | Signal |
|---|---|---|
| Netherlands | First Monday of each month, 12:00 | About 4,278 sirens. In May 2026 the government planned to switch them off in 2028; in June 2026 it reversed course and said it would replace them. Status in flux |
| Germany | Bundesweiter Warntag: second Thursday of September, 11:00 (10 Sep 2026; next 9 Sep 2027) | National test of sirens, apps and cell broadcast. States also run their own tests |
| Switzerland | First Wednesday of February: general-alarm test from 13:30 (4 Feb 2026) | Water-alarm sirens tested afterwards |
| Austria | Every Saturday, 12:00: 15 s steady tone (confirmed for Tyrol and Lower Austria). First Saturday of October: national test | October sequence: 12:15 warning (3 min steady), 12:30 alarm (1 min rising and falling), 12:45 all-clear (1 min steady). About 8,300 sirens |
| France | First Wednesday of each month, about 12:00 | 1 min 41 s (order of 23 March 2007). Moved if the day is a holiday |
| Czech Republic | First Wednesday of each month, 12:00 | 140 s. Reported suspended in 2022; current status not verified |
| Finland | First weekday Monday of each month, 12:00 | 7 s steady test signal |
| Sweden | "Hesa Fredrik": first non-holiday Monday of March, June, September and December, 15:00 | Cell-broadcast alerts reportedly added from September 2026 |

- **Yields.** Clock (to about a minute) and country. With several sirens
  audible, their relative delays could constrain position (speculative).
- **Accuracy.** Start times can vary locally by minutes (French sources mention
  11:45–12:15; one Czech region once started 10 minutes late).
- **Reference data.** A hand-maintained rules table with holidays. OSM has about
  26,000 `emergency=siren` features, with uneven coverage.
- **Maturity.** Speculative, but the physics is trivial.
- **Plugin fit.** Detector: siren-tone classifier. Catalogue: events generated
  from the rules (country, date, local time, pattern). ForwardModel: the event
  at local time within a tolerance. Scorer: a likelihood over the clock, plus a
  country prior.
- **Effort.** S–M.
- **Recommendation.** **Later.** A cheap clock anchor for European recordings.

### 5.2 Church bells and clock chimes

- **How it works.**
  - **Tower fingerprint.** In English-style change ringing, every bell in a ring
    sounds in turn, again and again. A recording therefore reveals how many bells
    there are and their nominal (main partial) frequencies. Dove's Guide lists
    those frequencies per bell. Matching the set of frequencies against Dove
    could identify the tower.
  - **Clock strikes.** Hour strikes and quarter chimes give the local time, if the
    clock is right.
- **Reference data (verified).** [Dove's Guide downloads](https://dove.cccbr.org.uk/downloads),
  licensed CC BY-SA 4.0 (derived data must be shared alike).
  - `dove.csv`: 7,262 rings with coordinates, number of bells, tenor note,
    frequency and weight, and practice night. 6,779 of the rings are in England;
    there are smaller numbers in Wales, Australia, the US, Ireland and Scotland.
    3,473 towers list a practice night.
  - `bells.csv`: 64,136 bells, 46,983 with a nominal frequency in Hz. 6,362
    full-circle rings have a frequency for every bell. Chimes and carillons are
    listed too.
- **Yields.** Tower identity (an exact point) if the match is unique. Practice
  nights and service times add a weak time prior.
- **Accuracy.** Untested. Risks: many rings share a key; the frequency data vary
  in quality; recordings of distant bells are distorted; rings can overlap; bell
  partials are complex.
- **Maturity.** Speculative. No published use found.
- **Plugin fit.** Detector: bell-partial extraction (long FFTs, peak picking,
  clustering per bell). Catalogue: Dove rings. ForwardModel: a static set of
  frequencies. Scorer: assignment-based matching with a tolerance in Hz. Output:
  candidate points, with no clock.
- **Effort.** M.
- **Recommendation.** **Later, spike first.** The data are ready to use; a few
  recordings of known towers would show quickly whether the frequency sets
  discriminate.

### 5.3 Call to prayer timing

- **How it works.** The five daily calls are tied to the sun. Dhuhr falls just
  after solar noon and Maghrib at sunset. Fajr and Isha are set by sun-depression
  angles that differ by convention: the Aladhan API lists over 20 methods, with
  Fajr between 12° and 20°, and Isha between 12° and 18° or a fixed interval
  after Maghrib (often 90 minutes). Asr depends on shadow length and has two
  schools. If the recording's UTC time is known (from ENF, aircraft or
  lightning), the start of a call constrains longitude, because solar noon moves
  4 minutes per degree. For
  Maghrib and Fajr it also constrains latitude, given the date. If the place is
  known instead, the call gives the local time.
- **Yields.** A longitude band, or the local time.
- **Accuracy.** Mosques often call some minutes after the computed time, and
  conventions vary. ±5 minutes is about ±1.25° of longitude, or about 100 km at
  mid-latitudes *(estimate)*.
- **Reference data.** Prayer-time calculators ([Aladhan API](https://aladhan.com/prayer-times-api),
  free and verified; [PrayTimes.org](https://praytimes.org), open source). OSM has
  about 343,000 `religion=muslim` features for mosque locations.
- **Maturity.** Speculative. I found adhan audio used in fact-checks to trace a
  clip's origin, not to time it.
- **Plugin fit.** Detector: an adhan detector (a melodic call with recognisable
  phrases; ASR keyword spotting). Catalogue: prayer times computed per grid cell
  and date for several conventions. ForwardModel: a distribution of start times
  (computed time plus 0–10 minutes). Scorer: joint over (*x*, clock).
- **Effort.** S–M.
- **Recommendation.** **Later.** Coarse but cheap, and useful for checking a
  claimed place and time.

### 5.4 Broadcast audio fingerprinting and time signals

- **How it works.** Background radio or TV can be identified with landmark
  fingerprinting: pairs of spectrogram peaks, hashed, which is robust to noise
  and voice-over (Wang 2003, the Shazam method). Open options include Panako,
  which handles ±10 % time and pitch changes, and Chromaprint/AcoustID (LGPL
  2.1 overall), which is built for near-identical audio rather than noisy
  queries. Matching against a recording of the broadcast gives the absolute time,
  to within the playout and delivery delay, and the station implies a coverage
  area. Hourly time signals mark exact times at the source.
- **The problem is the reference.** There is no free archive of what every
  station aired. Identifying a song identifies the track, not the time; that needs
  a station's playout log. Commercial broadcast-monitoring services exist but
  were not evaluated. A user can supply a reference, such as a recording of a
  station's stream.
- **Yields.** Clock (seconds) and a station coverage region.
- **Accuracy.** Seconds, plus a delivery delay that depends on the platform
  (terrestrial vs digital vs streaming; not measured here). The delay itself
  hints at the platform.
- **Maturity.** Established technology; using it for geolocation is limited by
  the data.
- **Plugin fit.** Detector: fingerprint extraction. Catalogue: user-supplied
  reference recordings or logs. ForwardModel: arrival = air time + platform
  delay. Scorer: peak in the histogram of time offsets.
- **Effort.** M with user-supplied references; L for a general archive.
- **Recommendation.** **Later**, with user-supplied references only.

### 5.5 Emergency-alert data bursts (EAS/SAME) and alert chimes

- **How it works.**
  - **US EAS.** Every alert starts with three data bursts in audio frequency-shift
    keying: 520.83 baud, mark 2083.3 Hz, space 1562.5 Hz. The header reads
    `ZCZC-ORG-EEE-PSSCCC+TTTT-JJJHHMM-LLLLLLLL-`: originator, event code, up to 31
    county (FIPS) location codes, valid period, issue day-of-year and UTC time,
    and the sending station's ID. A two-tone attention signal (853 + 960 Hz)
    follows for 8–25 s (47 CFR 11.31). Broadcast stations must send a header and
    end-of-message test at least once a week, at random times (47 CFR 11.61). A
    burst decoded from background audio therefore leaks the counties, the issue
    time to the minute and the station.
  - **Japan.** The Japan Meteorological Agency publishes every earthquake
    early-warning alert since 2007-10-01, with the warned areas. The warning chime
    on TV and phones would then give a time (to seconds) and a region (detection
    not tested).
- **Yields.** County list, issue time to the minute, station identity.
- **Accuracy.** Exact once decoded. The region is the alert or broadcast area
  (county to multi-county scale).
- **Reference data (verified).** Open decoders exist (multimon-ng supports EAS).
  For cross-checks: the [OpenFEMA IPAWS archived alerts API](https://www.fema.gov/api/open/v1/IpawsArchivedAlerts)
  (latest entry 2026-11-03), [IEM's archive of NWS warnings](https://mesonet.agron.iastate.edu/vtec/),
  and [JMA's EEW history](https://www.data.jma.go.jp/svd/eew/data/nc/rireki/rireki.html).
- **Maturity.** Established technology. Using it as a geolocation leak is new.
- **Plugin fit.** Detector: AFSK decoder. Catalogue: the archives (for
  validation; not required). ForwardModel: trivial. Scorer: a hard constraint on
  the clock (allowing for delay) plus a region prior from county polygons.
- **Effort.** S.
- **Recommendation.** **Later.** US-only and rare, but cheap and decisive when
  present. It also suits the "privacy check" framing in PLAN.md section 15.

### 5.6 Language, dialect and spoken place names

- **How it works.** Language identification (Whisper covers about 100 languages;
  VoxLingua107 has 107) gives a region prior. Transcripts can reveal place names,
  station announcements and shop names. Accent and dialect identification are
  research-grade.
- **Yields.** A country or region prior; occasionally an exact place.
- **Accuracy.** Language ID is reliable for major languages, but languages map
  loosely to places (migration, travel).
- **Maturity.** Established ML; listening for language and place names is common
  OSINT practice.
- **Plugin fit.** A prior layer with no clock.
- **Effort.** S.
- **Recommendation.** **Not core.** It processes speech, which conflicts with the
  planned privacy mode (PLAN.md section 6), where speech never leaves the
  browser. If offered at all, make it local and opt-in.

---

## 6. Mains electricity

### 6.1 ENF timestamping and grid identification (literature only)

- **How it works.** Mains frequency wanders around its nominal value by tens of
  millihertz, in a pattern shared across a synchronous grid. Recordings pick up
  hum, electrically or acoustically, and mains-powered lights flicker at twice
  the mains frequency.
  - Matching the extracted ENF trace against grid frequency logs dates the
    recording (Grigoras 2005, 2007). Short recordings are harder (Huijbregtse &
    Geradts 2009). Phase discontinuities reveal edits (Nicolalde Rodriguez et al.
    2010).
  - ENF statistics can identify the grid even without a reference recorded at
    the same time (Hajj-Ahmad, Garg & Wu 2015). The IEEE Signal Processing Cup
    2016 dataset covers nine grids.
  - Locating a recording *within* one grid, from small local differences, is only
    weakly feasible and needs strong traces (Garg, Hajj-Ahmad & Wu 2013, 2021).
  - In video, ENF can be read from light flicker, including rolling-shutter
    tricks that recover it at normal frame rates (Garg et al. 2013 "Seeing ENF";
    Su et al. 2014).
  - Practical problems: weak or absent ENF, false detections, and
    battery-powered devices (Hua, Bi & Thing 2017).
- **Yields.** Timestamp (seconds, given minutes of clean signal), grid identity,
  and at best a weak position within a grid.
- **Grid membership changes over time.** For example, the Baltic states left the
  Russian-linked grid and joined the Continental European synchronous area on
  2025-02-09. Grid identification must therefore take the date into account.
- **Maturity.** Timestamping and authenticity checks are established forensic
  practice; grid identification is research; position within a grid is
  speculative.
- **Plugin fit.** Detector: ENF trace. Catalogue: grid frequency logs.
  ForwardModel: the recorded trace equals the grid's trace at the clock offset.
  Scorer: correlation over clock offsets, per grid. For TrackSound its main value
  is as a **clock anchor** that switches the aircraft search to single-event mode.
- **Recommendation.** In progress (another agent is verifying the data sources).

### 6.2 Nominal mains frequency (50 vs 60 Hz) as a region prior

The hum fundamental and its harmonics (100/120 Hz and so on, for example from
transformers) give the nominal frequency. 60 Hz is used in North and Central
America and much of the Caribbean, and in Brazil, Colombia, Ecuador, Peru,
Venezuela, Guyana, Suriname, Saudi Arabia, South Korea, Taiwan, the
Philippines, Liberia and western Japan. Almost everywhere else (Europe, Africa,
most of Asia, Oceania, southern South America, eastern Japan) uses 50 Hz. It is
a crude but robust prior. Beware equipment with its own supply, such as
generators and vehicles. It falls out of the ENF detector at little cost
(effort S).

---

## 7. Wildlife

- **How it works.** Identify species from their calls (BirdNET; Cornell's Merlin)
  and combine them with where and when those species occur. Sources for that are
  BirdNET's own species-occurrence model by latitude, longitude and week; eBird
  Status & Trends (weekly relative abundance on a 3 km grid); and GBIF or
  iNaturalist occurrence records. The result is a likelihood over location and
  season.
- **Evidence.** The 2025 "Audio Geolocation: A Natural Sounds Benchmark" (Chasmai
  et al.) used about 230,000 iNatSounds recordings. Its best method had a median
  error of 1,082 km for a single recording: 6.4 % within 25 km, 17.2 % within
  200 km, 41 % within 750 km and 71 % within 2,500 km. Pooling recordings from
  the same area improved the median to 520 km. With a perfect species list the
  method would reach 97.8 % within 200 km, so the bottleneck is identifying the
  species, not the range maps. In OSINT, Bellingcat's toolkit lists Merlin for
  checking where a bird recording could come from.
- **Insects.** US periodical cicadas emerge in known broods and years, a strong
  year-and-region cue (general knowledge; not checked here).
- **Yields.** Region and season prior; a plausibility check on claims (a species
  that cannot be at the claimed place in the claimed month).
- **Reference data and licences.** BirdNET-Analyzer covers 6,512 species. Its code
  is MIT, but its models are CC BY-NC-SA 4.0 (non-commercial). The original
  BirdNET paper covered 984 species. eBird Status & Trends needs an access key and
  has its own terms of use (non-commercial by default, limits on
  redistribution).
- **Maturity.** Research.
- **Plugin fit.** A prior layer that depends on the date (week), with no clock
  offset.
- **Effort.** M.
- **Recommendation.** **Later**, as a prior and plausibility check. Settle the
  non-commercial licences before any hosted use.

---

## 8. Signals with little or no location value

### 8.1 Seismic and infrasound coupling

Consumer recording chains cut low frequencies, and infrasound networks (the
CTBTO's International Monitoring System) are not open data; access is by
agreement only. The useful case,
felt earthquakes, is covered in 4.2. **Not worth it** beyond that.

### 8.2 Wind turbines

Turbine noise has a "swish" amplitude modulation at the blade-passing rate (three
times the rotor speed, roughly 0.3–1 Hz for modern turbines; Oerlemans et al.
2007 explain the mechanism). Turbine positions are public: the [USWTDB API](https://energy.usgs.gov/api/uswtdb/v1/turbines)
(verified; it includes model, hub height and rotor diameter) and about 480,000
`generator:source=wind` features in OSM. That gives "near some turbines" and
little more, because rotor speed (SCADA) data are not public. **Not worth it.**

### 8.3 Road traffic, acoustic scene classification, city identification

Scene classification (the DCASE challenges) labels the environment (street, park,
metro), not the place. Identifying the city from audio alone has been studied on
Flickr videos, as classification among a small set of cities (Lei, Choi &
Friedland 2012; Elizalde et al. 2016, using the MediaEval Placing Task data). I
found nothing suggesting it would be useful for TrackSound. Emergency-vehicle
siren styles are a country cue at best. **Not worth it.**

### 8.4 Reverberation and echoes

Echoes can recover room geometry (Dokmanić et al. 2013), not location. Outdoor
echoes off known façades might refine the position of an impulsive source in
OSINT work, but that needs 3D city models and is speculative. **Not worth it**
now.

### 8.5 Weather sounds vs radar

Rain or hail starting in a recording could be compared with radar at candidate
locations. NEXRAD Level II data are free on AWS (the `unidata-nexrad-level2`
bucket has 2026-11-01 data); other regions were not checked. It is a weak
consistency check with a hard detection problem. **Not worth it** now.

### 8.6 Other ideas, not evaluated

All unverified:

- GSM handsets cause a buzz at the 217 Hz frame rate. Since many countries have
  switched off 2G, the buzz would be a weak date-and-region cue.
- Cricket chirp rate tracks air temperature (Dolbear's law) and could be compared
  with weather archives.
- Stadium roars could be matched to goal times.
- Air-traffic-control radio in plane-spotter recordings could be matched to
  LiveATC archives (the site blocked scripted access).

---

## 9. Cross-cutting observations

1. **Clocks are as valuable as locations.** A trusted clock turns the aircraft
   search into single-event mode. The core should accept clock-only evidence (a
   likelihood over clock offset, with an optional region prior) from ENF, sirens,
   EAS, broadcasts and known-time events.
2. **Priors need a home.** Mains frequency, birds and language produce score maps
   over *x* (and sometimes the date) with no clock. They should feed the same
   log-likelihood fusion as the main sources (PLAN.md section 7), with low
   weights until calibrated.
3. **User-supplied catalogue entries.** Verification work often starts from a
   claim: an explosion at a known time and place, a shooter position, a radio
   station. Catalogues should accept manual entries with their uncertainties,
   recorded as provenance in the evidence panel.
4. **Multi-recording cases** (4.5) are a real OSINT workflow. They would also let
   two recordings of the same flyover share evidence. Consider them in the M5
   refactor.
5. **Calibration is the hard part.** The open aircraft tools found real-world
   failure rates far above synthetic ones, and confidence regions that were
   overconfident until widened. Every new source needs its own null tests
   (decoy events, time-shifted catalogues) before it is fused.
6. **Responsible use.** Gunshot ranging (4.4) and speech-based cues (5.6) raise
   the stakes beyond "where was this recorded". Gate them behind the
   responsible-use measures in PLAN.md section 15. Keep speech processing out of
   the privacy mode.

## 10. Recommended roadmap

Ranked, for after prop aircraft, lightning and ENF.

1. **Extend the aircraft source: helicopters now, jets next (3.2, 3.3).**
   - Helicopters are about S: keep helicopter types and lower the detection band.
   - Jets via ground-reflection delay are about M.
   - Why first: they use the same ADS-B data, flight store, grid, clock and UI.
     Jets are most of the traffic near airports and cities and have the best
     ADS-B coverage. The main real-world failure of the tonal method is "no
     events" on jet-dominated clips, and this addresses it directly. There is
     solid prior art (Lo, Perry & Ferguson 2002; Lo et al. 2003) and an early
     open implementation with mixed but real successes.
   - Exit test: our own ground-truth clips under approach paths.
2. **Generalise lightning into a "known-time impulsive events" source (4.1–4.3).**
   - Add catalogues: USGS ComCat and EMSC (blasts, earthquakes, sonic booms),
     rocket launches and landings (Launch Library 2, GCAT), CNEOS fireballs, and
     user-supplied events.
   - Add video flash-to-bang ranging for any flash, not only lightning.
   - Why: once lightning exists the Detector, ForwardModel and Scorer are the
     same, the catalogues are free APIs we have checked, and each match gives a
     range ring plus an absolute clock. Flash-to-bang against a known event is
     established OSINT practice.
   - Effort: S–M.
3. **Cheap clock anchors from scheduled or encoded signals (5.1, 5.5).**
   - An EAS/SAME decoder (US): S.
   - A rules table of siren tests with a siren-tone detector (Europe): S–M.
   - Later, the JMA alert chime and time signals.
   - Why: each is small, rare in recordings, and decisive when present: a minute-
     or second-level clock plus a country or county. They feed the
     single-event aircraft mode.
4. **Ships vs AIS: spike (3.5).** M. Free daily AIS is available and verified for
   US and Danish waters. AIS carries vessel length, which the collision rules tie
   to horn pitch. The data model mirrors the flight store. There is no in-air
   prior art, so start with a field spike at a ferry terminal.
5. **Bell-tower fingerprints vs Dove's Guide: spike (5.2).** M. The data are free
   and detailed (6,362 complete rings with per-bell frequencies). Coverage is
   England-heavy and the method is unproven, but a positive result would give
   tower-level precision.
6. **Trains (3.7).** M. Good open data in the Netherlands and Switzerland,
   usable data in the UK and NYC, and an exact horn rule in the US. Limited by
   minute-level timing and missing freight data.
7. **Coarse priors: 50/60 Hz from the ENF work (S), birds (M), call to prayer
   (S–M).** Mainly for plausibility checks and narrowing blind-search regions.
   Settle licences first (BirdNET models and eBird data are non-commercial).
8. **Gunshot crack–bang calculator and multi-recording TDOA (4.4, 4.5).**
   Established practice, but they estimate where a *source* was rather than the
   recording, and they are sensitive. Do these only after a responsible-use
   review, and as analyst tools.

Not worth it for now: jet fan tones (3.4), foghorns (3.6), crossing sounds on
their own (3.8), language ID as a core source (5.6), broadcast fingerprinting
without a user-supplied reference (5.4), wind turbines, traffic and scene
classification, reverberation, infrasound and weather sounds (section 8).

## 11. What could not be verified

- **Portals that blocked scripts or did not respond (2026-11-04).**
  - USCG NAVCEN Light List: HTTP 403.
  - NGA Maritime Safety Information: HTTP 503.
  - LiveATC: Cloudflare challenge (HTTP 403).
  - CelesTrak (checked for satellite passes, which are out of scope): no
    response.
  - eBird Status & Trends download page: HTTP 503. Resolution and species counts
    come from the `ebirdst` documentation as indexed by search.
  - Several news sites and GitHub's web pages returned 403 to scripts. Tool
    READMEs were read through `raw.githubusercontent.com`.
- **AIS.** The `coast.noaa.gov` 2024 AIS index lists daily files, but a sample
  file returned 404. The 2025 files on Azure blob storage are reachable.
- **Casework known only from secondary reports.**
  - Methods used by the New York Times and the Washington Post in the Abu Akleh
    case: known from secondary coverage.
  - The Hind Rajab distance (13–23 m) and its method: from press coverage of the
    Forensic Architecture report.
  - AP's roof distance at Butler: seen only in search results.
  - I did not find a BBC Verify piece that documents an audio method, so BBC
    Verify practice is not covered.
- **Siren schedules.** Some are from official pages, some from press reports. The
  Czech schedule's status after its 2022 suspension, and the Dutch sirens'
  future, are unclear.
- **Unmeasured quantities.** Phone audio/video offsets, broadcast platform delays,
  and helicopter ADS-B coverage were not measured. Figures marked *(estimate)*
  are mine.
- **Open-tool results.** Accuracy figures for the open-source aircraft tools are
  the projects' own reports, not independently reproduced.

## 12. Sources

### Aircraft, helicopters and jets

- Ferguson, B. G. (1992). [A ground-based narrow-band passive acoustic technique for estimating the altitude and speed of a propeller-driven aircraft](https://doi.org/10.1121/1.403934). JASA 92(3), 1403–1407.
- Ferguson, B. G. & Quinn, B. G. (1994). [Application of the short-time Fourier transform and the Wigner–Ville distribution to the acoustic localization of aircraft](https://doi.org/10.1121/1.410320). JASA 96(2), 821–827.
- Reid, D. C., Zoubir, A. M. & Boashash, B. (1996). [The bootstrap applied to passive acoustic aircraft parameter estimation](https://doi.org/10.1109/icassp.1996.550545). ICASSP 1996.
- Ferguson, B. G. & Lo, K. W. (2000). [Turboprop and rotary-wing aircraft flight parameter estimation using both narrow-band and broadband passive acoustic signal-processing methods](https://doi.org/10.1121/1.1286150). JASA 108(4), 1763–1771.
- Lo, K. W. & Ferguson, B. G. (2000). [Broadband passive acoustic technique for target motion parameter estimation](https://doi.org/10.1109/7.826319). IEEE TAES 36(1), 163–175.
- Lo, K. W., Perry, S. W. & Ferguson, B. G. (2002). [Aircraft flight parameter estimation using acoustical Lloyd's mirror effect](https://doi.org/10.1109/7.993235). IEEE TAES 38(1), 137–151.
- Lo, K. W., Ferguson, B. G., Gao, Y. & Maguer, A. (2003). [Aircraft flight parameter estimation using acoustic multipath delays](https://doi.org/10.1109/TAES.2003.1188908). IEEE TAES 39(1), 259–268.
- Sadasivan, S., Gurubasavaraj, P. M. & Sekar, S. R. (2001). [Acoustic signature of an unmanned air vehicle exploitation for aircraft localisation and parameter estimation](https://doi.org/10.14429/dsj.51.2238). Defence Science Journal 51(3), 279–284.
- Stutz, C. M. et al. (NASA Langley). [Acoustic Assessment of an MD530F Helicopter in Maneuvering Flight](https://ntrs.nasa.gov/citations/20250002950). NTRS 20250002950 (main-rotor blade-passing frequency 39.75 Hz).
- Brentner, K. S. & Farassat, F. (2003). [Modeling aerodynamically generated sound of helicopter rotors](https://doi.org/10.1016/s0376-0421(02)00068-4). Progress in Aerospace Sciences 39, 83–120.
- McAlpine, A. & Fisher, M. J. (2001). [On the prediction of "buzz-saw" noise in aero-engine inlet ducts](https://doi.org/10.1006/jsvi.2001.3770). Journal of Sound and Vibration 248(1), 123–149.
- Farnborough Aerodrome Consultative Committee, [Noise & track monitoring](https://www.facc.org.uk/airport-information/noise-track-monitoring/); Port Authority of NY & NJ, [WebTrak](https://aircraftnoise.panynj.gov/webtrak/).
- colsto, ["I discovered a sound that leaks your exact location (and it's everywhere)"](https://www.youtube.com/watch?v=UytYrhYLOKI).
- Open tools: [dopplertrack](https://github.com/mjshiggins/dopplertrack), [hermes-aircraft-doppler-geolocation](https://github.com/mojomast/hermes-aircraft-doppler-geolocation), [doppler-geo](https://github.com/teamchong/doppler-geo), [audio-flyover-geolocation](https://github.com/AlanBacker/audio-flyover-geolocation), [flyover-locator](https://github.com/amintt2/flyover-locator), [DOPPLERGEIST](https://github.com/1337isnot1337/DOPPLERGEIST).

### Ships and foghorns

- [33 CFR 86.01](https://www.ecfr.gov/current/title-33/section-86.01), whistle frequencies and audibility (Annex III).
- [33 CFR 83.32](https://www.ecfr.gov/current/title-33/section-83.32), [83.34](https://www.ecfr.gov/current/title-33/section-83.34) and [83.35](https://www.ecfr.gov/current/title-33/section-83.35), blast definitions and when signals are sounded (US Inland Rules).
- MarineCadastre, [Vessel traffic](https://hub.marinecadastre.gov/pages/vesseltraffic) and the [2025 daily AIS index](https://noaaocm.blob.core.windows.net/ais/csv2/csv2025/index.html).
- Danish Maritime Authority, [AIS data](http://aisdata.ais.dk/).
- Niu, H., Reeves, E. & Gerstoft, P. (2017). [Source localization in an ocean waveguide using supervised machine learning](https://doi.org/10.1121/1.5000165). JASA 142(3), 1176–1188.
- US Coast Guard, [MRASS installation notice](https://content.govdelivery.com/accounts/USDHSCG/bulletins/244a015).

### Trains

- [49 CFR 222.21](https://www.ecfr.gov/current/title-49/section-222.21), when the locomotive horn must be sounded.
- FRA, [Crossing Inventory Data](https://data.transportation.gov/Railroads/Crossing-Inventory-Data-Current/m2f8-22s6).
- Rijden de Treinen, [Train archive](https://www.rijdendetreinen.nl/en/open-data/train-archive) (CC BY 4.0).
- opentransportdata.swiss, [Ist-Daten](https://opentransportdata.swiss/en/dataset/istdaten), [archive](https://archive.opentransportdata.swiss/actual_data_archive.htm) and [cookbook](https://opentransportdata.swiss/cookbook/actual-data/).
- [Realtime Trains API](https://api.rtt.io/); [Network Rail open data feeds](https://publicdatafeeds.networkrail.co.uk/); [subwaydata.nyc](https://subwaydata.nyc/); [Amtrak status maps archive](https://dixielandsoftware.net/Amtrak/status/StatusMaps/).
- OpenStreetMap taginfo: [railway=level_crossing](https://taginfo.openstreetmap.org/tags/railway=level_crossing), [crossing:bell](https://taginfo.openstreetmap.org/keys/crossing:bell).

### Explosions, seismic events and sonic booms

- Rigby, S. E. et al. (2020). [Preliminary yield estimation of the 2020 Beirut explosion using video footage from social media](https://doi.org/10.1007/s00193-020-00970-z). Shock Waves 30, 671–675 (CC BY 4.0).
- Pilger, C. et al. (2020 preprint). [Yield estimation of the 2020 Beirut explosion using open access waveform and remote sensing data](https://doi.org/10.31223/x5w027). EarthArXiv.
- Dewey, J. M. (2021). [The TNT and ANFO equivalences of the Beirut explosion](https://doi.org/10.1007/s00193-021-00992-1). Shock Waves 31, 95–99.
- Aouad, C. J. et al. (2021). [Beirut explosion: TNT equivalence from the fireball evolution in the first 170 milliseconds](https://doi.org/10.1007/s00193-021-01031-9). Shock Waves 31, 813–827.
- Díaz, J. S. (2021). [Explosion analysis from images: Trinity and Beirut](https://doi.org/10.1088/1361-6404/abe131). European Journal of Physics 42, 035803.
- PetaPixel, [Forensic experts used photos and videos from social media to reconstruct Beirut explosion](https://petapixel.com/2020/11/20/forensic-experts-used-photos-and-videos-from-social-media-to-reconstruct-beirut-explosion) (Forensic Architecture's method, secondary).
- USGS, [ComCat FDSN event service](https://earthquake.usgs.gov/fdsnws/event/1/); EMSC, [FDSN event service](https://www.seismicportal.eu/fdsnws/event/1/).
- Hill, D. P., Fischer, F. G., Lahr, K. M. & Coakley, J. M. (1976). [Earthquake sounds generated by body-wave ground motion](https://www.osti.gov/biblio/5042236). BSSA 66(4), 1159–1172.
- USGS, [Earthquake travel times](https://www.usgs.gov/programs/earthquake-hazards/earthquake-travel-times).
- NASA Dryden, [Sonic booms fact sheet FS-016](https://www.nasa.gov/centers/dryden/pdf/120274main_FS-016-DFRC.pdf).
- Plotkin, K. J. (2002). [State of the art of sonic boom modeling](https://doi.org/10.1121/1.1379075). JASA 111(1), 530–536.
- Durrant, J. T. et al. (2023). [Overview and spectral analysis of the Falcon-9 SARah-1 launch and reentry sonic boom](https://doi.org/10.1121/2.0001767). POMA 51, 040006.
- Anderson, M. C. & Gee, K. L. (2024). [Sonic boom measurements from the SpaceX Transporter-8 Falcon-9 rocket landing at Vandenberg Space Force Base](https://doi.org/10.2514/6.2024-3188). AIAA Aeroacoustics 2024.
- Gee, K. L. et al. (2024). [Starship Super Heavy acoustics: far-field noise measurements during launch and the first-ever booster catch](https://doi.org/10.1121/10.0034453). JASA Express Letters 4(11).
- BYU Physics, [publication abstract on Falcon 9 landing booms](https://physics.byu.edu/docs/publication/12033).
- [Launch Library 2 API](https://ll.thespacedevs.com/2.3.0/launches/previous/); McDowell, [GCAT launch list](https://planet4589.org/space/gcat/tsv/launch/launch.tsv); NASA/JPL, [CNEOS fireballs](https://cneos.jpl.nasa.gov/fireballs/) and [fireball API](https://ssd-api.jpl.nasa.gov/fireball.api).

### Gunshots and multi-recording analysis

- Maher, R. C. (2022). [Interpretation of audio forensic information from the shooting of journalist Shireen Abu Akleh](https://www.montana.edu/rmaher/publications/maher_aes_1022_22.pdf). AES 153rd Convention, Express Paper 22.
- Maher, R. C. (2024). [The Trump Rally Shooting: Listening to an Assassination Attempt](https://www.montana.edu/rmaher/publications/maher_asa_1124.pdf). ASA 187th meeting, lay-language paper.
- Forensic Architecture, [Audio and acoustic analysis: gunfire before, during and after the killing of Shireen Abu Akleh](https://content.forensic-architecture.org/wp-content/uploads/2022/11/2022.11.02_Final-Audio-and-Acoustic-Analysis-SAA-Report.pdf) (2022), and the [full report with Al-Haq](https://content.forensic-architecture.org/wp-content/uploads/2022/11/Shireen-Abu-Akleh_FA-and-Al-Haq-Full-Report.pdf).
- Bellingcat, [Unravelling the killing of Shireen Abu Akleh](https://www.bellingcat.com/news/middle-east/2022/05/14/unravelling-the-killing-of-shireen-abu-akleh/) (2022).
- Bellingcat, [What audio analysis reveals about aid workers killed in Gaza](https://www.bellingcat.com/news/2025/04/09/gaza-palestine-israel-killed-death-aid-workers-international-red-cross-crescent-audio-analysis/) (2025).
- GIJN, [How two forensic teams investigated the killing of journalist Shireen Abu Akleh](https://gijn.org/2022/08/29/how-two-forensic-teams-investigated-the-killing-of-journalist-shireen-abu-akleh/) (2022).
- CBS News, [Trump rally gunman fired 8 shots in under 6 seconds](https://www.cbsnews.com/news/trump-rally-gunman-fired-eight-shots-under-six-seconds-before-being-killed-by-secret-service-analysis-shows/) (2024).
- The New Arab, [Israeli tanks likely killed Hind Rajab](https://www.newarab.com/news/israeli-tanks-killed-hind-rajab-ambulance-crew-gaza-report) (2024; reports the Forensic Architecture audio finding).
- Earshot, [How sounds help solve crimes](https://earshotngo.substack.com/p/how-sounds-help-solve-crimes) (2026).
- Liang, J., Aronson, J. D. & Hauptmann, A. (2019). [Shooter localization using social media videos](https://doi.org/10.1145/3343031.3350536). ACM Multimedia 2019; [VERA technical report](https://arxiv.org/abs/1905.13313); [code](https://github.com/JunweiLiang/VERA_Shooter_Localization).
- Beck, S. D., Nakasone, H. & Marr, K. W. (2011). [Variations in recorded acoustic gunshot waveforms generated by small firearms](https://doi.org/10.1121/1.3557045). JASA 129(4), 1748–1759.

### Sirens, bells, prayer times

- Netherlands: DutchNews, [sirens to fall silent from 2028](https://www.dutchnews.nl/2026/05/air-raid-sirens-to-fall-silent-from-2028-as-cabinet-cuts-funding/) and [government U-turn](https://www.dutchnews.nl/2026/06/government-u-turns-on-decision-not-to-replace-air-raid-sirens/) (2026).
- Germany: Bundesregierung, [Bundesweiter Warntag](https://www.bundesregierung.de/breg-de/service/fragen-und-anworten/bundesweiter-warntag-2383690).
- Switzerland: Kanton Zürich, [Sirenentest am Mittwoch, 4. Februar 2026](https://www.zh.ch/de/news-uebersicht/medienmitteilungen/2026/01/sirenentest-am-mittwoch-4-februar-2026.html).
- Austria: Tiroler Tageszeitung, [what the siren signals mean](https://www.tt.com/artikel/16125734/gut-zu-wissen-das-bedeuten-die-unterschiedlichen-sirenensignale); Land Niederösterreich, [Zivilschutz-Probealarm](https://www.noe.gv.at/noe/Zivilschutz-Probealarm__Sirenen_heulen_am_Samstag_im_ganz.html).
- France: AIDA/INERIS, [circular on the order of 23 March 2007 (signal national d'alerte)](https://aida.ineris.fr/reglementation/circulaire-ndeg-inte0700066c-050607-relative-a-lapplication-larrete-23-mars-2007).
- Czech Republic: HZS ČR, [siren test notice](https://hzscr.gov.cz/soubor/tz-zruseni-zkousky-siren-brezen-2015-doc.aspx).
- Finland: Verkkouutiset, [Väestöhälyttimiä testataan huomenna](https://www.verkkouutiset.fi/a/vaestohalyttimia-testataan-huomenna/).
- Sweden: MSB, [Test av Hesa Fredrik](https://msb.mynewsdesk.com/pressreleases/test-av-hesa-fredrik-paa-maandag-7-mars-klockan-15-punkt-00-3165238).
- OpenStreetMap taginfo: [emergency=siren](https://taginfo.openstreetmap.org/tags/emergency=siren), [religion=muslim](https://taginfo.openstreetmap.org/tags/religion=muslim).
- Central Council of Church Bell Ringers, [Dove's Guide downloads](https://dove.cccbr.org.uk/downloads) (CC BY-SA 4.0).
- [Aladhan prayer times API](https://aladhan.com/prayer-times-api); [PrayTimes.org](https://praytimes.org).

### Broadcast and alerts

- Wang, A. (2003). [An industrial-strength audio search algorithm](https://www.princeton.edu/~cuff/ele201/files/Wang03-shazam.pdf). ISMIR 2003, 7–13.
- Six, J. & Leman, M. (2014). [Panako: a scalable acoustic fingerprinting system handling time-scale and pitch modification](https://archives.ismir.net/ismir2014/paper/000122.pdf). ISMIR 2014.
- [Chromaprint](https://github.com/acoustid/chromaprint) (AcoustID).
- [47 CFR 11.31](https://www.ecfr.gov/current/title-47/section-11.31), EAS protocol; [47 CFR 11.61](https://www.ecfr.gov/current/title-47/section-11.61), tests.
- FEMA, [OpenFEMA IPAWS archived alerts API](https://www.fema.gov/api/open/v1/IpawsArchivedAlerts); Iowa Environmental Mesonet, [NWS warnings archive](https://mesonet.agron.iastate.edu/vtec/); [multimon-ng](https://github.com/EliasOenal/multimon-ng).
- Japan Meteorological Agency, [earthquake early warning issuance history](https://www.data.jma.go.jp/svd/eew/data/nc/rireki/rireki.html).
- Radford, A. et al. (2022). [Robust speech recognition via large-scale weak supervision](https://arxiv.org/abs/2212.04356) (Whisper).
- Valk, J. & Alumäe, T. (2021). [VoxLingua107: a dataset for spoken language recognition](https://doi.org/10.1109/slt48900.2021.9383459). IEEE SLT 2021.

### Mains electricity (ENF)

- Grigoras, C. (2005). [Digital audio recording analysis: the electric network frequency (ENF) criterion](https://doi.org/10.1558/sll.2005.12.1.63). Int. J. Speech, Language and the Law 12(1), 63–76.
- Grigoras, C. (2007). [Applications of ENF criterion in forensic audio, video, computer and telecommunication analysis](https://doi.org/10.1016/j.forsciint.2006.06.033). Forensic Science International 167, 136–145.
- Huijbregtse, M. & Geradts, Z. (2009). [Using the ENF criterion for determining the time of recording of short digital audio recordings](https://doi.org/10.1007/978-3-642-03521-0_11). IWCF 2009, LNCS.
- Nicolalde Rodríguez, D. P., Apolinário, J. A. & Biscainho, L. W. P. (2010). [Audio authenticity: detecting ENF discontinuity with high precision phase analysis](https://doi.org/10.1109/tifs.2010.2051270). IEEE TIFS 5(3), 534–543.
- Garg, R., Varna, A. L., Hajj-Ahmad, A. & Wu, M. (2013). ["Seeing" ENF: power-signature-based timestamp for digital multimedia via optical sensing and signal processing](https://doi.org/10.1109/tifs.2013.2272217). IEEE TIFS 8(9), 1417–1432.
- Garg, R., Hajj-Ahmad, A. & Wu, M. (2013). [Geo-location estimation from electrical network frequency signals](https://doi.org/10.1109/icassp.2013.6638180). ICASSP 2013.
- Su, H., Hajj-Ahmad, A., Garg, R. & Wu, M. (2014). [Exploiting rolling shutter for ENF signal extraction from video](https://doi.org/10.1109/icip.2014.7026086). ICIP 2014.
- Hajj-Ahmad, A., Garg, R. & Wu, M. (2015). [ENF-based region-of-recording identification for media signals](https://doi.org/10.1109/tifs.2015.2398367). IEEE TIFS 10(6), 1125–1136.
- Hua, G., Bi, G. & Thing, V. L. L. (2017). [On practical issues of electric network frequency based audio forensics](https://doi.org/10.1109/access.2017.2647921). IEEE Access 5.
- Garg, R., Hajj-Ahmad, A. & Wu, M. (2021). [Feasibility study on intra-grid location estimation using power ENF signals](https://arxiv.org/abs/2105.00668). arXiv.
- [On spectrogram analysis in a multiple classifier fusion framework for power grid classification using ENF](https://arxiv.org/abs/2403.18402) (2024; describes the SP Cup 2016 nine-grid dataset).
- Wikipedia, [Baltic states synchronization with CESA](https://en.wikipedia.org/wiki/Baltic_states_synchronization_with_CESA); Wikipedia, [Mains electricity by country](https://en.wikipedia.org/wiki/Mains_electricity_by_country).

### Wildlife

- Kahl, S., Wood, C. M., Eibl, M. & Klinck, H. (2021). [BirdNET: a deep learning solution for avian diversity monitoring](https://doi.org/10.1016/j.ecoinf.2021.101236). Ecological Informatics 61, 101236.
- [BirdNET-Analyzer](https://github.com/birdnet-team/BirdNET-Analyzer) (code MIT, models CC BY-NC-SA 4.0).
- Chasmai, M., Liu, W., Maji, S. & Van Horn, G. (2025). [Audio geolocation: a natural sounds benchmark](https://arxiv.org/abs/2505.18726). arXiv.
- eBird, [Status and Trends terms of use](https://ebird.org/about/products-access-terms-of-use); [download page](https://science.ebird.org/en/status-and-trends/download-data); [ebirdst R package](https://github.com/ebird/ebirdst).
- Bellingcat, [Online investigations toolkit: Merlin](https://bellingcat.gitbook.io/toolkit/more/all-tools/merlin).

### Other signals

- Oerlemans, S., Sijtsma, P. & Méndez López, B. (2007). [Location and quantification of noise sources on a wind turbine](https://doi.org/10.1016/j.jsv.2006.07.032). Journal of Sound and Vibration 299, 869–883.
- USGS, [US Wind Turbine Database](https://energy.usgs.gov/uswtdb/) and [API](https://energy.usgs.gov/api/uswtdb/v1/turbines).
- Lei, H., Choi, J. & Friedland, G. (2012). [Multimodal city-verification on Flickr videos using acoustic and textual features](https://doi.org/10.1109/icassp.2012.6288367). ICASSP 2012.
- Elizalde, B. et al. (2016). [City-identification of Flickr videos using semantic acoustic features](https://doi.org/10.1109/bigmm.2016.28). IEEE BigMM 2016.
- Choi, J. et al. (2014). [The Placing Task: a large-scale geo-estimation challenge for social-media videos and images](https://doi.org/10.1145/2661118.2661125). ACM GeoMM 2014.
- Mesaros, A., Heittola, T. & Virtanen, T. (2019). [Acoustic scene classification in DCASE 2019 challenge](https://doi.org/10.33682/m5kp-fa97). DCASE 2019.
- Dokmanić, I. et al. (2013). [Acoustic echoes reveal room shape](https://doi.org/10.1073/pnas.1221464110). PNAS 110(30), 12186–12191.
- NOAA NEXRAD Level II on AWS: [unidata-nexrad-level2 bucket](https://unidata-nexrad-level2.s3.amazonaws.com/?list-type=2&prefix=2026/11/01/KTLX/&max-keys=5).
