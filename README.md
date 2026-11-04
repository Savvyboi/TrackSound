# TrackSound

A local web app for **acoustic geolocation**: work out where (and when) an audio or video
recording was made from faint sounds in it, matched against public records of events that
happened at known places and times.

It runs entirely on your machine: a small Python server plus a browser UI. Your recordings
never leave your computer; the only network traffic is downloading public reference data
(flight history, lightning, grid frequency, map tiles).

## Evidence sources

| Source | What it listens for | Matched against | Gives |
|---|---|---|---|
| **Aircraft** | Propeller tones (60–400 Hz) sweeping down in pitch as a plane flies past (Doppler) | Historical ADS-B flight tracks ([adsb.lol](https://github.com/adsblol/globe_history_2026), free) | Location (tens of metres to ~2 km) and the recording's start time |
| **Lightning** | Thunder onsets, and flashes visible in video | Lightning catalogues: NOAA GOES GLM (free, Americas) or any CSV | Location (km level with satellite data) and the start time |
| **Mains hum (ENF)** | The 50/60 Hz hum's tiny frequency wobble | Power-grid frequency logs | The date and time, and which power grid (region) |

The aircraft method comes from colsto's video
["I discovered a sound that leaks your exact location"](https://www.youtube.com/watch?v=UytYrhYLOKI)
and is built on [mjshiggins/dopplertrack](https://github.com/mjshiggins/dopplertrack),
an open, validated implementation. A survey of other audio geolocation techniques, and which
ones TrackSound could add next, is in
[docs/research/audio-geolocation-techniques.md](docs/research/audio-geolocation-techniques.md).

## Quick start

You need Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node.js 20+, and ffmpeg.

1. Build the web UI (once, and after UI changes):

   ```bash
   cd frontend && npm install && npm run build
   ```

2. Optionally, create demo cases with synthetic recordings that need no downloads:

   ```bash
   cd backend && uv run tracksound demo
   ```

3. Start the app:

   ```bash
   cd backend && uv run tracksound serve --open
   ```

The app opens at http://127.0.0.1:8000. Data is kept in `~/.tracksound`; change that with
`--data DIR` or `TRACKSOUND_DATA`.

## Using it

1. **New case:** drop an audio or video file on the home page.
2. **Setup:** set the recording date (UTC day) and draw a search region on the map. Add the
   start time if you know it.
3. **Aircraft:**
   1. Detect flyovers and accept the clear ones.
   2. Fetch that day's flights. This streams about 4 GB once, then it's cached.
   3. Run a blind search with two events, then refine with any extra events.
4. **Lightning:** detect thunder (and flashes, for video), fetch GOES GLM strikes or import a
   CSV, then solve.
5. **Mains hum:** extract the ENF trace, then match it against a grid-frequency reference
   to get the date and time.
6. **Summary:** compare what each source says, and export a JSON report.

## Development

- Backend: `cd backend && uv run pytest`. Slow and network tests are deselected; run them
  with `-m slow`.
- Frontend: `cd frontend && npm run dev`. That's the Vite dev server on :5173, proxying
  `/api` to the backend on :8000.
- API contract: [docs/API.md](docs/API.md). Live API docs are at `/api/docs` while the
  server runs.
- Plan and roadmap: [docs/PLAN.md](docs/PLAN.md).

## Data and attribution

- ADS-B history © adsb.lol contributors, [ODbL 1.0](http://opendatacommons.org/licenses/odbl/1.0/).
- Aircraft types: ICAO Doc 8643 via the OpenSky Network.
- Terrain: AWS Terrain Tiles.
- Lightning: NOAA GOES-R GLM.
- Map data © OpenStreetMap contributors, tiles by OpenFreeMap.
- Grid frequency: see [docs/research/enf.md](docs/research/enf.md).

## Responsible use

This tool can reveal where a recording was made, including someone's home. Use it for
verification and research, and to check what your own recordings leak. Don't use it to
locate people without their consent. Results are estimates with real uncertainty. Read the
margins and runner-up candidates before trusting a single pin.
