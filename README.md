# TrackSound

A web tool for **acoustic geolocation**: work out where (and when) an audio or
video recording was made from faint sounds in it, matched against public
records of events that happened at known places and times.

The first source is **propeller aircraft**. Their blade-pass tones (roughly
60–400 Hz, usually inaudible) sweep down in pitch as they fly past, and the
exact shape of that Doppler curve depends on where the listener was. Matching
the curves in a recording against historical ADS-B flight tracks can place the
recording to within tens of metres to a few kilometres. **Lightning** (thunder
delays against lightning-detection networks) is planned next, on the same core.

Inspired by colsto's video
["I discovered a sound that leaks your exact location"](https://www.youtube.com/watch?v=UytYrhYLOKI)
and built on [mjshiggins/dopplertrack](https://github.com/mjshiggins/dopplertrack),
an open, validated Python implementation of the method.

## Status

Planning. Nothing is built yet.

- [docs/PLAN.md](docs/PLAN.md): scope, architecture, data sources, roadmap, risks, open decisions
- [docs/research/lightning.md](docs/research/lightning.md): notes for the future lightning source
