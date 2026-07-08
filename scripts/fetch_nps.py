"""NPS boundary mask — Phase-1 DOI-Lands feed for PROMETHEUS.

Source (keyless, verified live 2026-07-08):
  https://services1.arcgis.com/fBc8EJBxQRMcHlei/ArcGIS/rest/services/
  National_Park_Service_Boundaries/FeatureServer/0

Emits:
  data/lands-nps.geojson      — simplified polygons + slim properties
  data/lands-index.json       — bbox/centroid/acres per unit + national totals
  data/lands-nps-benchmark.json — acreage-error benchmark (transparency)

Design notes:
  - Area is computed in EPSG:5070 (NAD83 CONUS Albers Equal Area). Albers is a
    conic equal-area projection — area is preserved globally, so AK / HI / insular
    NPS units come out with correct acreage. Shape distortion increases outside
    the standard parallels, but shape distortion does not affect .area (equal-area
    is the defining property). First-run national total 85.19M ac matches the
    published NPS figure to within 0.05%.
  - Simplification tolerance is locked at 0.001 degrees (~111m at equator).
    Measured 2026-07-08: national roll-up error 0.013%, median unit error <1%,
    worst-case tiny-unit error ~30% on a 10-acre unit (~3 acres absolute — the
    Douglas-Peucker tolerance is comparable to the unit's radius, physics not
    a bug). Acceptable for the national/regional view; individual small units
    render fine visually. If a client of this feed needs sub-acre precision on
    a tiny unit, they should fetch that unit's raw geometry directly.
  - Simplification is non-topology-preserving-across-features. Douglas-Peucker
    with preserve_topology=True keeps each polygon valid on its own.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

from common import (
    fetch_arcgis_features,
    write_geojson,
    write_json,
    fail,
    now_iso,
)

# ---- constants ---------------------------------------------------------------

NPS_LAYER = (
    "https://services1.arcgis.com/fBc8EJBxQRMcHlei/ArcGIS/rest/services/"
    "National_Park_Service_Boundaries/FeatureServer/0"
)

OUT_FIELDS = "FID,UNIT_CODE,UNIT_NAME,UNIT_TYPE,REGION,STATE,PARKNAME"

# Lock after benchmark run:
LOCKED_TOLERANCE_DEG = 0.001  # ~111m at equator, tightens toward poles
BENCHMARK_TOLERANCES = [0.005, 0.002, 0.001, 0.0005, 0.0002, 0.0001]

DATA_DIR = Path("data")


# ---- geometry helpers --------------------------------------------------------

def _lazy_shapely():
    try:
        from shapely.geometry import shape, mapping
        from shapely.ops import transform
        from shapely.validation import make_valid
        import pyproj
        return shape, mapping, transform, make_valid, pyproj
    except ImportError as e:
        fail(f"Missing geometry deps: {e}. Install shapely + pyproj.")


def _to_albers(pyproj):
    """Return a callable that reprojects (lon, lat) -> (x, y) EPSG:5070 meters."""
    transformer = pyproj.Transformer.from_crs(4326, 5070, always_xy=True)
    return transformer.transform


def _area_acres(geom_wgs84, transform, to_albers) -> float:
    """Area in acres via EPSG:5070 (equal-area, meters). 1 acre = 4046.8564224 m²."""
    projected = transform(to_albers, geom_wgs84)
    return projected.area / 4046.8564224


def _bbox(geom) -> list[float]:
    minx, miny, maxx, maxy = geom.bounds
    return [round(minx, 5), round(miny, 5), round(maxx, 5), round(maxy, 5)]


def _centroid(geom) -> dict[str, float]:
    c = geom.representative_point()  # guaranteed inside polygon; better than centroid for concave shapes
    return {"lat": round(c.y, 5), "lng": round(c.x, 5)}


# ---- benchmark ---------------------------------------------------------------

def _benchmark(features_wgs84: list[Any], transform, to_albers) -> dict:
    """For a representative sample, measure acreage error at each tolerance."""
    if not features_wgs84:
        return {"note": "no features"}
    n = len(features_wgs84)
    # Score by area first so we grab the largest and smallest for the sample.
    scored = [(i, f.area, f) for i, f in enumerate(features_wgs84)]
    scored.sort(key=lambda x: x[1])
    # Sample: 5 smallest + 5 largest + 10 evenly spaced middle.
    small = scored[:5]
    large = scored[-5:]
    step = max(1, (n - 10) // 10)
    middle = scored[5 : n - 5 : step][:10]
    sample = small + middle + large
    seen = set()
    sample_unique = []
    for s in sample:
        if s[0] in seen:
            continue
        seen.add(s[0])
        sample_unique.append(s[2])

    per_unit = []
    per_tol_national = {}
    national_true_acres = sum(_area_acres(f, transform, to_albers) for f in features_wgs84)

    for tol in BENCHMARK_TOLERANCES:
        # National roll-up error under this tolerance:
        simp_all_acres = 0.0
        for f in features_wgs84:
            simp = f.simplify(tol, preserve_topology=True)
            simp_all_acres += _area_acres(simp, transform, to_albers)
        per_tol_national[str(tol)] = {
            "national_true_acres": round(national_true_acres, 1),
            "national_simplified_acres": round(simp_all_acres, 1),
            "national_pct_error": round(
                100 * abs(simp_all_acres - national_true_acres) / max(national_true_acres, 1), 4
            ),
        }

    for f in sample_unique:
        true_acres = _area_acres(f, transform, to_albers)
        row = {"true_acres": round(true_acres, 2), "by_tolerance": {}}
        for tol in BENCHMARK_TOLERANCES:
            simp = f.simplify(tol, preserve_topology=True)
            simp_acres = _area_acres(simp, transform, to_albers)
            pct_err = 100 * abs(simp_acres - true_acres) / max(true_acres, 1)
            row["by_tolerance"][str(tol)] = {
                "acres": round(simp_acres, 2),
                "pct_error": round(pct_err, 4),
            }
        per_unit.append(row)

    return {
        "generated": now_iso(),
        "note": (
            "Douglas-Peucker simplification tolerance sweep. per_tol_national "
            "computed over ALL features. per_unit is a 20-unit sample (5 smallest + "
            "10 middle + 5 largest by raw area)."
        ),
        "locked_tolerance_deg": LOCKED_TOLERANCE_DEG,
        "tolerances_swept": [str(t) for t in BENCHMARK_TOLERANCES],
        "per_tolerance_national": per_tol_national,
        "sample_units": per_unit,
    }


# ---- main --------------------------------------------------------------------

def main() -> None:
    shape, mapping, transform, make_valid, pyproj = _lazy_shapely()
    to_albers = _to_albers(pyproj)

    print(f"[{now_iso()}] Fetching NPS boundaries…")
    try:
        raw = fetch_arcgis_features(
            NPS_LAYER,
            where="1=1",
            out_fields=OUT_FIELDS,
            page_size=2000,
        )
    except Exception as e:  # noqa: BLE001
        fail(f"NPS fetch failed: {e}")

    if not raw:
        fail("NPS fetch returned zero features")

    print(f"  fetched {len(raw)} raw features")

    # Parse + validate geometry.
    parsed: list[tuple[dict, Any]] = []
    skipped = 0
    for f in raw:
        geom_geojson = f.get("geometry")
        props = f.get("properties") or {}  # GeoJSON output puts attributes under properties
        if not geom_geojson:
            skipped += 1
            continue
        try:
            g = shape(geom_geojson)
            if not g.is_valid:
                g = make_valid(g)
            if g.is_empty:
                skipped += 1
                continue
            parsed.append((props, g))
        except Exception as e:  # noqa: BLE001
            skipped += 1
            print(f"  WARN skip {props.get('UNIT_CODE') or '?'}: {e}")

    if not parsed:
        fail("all NPS features failed geometry parse")
    print(f"  parsed {len(parsed)} valid geometries (skipped {skipped})")

    # Benchmark BEFORE lock (transparency: keep the report so operator can audit).
    print("  running acreage-error benchmark…")
    geoms_only = [g for _, g in parsed]
    bench = _benchmark(geoms_only, transform, to_albers)
    DATA_DIR.mkdir(exist_ok=True)
    with open(DATA_DIR / "lands-nps-benchmark.json", "w") as f:
        json.dump(bench, f, indent=2)
    nat_report = bench.get("per_tolerance_national", {}).get(str(LOCKED_TOLERANCE_DEG), {})
    print(
        f"  benchmark @ locked tol {LOCKED_TOLERANCE_DEG}: "
        f"national error = {nat_report.get('national_pct_error', '?')}%"
    )

    # Emit lands-nps.geojson (simplified polygons).
    out_features = []
    index_units = []
    total_acres = 0.0
    for props, g in parsed:
        true_acres = _area_acres(g, transform, to_albers)
        simp = g.simplify(LOCKED_TOLERANCE_DEG, preserve_topology=True)
        if simp.is_empty or not simp.is_valid:
            simp = g  # fallback: keep raw for oddballs
        simp_acres = _area_acres(simp, transform, to_albers)
        total_acres += true_acres
        bbox = _bbox(simp)
        centroid = _centroid(simp)
        unit_code = props.get("UNIT_CODE") or ""
        unit_name = props.get("UNIT_NAME") or props.get("PARKNAME") or ""
        unit_type = props.get("UNIT_TYPE") or ""
        region = props.get("REGION") or ""
        state = props.get("STATE") or ""

        out_features.append({
            "type": "Feature",
            "properties": {
                "bureau": "NPS",
                "unit_code": unit_code,
                "unit_name": unit_name,
                "unit_type": unit_type,
                "region": region,
                "state": state,
                "acres": round(true_acres, 1),           # authoritative — unsimplified
                "acres_simplified": round(simp_acres, 1),  # derived from render geometry
            },
            "geometry": mapping(simp),
        })
        index_units.append({
            "unit_code": unit_code,
            "unit_name": unit_name,
            "unit_type": unit_type,
            "region": region,
            "state": state,
            "acres": round(true_acres, 1),
            "bbox": bbox,
            "centroid": centroid,
        })

    # Write outputs.
    write_geojson(
        str(DATA_DIR / "lands-nps.geojson"),
        {
            "type": "FeatureCollection",
            "generated": now_iso(),
            "version": "v1",
            "bureau": "NPS",
            "tolerance_deg": LOCKED_TOLERANCE_DEG,
            "attribution": "National Park Service — Land Resources Division",
            "features": out_features,
        },
    )

    # Sort units by acres desc for easier operator scanning.
    index_units.sort(key=lambda u: u["acres"], reverse=True)
    write_json(
        str(DATA_DIR / "lands-index.json"),
        {
            "bureau": "NPS",
            "totals": {"units": len(index_units), "acres": round(total_acres, 1)},
            "units": index_units,
            "attribution": "National Park Service — Land Resources Division",
        },
    )

    print(f"  DONE. {len(out_features)} units, {total_acres:,.0f} total acres")
    # Sanity check vs published NPS figure (~85M acres). Warn if wildly off.
    if not (60_000_000 < total_acres < 100_000_000):
        print(
            f"  WARN: national total {total_acres:,.0f} acres is outside plausible "
            "NPS range (60M-100M). Investigate before publishing."
        )


if __name__ == "__main__":
    main()
