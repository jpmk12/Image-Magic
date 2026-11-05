"""Regenerate src/image_magic/data/{cities.tsv.gz,countries.json}.

Sources (both on PyPI):
  reverse_geocoder  -> rg_cities1000.csv  (GeoNames cities with population > 1000)
  pycountry         -> iso3166-1.json     (country names)

GeoNames data is licensed CC BY 4.0 (https://www.geonames.org/).

Usage:
  pip download reverse_geocoder pycountry --no-deps -d /tmp/geo
  (extract both archives)
  python tools/build_places_data.py path/to/rg_cities1000.csv path/to/iso3166-1.json
"""

import csv
import gzip
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "src" / "image_magic" / "data"


def main(cities_csv: str, iso_json: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = 0
    with open(cities_csv, newline="", encoding="utf-8") as f, \
            gzip.open(OUT / "cities.tsv.gz", "wt", encoding="utf-8", compresslevel=9) as out:
        out.write("# GeoNames cities1000 (CC BY 4.0, geonames.org): lat lon name region country\n")
        for r in csv.DictReader(f):
            fields = [f"{float(r['lat']):.4f}", f"{float(r['lon']):.4f}",
                      r["name"], r["admin1"], r["cc"]]
            out.write("\t".join(x.replace("\t", " ") for x in fields) + "\n")
            rows += 1
    data = json.loads(Path(iso_json).read_text(encoding="utf-8"))["3166-1"]
    countries = {c["alpha_2"]: c.get("common_name") or c["name"] for c in data}
    (OUT / "countries.json").write_text(json.dumps(countries, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(f"{rows} places, {len(countries)} countries -> {OUT}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
