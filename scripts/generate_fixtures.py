#!/usr/bin/env python3
"""Generate data/fixtures/locations.json and data/fixtures/route_matrix.json.

The matrix is a deterministic FIXTURE (schematic): haversine distance at an urban speed with a fixed
directional asymmetry so that tests never assume symmetry. It is NOT real road routing.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "data" / "fixtures"

# Public Singapore areas (approximate public coordinates). Synthetic demo geography.
LOCATIONS = [
    ("loc_depot_ubi", "Ubi Depot (service base)", 1.3298, 103.8990, "Ubi"),
    ("loc_tampines", "Tampines Hub", 1.3525, 103.9403, "Tampines"),
    ("loc_bedok", "Bedok Mall", 1.3249, 103.9296, "Bedok"),
    ("loc_paya_lebar", "Paya Lebar Quarter", 1.3176, 103.8928, "Paya Lebar"),
    ("loc_toa_payoh", "Toa Payoh Central", 1.3324, 103.8474, "Toa Payoh"),
    ("loc_bishan", "Bishan Junction 8", 1.3508, 103.8484, "Bishan"),
    ("loc_ang_mo_kio", "Ang Mo Kio Hub", 1.3691, 103.8480, "Ang Mo Kio"),
    ("loc_serangoon", "Serangoon NEX", 1.3505, 103.8722, "Serangoon"),
    ("loc_hougang", "Hougang Mall", 1.3725, 103.8934, "Hougang"),
    ("loc_punggol", "Punggol Waterway Point", 1.4064, 103.9021, "Punggol"),
    ("loc_bugis", "Bugis Junction", 1.2996, 103.8555, "Bugis"),
    ("loc_orchard", "Orchard Road", 1.3040, 103.8318, "Orchard"),
    ("loc_queenstown", "Queenstown MRT", 1.2945, 103.8060, "Queenstown"),
    ("loc_clementi", "Clementi Mall", 1.3150, 103.7644, "Clementi"),
    ("loc_jurong_east", "Jurong East JEM", 1.3334, 103.7430, "Jurong East"),
    ("loc_woodlands", "Woodlands Causeway Point", 1.4360, 103.7861, "Woodlands"),
    ("loc_yishun", "Yishun Northpoint", 1.4294, 103.8355, "Yishun"),
    ("loc_marine_parade", "Parkway Parade", 1.3013, 103.9052, "Marine Parade"),
]


def haversine_km(a, b):
    r = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dphi = p2 - p1
    dl = math.radians(b[1] - a[1])
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    locs = [{"id": i, "name": n, "lat": la, "lon": lo, "area": ar} for i, n, la, lo, ar in LOCATIONS]
    (FIXTURES / "locations.json").write_text(json.dumps({"version": "fixture-v1", "locations": locs}, indent=1), encoding="utf-8")
    minutes: dict[str, dict[str, int]] = {}
    for i, (ida, _, la, loa, _) in enumerate(LOCATIONS):
        row: dict[str, int] = {}
        for j, (idb, _, lb, lob, _) in enumerate(LOCATIONS):
            if ida == idb:
                row[idb] = 0
                continue
            km = haversine_km((la, loa), (lb, lob)) * 1.35
            base = 5 + km / 30.0 * 60.0
            # deterministic asymmetry: eastbound (increasing lon) +6%, westbound -3%; index parity adds 0..2 min
            asym = 1.06 if lob > loa else 0.97
            row[idb] = int(round(base * asym)) + ((i * 7 + j * 3) % 3)
        minutes[ida] = row
    (FIXTURES / "route_matrix.json").write_text(
        json.dumps({"version": "fixture-v1", "unit": "minutes", "schematic": True, "minutes": minutes}, indent=1),
        encoding="utf-8",
    )
    print(f"wrote {len(locs)} locations, {len(locs) ** 2} matrix cells")


if __name__ == "__main__":
    main()
