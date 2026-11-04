# TrackSound web UI

The browser interface for TrackSound: a React + TypeScript app built with Vite. It talks to
the local Python server (`tracksound serve`) over the JSON API in [`docs/API.md`](../docs/API.md).

## Develop

Needs Node 22+ and npm 10+.

```sh
cd frontend
npm install

# in another terminal: the API server (default port 8000)
cd backend && uv run tracksound serve --reload

npm run dev          # http://localhost:5173, proxies /api to http://127.0.0.1:8000
```

To point the dev server at a different backend, set `VITE_API_TARGET`:

```sh
VITE_API_TARGET=http://127.0.0.1:8100 npm run dev
```

`uv run tracksound demo` (in `backend/`) creates demo cases with synthetic recordings, which
is the quickest way to see every part of the UI with data.

## Build

```sh
npm run typecheck    # tsc --noEmit (strict)
npm run build        # typecheck, then bundle into frontend/dist
```

`tracksound serve` serves `frontend/dist` at `/` (with a fallback to `index.html` for client
routes), so after a build the whole app runs from one process at http://127.0.0.1:8000/.
All API calls use absolute `/api/...` paths.

## Layout

```
src/
  api/          types (mirror docs/API.md), fetch client, React Query hooks, job tracking
  components/   UI primitives (buttons, form controls, popovers, dialogs, toasts)
  lib/          formatting, time zones, geo helpers, colour maps, Doppler physics
  state/        workspace store: shared time viewport, selections, tools, display prefs
  features/
    home/       case list, drag-and-drop upload with progress
    workspace/  page layout, header (case clock, jobs), resizable panes, shortcuts
    timeline/   spectrogram / thunder envelope / ENF trace canvas, playback
    map/        MapLibre map, basemaps, overlay layers, region drawing
    setup/ aircraft/ lightning/ enf/ alerts/ summary/   sidebar tabs
```

### Notes

- **Spectrogram:** tiles from `/api/cases/{id}/spectrogram/tile` are decoded into an LRU
  cache. Each frame picks the pyramid level with about one column per pixel, max-pools tile
  cells into screen pixels (so faint lines survive zooming out), then maps bytes through a
  256-entry colour LUT. Contrast is automatic by default (floor just above the visible noise)
  and can be set by hand under the sliders button.
- **Shared time axis:** the spectrogram, thunder envelope and ENF trace share one viewport
  (`state/workspace.ts`), so switching tabs keeps your place.
- **Jobs:** long operations return a Job. `api/jobs.tsx` polls `/api/jobs`, shows toasts when
  jobs finish or fail, and refetches the data each job changed.
- **Missing endpoints:** if the server answers 404/405/501 for a source's endpoint, that part
  of the UI shows "Not available yet" instead of failing.
- **Flyover curves** are drawn as two "rails" either side of the fitted Doppler curve, so the
  sweep itself stays visible between them. Fits that score like pure noise are hidden until
  you ask for them.
- **Heat maps** (aircraft search, lightning solve) are rasterized in the browser by
  nearest-grid-point lookup, so multi-resolution grids render as one clean image layer.
- **Basemaps:** OpenFreeMap vector styles (no key) and Esri World Imagery. Without internet
  the map falls back to a plain background (with country outlines from `/api/enf/grid-map`
  when available); everything else keeps working. Aircraft icons are drawn on a canvas, so
  no sprite server is needed.

### Keyboard shortcuts (workspace)

Space play/pause · wheel zoom time · Shift+wheel zoom frequency · drag pan · `+` `-` `0`
zoom · ← → pan · `B` box tool (fit a flyover) · `A`/`R` accept/reject the selected flyover ·
`C` show/hide fitted curves · `R` draw region · `1`–`6` tabs · `?` all shortcuts · Esc cancel.
