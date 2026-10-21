---
pretty_name: World heritage sites (Wikidata and OpenStreetMap)
license: other
license_name: cc0-1.0-and-odbl-1.0-per-file
license_link: https://opendatacommons.org/licenses/odbl/1-0/
tags:
  - geospatial
  - heritage
  - world-heritage
---

# World heritage

World Heritage sites from open data only, rebuilt weekly by Milleleaf
([milleleaf/world-heritage](https://github.com/milleleaf/world-heritage)). Two sources under two licences, kept in
separate files and never merged.

| File | Licence | Holds |
|---|---|---|
| `sites.parquet` | CC0 1.0 (Wikidata contributors) | GeoParquet: one row per Wikidata item with a World Heritage Site ID (P757), component parts included (IDs such as `600-001`). Fields: `id` (QID), `whc` (IDs, `;`-separated), `component`, `name_en`, `name_fr`, `countries` (ISO 3166 alpha-3, `;`-separated), `inscribed` (year), `criteria`, `wikidata`, `coordinates` (how many P625 values; the first is the geometry). Items without a coordinate are kept with an empty geometry |
| `sites.pmtiles` | CC0 1.0 (Wikidata contributors) | Vector tiles, layer `sites`, zooms 0 to 10, every site that has a coordinate |
| `areas.parquet` | ODbL 1.0 (© OpenStreetMap contributors) | GeoParquet: every OpenStreetMap way and relation tagged `heritage:operator=whc`, with `id` (`way/…` or `relation/…`), `osm` (link) and the tags `name`, `name:en`, `name:fr`, `ref:whc`, `whc:criteria`, `whc:inscription_date`, `protect_class`, `protection_title`, `wikidata`, `wikipedia`, `boundary`, `heritage`, `type`, `website` (`:` written `_`). Polygons where the members close into rings (outer minus inner), otherwise lines |
| `areas.pmtiles` | ODbL 1.0 (© OpenStreetMap contributors) | Vector tiles, layer `areas`, zooms 0 to 14, every outline |

The outlines are **as mapped by OpenStreetMap contributors, not UNESCO's official boundaries**. Routes and route
networks (OSM `type=route`, `superroute`, `network`) are kept only when they carry a World Heritage ID (`ref:whc`): OSM tags
whole networks, such as the Way of St James across France, where UNESCO lists only sections. Tiles stop at 85.05 degrees
latitude; true coordinates are in the Parquet files. `index.json` holds the counts; `manifest.json` every file's sha256.

## Sources and terms

- Points: [Wikidata](https://www.wikidata.org/) contributors, [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).
- Outlines: © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
  `areas.*` is a derivative database under the ODbL: share-alike applies to it.

Published by Milleleaf. Not affiliated with or endorsed by UNESCO or the World Heritage Centre; no UNESCO data is used.
Provided as is, with no warranty.
