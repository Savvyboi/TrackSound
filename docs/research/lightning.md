# Lightning as a geolocation source: research notes

Status: idea stage, planned for milestone M5 (see [../PLAN.md](../PLAN.md#12-roadmap)).

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
