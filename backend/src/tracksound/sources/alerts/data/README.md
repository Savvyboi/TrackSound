# SAME location tables

Lookup tables for the `PSSCCC` location codes in EAS / NOAA Weather Radio SAME headers.
Both files are derived from US federal government works, which are in the **public domain**
(17 U.S.C. § 105). `build_tables.py` regenerates them (it downloads about 1.5 MB).

## us_counties.csv

`fips` (SS + CCC), `state` (USPS abbreviation), `name`, `lat`, `lon` (WGS-84, rounded to
0.001°), `src`. About 3,370 rows.

| `src` | Rows | What it is |
|---|---|---|
| (empty) | 3,222 | Current county or equivalent. Name and internal point from the [US Census Bureau 2026 Gazetteer counties file](https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2026_Gazetteer/2026_Gaz_counties_national.zip) (50 states, DC, Puerto Rico). |
| `tiger` | 13 | American Samoa, Guam, Northern Mariana Islands, US Virgin Islands, from the attribute table of [TIGER/Line 2025 counties](https://www2.census.gov/geo/tiger/TIGER2025/COUNTY/tl_2025_us_county.zip). |
| `legacy` | 12 | Retired codes that NWS still lists for SAME (for example Connecticut's 8 historical counties, used instead of its planning regions). Point from the [2021](https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2021_Gazetteer/2021_Gaz_counties_national.zip), [2015](https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2015_Gazetteer/2015_Gaz_counties_national.zip) or [2010](https://www2.census.gov/geo/docs/maps-data/data/gazetteer/Gaz_counties_national.zip) Gazetteer. |
| `approx` | 4 | Retired codes with no Gazetteer entry (Alaska 02201, 02232, 02280; Virginia 51560). Land-area-weighted mean of the areas that replaced them. |
| `nws` | 62 | Name only, from [NWS SameCode.txt](https://www.weather.gov/source/nwr/SameCode.txt): Micronesia, Marshall Islands, Palau and the Minor Outlying Islands. No coordinates. |
| `state` | 60 | State-wide code (CCC = 000). Land-area-weighted mean of the state's county internal points. State names from the [Census state codes](https://www2.census.gov/geo/docs/reference/state.txt). |
| `national` | 1 | `00000`: all of the United States (no coordinates). |

The internal point is the Census Bureau's "INTPTLAT/INTPTLONG": a point inside the county
near its centre, not the centre of population.

## marine_zones.csv

`code` (SS + CCC), `area` (NWS 2-letter marine area; the zone ID is `area + "Z" + CCC`,
for example `73530` is ANZ530), `name`, `lat`, `lon` (zone centroid). About 770 rows: the
NWS [Coastal & Offshore Marine Area & Zone Codes for NWR and EAS](https://www.weather.gov/gis/EasNWR)
(`marnwr18mr25.txt`, `mareas18mr25.txt`), plus one row per marine area (CCC = 000, names from
`marst14fe25.txt`, no coordinates). Marine synopsis codes have no coordinates either.
