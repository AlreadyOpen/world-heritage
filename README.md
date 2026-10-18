# world-heritage

World Heritage sites from open data only: points from [Wikidata](https://www.wikidata.org/) (CC0) and outlines from
[OpenStreetMap](https://www.openstreetmap.org/copyright) (ODbL), as GeoParquet and vector tiles, one licence per file. Meant
to be published weekly to Hugging Face (`alreadyopen/world-heritage`, dataset and bucket) and mirrored to Kaggle
(`helenkwok/world-heritage`). The files are listed in `hf/README.md`.

    python3 bake.py          # data/out/: Parquet, PMTiles + index.json (needs tippecanoe 2.79)
    python3 publish.py       # gate, then Hugging Face (HF_TOKEN)
    python3 check.py         # data-free checks (CI)

UNESCO's own lists, KML, XLS and ArcGIS services are never used: their terms require written permission to republish.
The outlines are as mapped by OpenStreetMap contributors, not UNESCO's official boundaries. Not affiliated with or endorsed
by UNESCO.

The bake stops if a tile set's deepest zoom holds fewer features than there are rows.

Code: Apache-2.0. Data: CC0 (Wikidata) and ODbL (OpenStreetMap), see `sources.json`.
