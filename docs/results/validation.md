# Validation results

Aircraft results come first (real ADS-B days); the other sources follow.

## Aircraft on real ADS-B days

Date: 2026-11-04. These runs go through the HTTP API, exactly as the UI does:

1. Upload the recording.
2. Detect flyovers.
3. Fetch the day's flights.
4. Run a blind search.
5. Optionally refine.

### What was tested, and what wasn't

- **Real flight data.** Each check uses the real adsb.lol archive for a real day, with
  every prop aircraft that was airborne near the area: 1,232 flights for the New York
  day and 386 for the Florida day.
- **Synthetic audio.** The recordings are synthesised. For a listener at a known point
  (dopplertrack's two reference answers), every real prop flight that passed within 9 km
  during the hour gets a Doppler-shifted tone with a second harmonic, amplitude falling
  with distance, plus noise.
- **What this proves:** that TrackSound's pipeline handles real traffic volumes,
  real trajectories (turns, climbs, speed changes) and real coincidences between
  flights.
- **What it does not prove:** how well it works on real microphones. The synthetic tones
  have no microphone high-pass filtering, compression, wind or ground effects. The
  research survey
  ([audio-geolocation-techniques.md](../research/audio-geolocation-techniques.md),
  section 3.1) found open tools succeeding far less often on arbitrary real clips.
- **Next step:** our own field recordings with GPS and an accurate clock (PLAN.md
  section 13).

### Results

| Day and place | Search | Events used | Result | Start-time error | Margin over next pair |
|---|---|---|---|---|---|
| 2026-09-08, Oyster Bay NY (40.876466, −73.524951) | Blind, 26 × 29 km, 200 m grid, 1,315 flight segments, 135 s | R44 helicopter at 4.1 km and King Air 350 at 3.6 km (both detected automatically) | **355 m**, right flights | +2.9 s | 3.6 % |
| same | Refine, 50 m grid, 11 s | plus the PC-12 at 6.1 km, fitted by hand with a box (the detector missed it) | **39 m**, extra flight identified as N22NY | −0.6 s | 1.4 % |
| 2024-12-28, SWFL eagle nest (26.702359, −81.847766) | Blind, 24 × 26 km, 655 segments, 90 s | Two strong events 11 minutes apart, with five other flyovers in between | **11.2 km, wrong pair** | −295 s | 12.7 % |
| same | same, "silence" filter off | same two events | **51 m**, right flights | +1.5 s | 18.8 % |
| same | same, filter on | two *adjacent* strong events (no flyover between them) | **51 m**, right flights | −0.5 s | 36.5 % |

### What we changed because of these runs

- **Automatic silence filter.** dopplertrack's "silence" filter drops candidate points
  that would have heard another close prop between the two events. In a busy sky the true
  point always fails it: the Florida case is near a flight school, with 27 audible aircraft
  in an hour. The backend now switches the filter off when the recording itself shows other
  flyovers between the chosen events, and says so in the search notes. The UI suggests
  adjacent strong events by default.
- **Event quality labels.** The detector also returns fits to pure noise, which score
  about 1 dB, while real flyovers scored 3.5–13. Events now carry a `quality` label
  (strong / possible / weak).
- **Parallel archive parsing.** The first day took about 17 minutes to download and
  filter, because it parsed at 8 MB/s on one core. With parsing spread across processes,
  the Florida day took 105 s at 25 MB/s, close to the network speed.
- **Search cost estimate.** It was about 4× optimistic on this busy 4-core machine, and has
  been made more conservative.

## Other sources

Measured on 2026-11-04. Method details are in each source's research note.

### Lightning and known-time events

Detail: [research/lightning.md](../research/lightning.md).

- **Synthetic storms, 20 per setting, with the clock unknown:**

  | Catalogue position error | Median position error | 90th percentile | Clock error (median) |
  |---|---|---|---|
  | 300 m (ground-network quality) | about 140 m | 240 m | 0.2 s |
  | 4 km (GOES GLM satellite) | 2.7 km | 6–7 km | 3–4 s |

  With a 4 km catalogue, knowing every thunder-to-strike match in advance would give
  about 2.0 km, so the solver is close to what that catalogue allows.
- **Real GOES GLM data:**
  - G16, 2024-07-15 near Jacksonville: 2,060 flashes in 30 minutes.
  - G19, 2025-07-19 in Florida: 1,909 flashes.
  - In storms that active, each thunder onset fits 10–17 catalogued flashes almost
    anywhere. Thunder timing alone cannot place a listener against satellite positions.
    The solver now reports that ambiguity instead of a confident wrong answer.
  - Video flashes, which fix the clock, or a ground-network catalogue make the problem
    solvable.
- **One reported explosion plus a flash in video:**
  - Start time within 0.02 s.
  - Flash-to-bang range 5,993 m against 6,000 m.

### Mains hum (ENF)

Detail: [research/enf.md](../research/enf.md).

- **Demo recording:** 20 minutes of synthetic hum following real Great Britain grid
  frequency (NESO 1 s data, 2026-09-14), with the recorder clock 12 ppm fast.
  - Matched against the live NESO data for the whole day (86,400 candidate start times).
  - Start time −0.33 s from the truth.
  - Correlation 0.79, z 15.7 ("stands out clearly").
- **30-minute recordings against 3 days of real GB data:** 4 of 4 within 1 s (z 11–12).
- **Controls whose hum isn't in the reference:** all "not significant" (z ≤ 4.4).
- **Short recordings:** under about 10 minutes they are often ambiguous over a window of
  several days.

### Emergency alerts (EAS/SAME)

Detail: [research/alerts.md](../research/alerts.md).

- **Header decoding:**
  - Exact at +3 dB SNR and above.
  - About 90 % exact at 0 dB.
  - Exact under music with the burst 6 dB below the music.
- **Codecs:** survives MP3 (32–128 kbps), AAC (32–96 kbps), Opus and Vorbis round trips.
  Opus at 16 kbps fails.
- **False alerts:** none in 900 s of noise, music and speech.
- **Demo case:** a weekly test alert in music, 150 s in. It decoded to Jackson County, MO,
  with the implied start exactly at the true 17:37:30 UTC.

## Reproducing

The scripts are not part of the repository; they drive a running server through the API.

1. Run `tracksound serve`.
2. Create a case with the day and region, and fetch flights.
3. Synthesise audio at the true point from the cached traces: see
   `tracksound.sources.aircraft.synth.synth_audio` for the tone model.
4. Upload it, detect, and search.

`backend/tests/test_aircraft_api.py` runs the same flow on a fully synthetic world in
about two minutes, as part of the normal test suite.
