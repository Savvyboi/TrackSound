# Emergency-alert data bursts (EAS / SAME) as a clock and region anchor

Status: implemented (Alerts source). Code: `backend/src/tracksound/sources/alerts/`,
API: `backend/src/tracksound/api/alerts.py`, contract: [../API.md](../API.md#alerts-source-us-emergency-alert-data-bursts-eassame).
Recommended by the survey ([audio-geolocation-techniques.md](audio-geolocation-techniques.md), section 5.5
and roadmap item 3) as a cheap but decisive clock anchor.

## The idea

US broadcast stations, cable systems and NOAA Weather Radio start every alert, and every weekly
or monthly test, with a digital header sent three times as audio tones. Anyone near a radio or
TV can end up recording it. Decoded, the header gives:

- **the issue time to the minute** (UTC, day of year: `JJJHHMM`): a clock anchor;
- **up to 31 county codes** (FIPS) or marine zones: a region prior;
- **the event and originator** (for example a Tornado Warning from the National Weather Service);
- **the sending station** (for example `KEAX/NWS`, or a station call sign such as `WDAF/FM`).

`implied_start_utc = issued_utc - t`, where `t` is the burst's position in the recording.

## The signal (verified against the primary sources)

From [47 CFR 11.31](https://www.ecfr.gov/current/title-47/section-11.31) (eCFR, current to
2026-11-02) and [NWS Instruction 10-1712](https://www.weather.gov/media/directives/010_pdfs/pd01017012curr.pdf)
(20 April 2022):

| Item | Value | Source |
|---|---|---|
| Modulation | AFSK, phase continuous at bit boundaries | 11.31(a)(1); 10-1712 A.1.2 |
| Bit rate | 520.83 bit/s, 1.92 ms per bit (±1 µs) | 11.31(a)(1); 10-1712 A.1.1.2 |
| Mark (1) / space (0) | 2083.3 Hz (4 cycles per bit) / 1562.5 Hz (3 cycles) | same |
| Bytes | 8 bits, least significant bit first, no start, stop or parity bits | 10-1712 A.1.1.1, A.1.2 |
| Characters | 7-bit ASCII; the eighth bit "either 0 or 1" (NWS sends 0) | 11.31(a)(1); 10-1712 A.1.2 |
| Preamble | 16 bytes of 0xAB before every header and EOM | 11.31(c); 10-1712 A.1.1.1 |
| Header | `ZCZC-ORG-EEE-PSSCCC-...+TTTT-JJJHHMM-LLLLLLLL-`, at most 268 bytes with the preamble | 11.31(c); 10-1712 A.2 |
| Repetition | header 3 times, then EOM (`NNNN`) 3 times, 1 s (±5 %) apart | 11.31(c); 10-1712 A.1.1 |
| Attention signal | EAS: 853 + 960 Hz together, 8-25 s. NWR: 1050 Hz, 8-10 s, 1-3 s after the third header (optional) | 11.31(a)(2), (c); 10-1712 A.1.3 |
| Originators | EAS, CIV, WXR, PEP only | 11.31(d) |
| Events | 57 codes in 11.31(e) (Blue Alert and Missing and Endangered Persons added since 2017), plus NWS-only SQW and the transmitter codes TXB, TXF, TXO, TXP | 11.31(e); 10-1712 A.4 |
| Locations | P = part of the county (0 all, 1-9 compass parts); SS = state FIPS or NWS marine area; CCC = county FIPS, 000 for the whole state | 11.31(c), (f); 10-1712 A.2.8 |
| Purge time | +TTTT in 15-minute steps to 1 h, then 30-minute steps | 11.31(c); 10-1712 A.2.9 |
| Voting | accept 2 identical copies of 3, else bit-by-bit majority | 10-1712 B.3 |

## What is implemented

1. **Decoding audio.** The scan reads the *original upload*, not the 4 kHz copy (its 2 kHz Nyquist
   limit is below the 2083 Hz mark tone). ffmpeg streams it as mono float32 at 11,025 Hz through
   a pipe in 30 s blocks. Each block is padded by 0.5 s before and 6 s after, so a burst (at
   most 4.1 s) is always whole in the block that owns its start. Memory stays at a few MB for
   multi-hour files. The front end runs at about 250× real time; a 3-hour file takes about a minute
   plus ffmpeg decoding.
2. **Soft bits** (`same.py`):
   - band-pass 1.1-2.7 kHz;
   - mix to baseband at the mark and space frequencies (I/Q) and integrate over one bit
     (boxcar; the tones are orthogonal over a bit);
   - at every sample, form `(|mark| - |space|) / local RMS over 16 bits`. This is about ±1 for a
     clean bit and noise of standard deviation ~0.46 elsewhere, at any recording level.
3. **Finding bursts.**
   - Correlate the soft signal with a zero-mean template of 6 preamble bytes, at bit rates of
     0.99, 1.00 and 1.01 times nominal. Steady music tones cancel in a zero-mean template.
   - For each candidate, a grid search over the sub-bit timing offset and the bit period (±0.6 %)
     maximises the decision margins `Σ|soft|`.
   - The payload marker (end of preamble + `ZCZC` or `NNNN`) is then found by soft correlation.
     It must agree both in absolute terms (≥ 0.35, which rejects silence and noise) and relative
     to the soft magnitudes (≥ 0.7, which rejects a window that lies inside a long preamble).
   - Headers get a finer period fit over the first ~40 bytes, then a phase-tracking loop over
     the rest (per 16 bits, gain 0.25), which tolerates clock drift. Tested with a bit clock
     0.39 % slow (a software encoder rounding to 85 samples per bit at 44.1 kHz) and with 1 %
     fast playback.
4. **Decoding the text** (`header.py`):
   - **Combining the three bursts.** Their soft bits are averaged, weighted by each burst's SNR
     (A/σ² measured on 64 known preamble and marker bits). Each burst is also decoded alone, and
     the alone-decodes are voted per character (the B.3 rule). Every candidate text (combined,
     voted, single) is scored against the averaged soft bits; the best well-formed one wins.
   - **Grammar-constrained maximum likelihood.** Each character is the allowed character whose 7
     bits best match the soft bits; bit 8 is ignored.
     - Closed sets are decoded as whole strings: originator, day 001-366, hour 00-23, minute 00-59.
     - Event codes, location codes (about 4,100 known), purge minutes (00/15/30/45) and station
       characters (A-Z, 0-9, `/`, space) snap to the usual value only when the bits to flip are
       weak (worth less than 0.6 of a typical bit). A confidently decoded unusual value is kept.
     - Whether a location code is followed by another (`-`) or by the purge time (`+`) is
       decided with an eight-character look-ahead.
   - **Confidence.** This is the posterior probability that every character is right. It uses a
     Gaussian model of the soft bits, `log P ∝ κ · agreement`, with `κ = A/σ²` estimated from
     the decoded bits and tempered by 0.75. It is lowered for a lone burst with no attention
     signal (×0.85), an unknown event (×0.8) or an unmapped location (×0.9). Headers below 0.2 are
     only mentioned in the notes.
5. **EOM** groups (up to 3 `NNNN` bursts within 2.5 s) are reported by the time of the first
   burst. They have no grammar to check, so an EOM needs a stronger marker (≥ 0.45).
6. **Attention signals** (`attention.py`) are supporting evidence. In ~0.11 s frames, the tone
   power is compared with the median of 8 neighbouring frequencies (±30-75 Hz). A tone needs
   ≥ 13 dB prominence for ≥ 4 s.
   - EAS two-tone (both tones) is reported after a header, or alone if ≥ 6 s.
   - A lone 1050 Hz tone is common in music (C6 is 1046.5 Hz), so it only counts right after a
     header.
7. **Locations** (`codes.py`, `data/`): see [data/README.md](../../backend/src/tracksound/sources/alerts/data/README.md).
   - Current counties come from the [2026 Census Gazetteer](https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2026_Gazetteer/2026_Gaz_counties_national.zip)
     (internal points, rounded to 0.001°).
   - Island areas come from TIGER/Line 2025.
   - Retired codes that NWS still uses for SAME come from older Gazetteers. For example,
     Connecticut's 8 historical counties are in the [NWS SAME code list](https://www.weather.gov/source/nwr/SameCode.txt),
     although the Census replaced them with planning regions in 2022. Both sets are included.
   - State-wide codes map to a land-area-weighted centroid.
   - NWS marine zones (`ANZ530` and so on) use NWS centroids.
   - Everything is public domain.
8. **Issue year.** JJJHHMM has no year. It comes from:
   - the case date (the closest year, so day 365 with a date of 2 January lands in the previous
     year);
   - else the file's creation-time metadata (ffprobe tags);
   - else the most recent matching day before the scan.

   The notes say which was used. The year is worked out when the state is read, so setting the
   case date later needs no new scan.
9. **IPAWS cross-check** (`ipaws.py`, on by default, `{"crosscheck": false}` turns it off).
   - It queries the [OpenFEMA IPAWS archive](https://www.fema.gov/api/open/v1/IpawsArchivedAlerts)
     for alerts sent within ±15 min of the issue time. Only the time window is sent; event and
     county codes are matched locally.
   - Verified live on 2026-11-04: the query takes ~0.5 s. It found the NWS Melbourne Flash Flood
     Warning for Brevard County, FL (SAME 012009) sent 2026-11-02 12:27 UTC.
   - The archive holds alerts that went through IPAWS: NWS warnings sent to EAS and WEA, state
     and local alerts, and national tests. Weekly and monthly tests generated by stations or NOAA
     Weather Radio are not in it, so "no match" is normal for RWT and RMT.

## Accuracy (synthetic tests)

SNR is the burst power over white-noise power in a 3 kHz band. "Exact" means every character
of the header is right.

| Condition | Result |
|---|---|
| White noise, +3 dB and above | 10/10 exact |
| White noise, 0 dB | ~90 % exact (8-9 of 10 over several runs; 15/16 in one run) |
| White noise, -2 dB | 30-50 % exact |
| White noise, -4 dB | fails (bursts still detected, text not recovered) |
| Under synthetic music, burst 6 dB below the music RMS | exact (also at -6 dB after every codec below) |
| MP3 128/64 kbps, MP3 32 kbps at 22 kHz, AAC 96/48/32 kbps, Opus 32 kbps, Vorbis q2, Speex and G.726 at 8 kHz | exact at +20, +6 and +3 dB noise and under music |
| Opus 16 kbps | exact down to +6 dB; fails at +3 dB |
| False alarms in 900 s of noise, music and speech | none (best marker score 0.31 against the 0.35 threshold) |
| Confidence (48 trials at -2 to +1 dB) | no wrong header scored ≥ 0.8; wrong ones scored up to 0.64; correct ones 0.84 on average |
| Burst time | about 1 ms in PCM; MP3 and AAC encoder delay adds tens of ms (the tests allow 60 ms) |

The 0 dB figure is close to what ideal noncoherent combining of three bursts allows. Coherent
detection would gain 1-2 dB: both tones complete whole cycles per bit, so the carrier phase is
the same at every bit start. It is not implemented, because it breaks with bit-clock errors and
some codecs.

## Limits

- **Issue time is not broadcast time.**
  - JJJHHMM is when the originator released the message, rounded down to the minute.
  - NOAA Weather Radio usually airs it within a minute or two. Stations relaying an alert can air
    it minutes later. A rebroadcast, a replay or a recording of a recording can be hours or years
    later.
  - `implied_start_utc` is therefore an earliest plausible start. The true start is usually
    0-2 min later.
  - A real anchor needs the station (`LLLLLLLL`) and, ideally, a log or an IPAWS match.
- **Region, not position.**
  - The counties are where the alert applies, not where the listener was.
  - A NOAA Weather Radio transmitter covers about 40 miles. A broadcast station can be heard
    beyond its counties, and a station relays alerts for its whole coverage area.
  - A partial-county code (P = 1-9) still maps to the whole county's centroid.
- **The header must be recorded clearly.** It needs roughly 0 dB SNR in a 3 kHz band, about
  1 s per burst; the attention tone and the voice are not needed. Heavy clipping, a speaker in
  another room or a 16 kbps voice codec can lose it. The voice message itself is not used.
- **Canada is not supported.**
  - Environment Canada's Weatheradio (station `EC/GC/CA`) and Canadian broadcasters use the same
    SAME framing with Canadian location codes (CLC), not US FIPS.
  - Such headers are decoded and flagged in the notes, but their locations are not mapped.
- **IPAWS digital alerts don't always carry audio SAME.** Wireless Emergency Alerts on phones
  and many CAP-only alerts carry no SAME data, so there is nothing to decode from a phone's alert
  sound. Conversely, many audio alerts (station tests, NWR) are not in the IPAWS archive.
- **Year.** Without a case date or trustworthy file metadata, the year is assumed. The notes
  say so.
- **US territories with no Census geometry.** Micronesia, the Marshall Islands, Palau and the
  Minor Outlying Islands get names but no coordinates.

## References

- 47 CFR 11.31, EAS protocol: https://www.ecfr.gov/current/title-47/section-11.31 (text checked via the
  eCFR versioner API, current to 2026-11-02; event-code changes since 2017-01-03 also checked).
- NWS Instruction 10-1712, NOAA Weather Radio All Hazards SAME (20 April 2022):
  https://www.weather.gov/media/directives/010_pdfs/pd01017012curr.pdf
- NWS SAME county code list: https://www.weather.gov/source/nwr/SameCode.txt
- NWS EAS/NWR marine area and zone codes: https://www.weather.gov/gis/EasNWR
- US Census Bureau Gazetteer files (2026, 2021, 2015, 2010) and TIGER/Line 2025 counties:
  https://www2.census.gov/geo/docs/maps-data/data/gazetteer/ and
  https://www2.census.gov/geo/tiger/TIGER2025/COUNTY/
- OpenFEMA IPAWS Archived Alerts API: https://www.fema.gov/api/open/v1/IpawsArchivedAlerts
- Background on noncoherent FSK detection and diversity combining: J. G. Proakis and M. Salehi,
  *Digital Communications*, 5th ed., McGraw-Hill, 2008 (chapters 4 and 13).
