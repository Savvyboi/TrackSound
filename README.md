# TrackSound

A local web app for **acoustic geolocation**: work out where (and when) an audio or video
recording was made from faint sounds in it, matched against public records of events that
happened at known places and times.

It runs entirely on your machine: a small Python server plus a browser UI. Your recordings
never leave your computer; the only network traffic is downloading public reference data
(flight history, lightning, grid frequency, map tiles).

![Aircraft: modelled flyover curves over the spectrogram, and the matching flights on the map](docs/images/aircraft.png)

| Lightning and known events | Mains hum (ENF) |
|---|---|
| ![Thunder onsets matched to catalogued strikes](docs/images/lightning.png) | ![Hum frequency matched to Great Britain grid records](docs/images/mains-hum.png) |

*Screenshots from the built-in demo cases (`tracksound demo`). The mains-hum demo matches
real Great Britain grid data.*

## Install

### Download the app (easiest)

Get the file for your computer from the
[latest release](https://github.com/Savvyboi/TrackSound/releases/latest), unzip it, and
double-click **TrackSound**. Nothing else to install: Python, ffmpeg and the web UI are inside.

| Computer | File | First launch |
|---|---|---|
| Windows (64-bit) | `TrackSound-windows-x64.zip` | Double-click `TrackSound.exe`. If Windows says "Windows protected your PC", click **More info → Run anyway** (the app is not code-signed yet). |
| Mac with Apple Silicon (M1 and later) | `TrackSound-macos-arm64.zip` | **Right-click** `TrackSound` → **Open** → **Open** (the app is not notarised yet). |
| Linux (64-bit) | `TrackSound-linux-x64.tar.gz` | Run `TrackSound/TrackSound`. |

TrackSound opens in your web browser. Keep its window open while you use it; close the window
to stop it.

### Or install with one command

This also adds a desktop shortcut. It works on Intel Macs too, and it updates when you run it
again.

macOS or Linux (Terminal):

```bash
curl -LsSf https://raw.githubusercontent.com/Savvyboi/TrackSound/HEAD/install.sh | sh
```

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/Savvyboi/TrackSound/HEAD/install.ps1 | iex"
```

The installer:

1. Gets [uv](https://docs.astral.sh/uv/), a fast Python installer, if you don't have it.
2. Uses uv to install TrackSound with its own Python and ffmpeg.
3. Adds the shortcut and starts TrackSound.

Everything goes in your home folder; no admin rights are needed. To uninstall, run
`uv tool uninstall tracksound`.

### Or with Python

```bash
uv tool install "tracksound @ https://github.com/Savvyboi/TrackSound/archive/HEAD.zip#subdirectory=backend"
tracksound
```

`tracksound` on its own starts the app and opens the browser. Other commands:

- `tracksound demo` adds demo cases with synthetic recordings, so you can try every source
  without downloads.
- `tracksound shortcut` adds a desktop shortcut.
- `tracksound serve --help` lists the server options.

Data is kept in `~/.tracksound`; change that with `--data DIR` or `TRACKSOUND_DATA`.

## Evidence sources

| Source | What it listens for | Matched against | Gives |
|---|---|---|---|
| **Aircraft** | Propeller tones (60–400 Hz) and helicopter rotor tones (10–60 Hz) sweeping down in pitch as the aircraft passes (Doppler) | Historical ADS-B flight tracks ([adsb.lol](https://github.com/adsblol/globe_history_2026), free) | Location (tens of metres to about 2 km) and the recording's start time |
| **Lightning & known events** | Thunder onsets, blasts and booms, and flashes visible in video | NOAA GOES GLM lightning (free, Americas), USGS-listed quarry blasts, explosions and sonic booms, a CSV, or an event you enter | Location (km level with satellite lightning) and the start time; flash-to-bang range rings |
| **Mains hum (ENF)** | The 50/60 Hz hum's tiny frequency wobble | Power-grid frequency logs | The date and time, and which power grid |
| **Emergency alerts** | US EAS / NOAA Weather Radio data bursts (SAME headers) | FIPS county list; FEMA's IPAWS archive for cross-checks | Affected counties and the issue time, to the minute |

Sources help each other. For example, mains hum or an alert can supply the start time, and
a known start time lets the aircraft search work from a single flyover.

Where these ideas come from:

- The aircraft method comes from colsto's video
  ["I discovered a sound that leaks your exact location"](https://www.youtube.com/watch?v=UytYrhYLOKI)
  and is built on [mjshiggins/dopplertrack](https://github.com/mjshiggins/dopplertrack), an
  open, validated implementation.
- The other sources came out of a survey of audio geolocation techniques, which also ranks
  what to add next:
  [docs/research/audio-geolocation-techniques.md](docs/research/audio-geolocation-techniques.md).

**How well does it work?**

- On real flight-history days with synthetic audio at known points, it lands within about
  40–50 m ([docs/results/validation.md](docs/results/validation.md)).
- Real recordings are harder. Phones cut low frequencies, and jets give no clean tones.
- Other open tools found the tonal method fails on most arbitrary clips. Treat results as
  leads to check, and read the margins and runner-ups.

## Using it

1. **New case:** drop an audio or video file on the home page.
2. **Setup:**
   - Set the recording date (UTC day).
   - Say where it was recorded, by country (for example Portugal: only Portuguese flights,
     lightning and power-grid records are checked), by drawing a region on the map, or both.
   - Add the start time if you know it, and look up the air temperature.
3. **Aircraft:**
   1. Detect flyovers, using the Propeller or Helicopter preset, and accept the clear ones.
   2. Fetch that day's flights. This streams 2–4 GB once, takes a few minutes, and is
      cached.
   3. Run a blind search with two adjacent flyovers, then refine with any extra events.
4. **Lightning & events:**
   1. Detect thunder (and flashes, for video).
   2. Fetch GOES GLM strikes or USGS events, import a CSV, or enter a known event.
   3. Solve.
5. **Mains hum:** extract the ENF trace and match it against a grid-frequency reference to
   get the date and time.
6. **Alerts:** scan for emergency-alert bursts. A decoded header gives counties and an issue
   time.
7. **Summary:** compare what each source says, set the case start time from the best one,
   and export a JSON report.

## Development

You need Python 3.12+ with [uv](https://docs.astral.sh/uv/) and Node.js 22+.

- **Backend:** `cd backend && uv run tracksound serve --reload`. Run the tests with
  `uv run pytest`; slow and network tests are deselected, so run those with `-m slow`.
- **Frontend:** `cd frontend && npm install && npm run dev`. That's the Vite dev server on
  :5173, proxying `/api` to :8000.
  - `npm run build` writes the UI into the Python package (`backend/src/tracksound/web`).
  - Commit the build with your UI changes; CI checks it is up to date.
- **Standalone apps:** `cd backend && uv run --group bundle pyinstaller packaging/tracksound.spec`.
  - To publish a release, push a tag `vX.Y.Z`, or run the Release workflow on GitHub with the
    version (it creates the tag). It builds the Windows, Mac and Linux apps, smoke-tests them
    and publishes them (`.github/workflows/release.yml`).
- **API contract:** [docs/API.md](docs/API.md). Live API docs are at `/api/docs` while the
  server runs.
- **Plan and roadmap:** [docs/PLAN.md](docs/PLAN.md).
- **Validation results:** [docs/results/validation.md](docs/results/validation.md).

## Data and attribution

- ADS-B history © adsb.lol contributors, [ODbL 1.0](http://opendatacommons.org/licenses/odbl/1.0/).
- Aircraft types: ICAO Doc 8643 via the OpenSky Network.
- Terrain: AWS Terrain Tiles.
- Lightning: NOAA GOES-R GLM. Seismic and explosion events: USGS ComCat.
- US counties: US Census Gazetteer (public domain). Emergency-alert cross-checks: FEMA OpenFEMA
  IPAWS archive.
- Country outlines: Natural Earth (public domain).
- ffmpeg: bundled through imageio-ffmpeg (LGPL/GPL builds of FFmpeg).
- Air temperature: Open-Meteo (CC BY 4.0).
- Map data © OpenStreetMap contributors, tiles by OpenFreeMap.
- Grid frequency: see [docs/research/enf.md](docs/research/enf.md).

## Licence

MIT; see [LICENSE](LICENSE). Data sources keep their own licences (above).

## Responsible use

This tool can reveal where a recording was made, including someone's home. Use it for
verification and research, and to check what your own recordings leak. Don't use it to
locate people without their consent. Results are estimates with real uncertainty. Read the
margins and runner-up candidates before trusting a single pin.
