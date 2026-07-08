"""Shared helpers for prometheus-doi-lands-data.

Fetches DOI bureau boundary polygons, computes acres in an equal-area projection
(EPSG:5070 NAD83 CONUS Albers — good for CONUS + reasonable elsewhere for our
error budget), simplifies geometry via Douglas-Peucker, and emits slim JSON +
GeoJSON on the CDN. Fail LOUD (non-zero exit) so silent-corruption never ships.

The lands mask is a WEEKLY refresh — boundaries barely move. Consumers of this
repo (prometheus-hazard-lands-data) intersect hazard polygons against this mask
in the pipeline, so the browser never touches the raw 655k-polygon PAD-US.
"""
from __future__ import annotations
import json, sys, urllib.request, urllib.error, urllib.parse, datetime, time

UA = "prometheus-doi-lands/1.0 (+https://github.com/Deasus/prometheus-doi-lands-data)"


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get(url: str, timeout: int = 60, accept: str | None = None, retries: int = 3) -> bytes:
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": UA, **({"Accept": accept} if accept else {})},
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"GET {url} failed after {retries} attempts: {last_err}")


def get_json(url: str, timeout: int = 60):
    return json.loads(get(url, timeout, accept="application/json"))


def write_json(path: str, payload: dict) -> None:
    payload.setdefault("generated", now_iso())
    payload.setdefault("version", "v1")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print(f"WROTE {path}")


def write_geojson(path: str, feature_collection: dict) -> None:
    """FeatureCollection writer — preserves top-level 'generated'/'version'."""
    feature_collection.setdefault("type", "FeatureCollection")
    feature_collection.setdefault("generated", now_iso())
    feature_collection.setdefault("version", "v1")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(feature_collection, f, ensure_ascii=False, separators=(",", ":"))
    print(f"WROTE {path}  ({len(feature_collection.get('features', []))} features)")


def fail(msg: str):
    print(f"FATAL: {msg}", file=sys.stderr)
    sys.exit(1)


def fetch_arcgis_features(
    layer_url: str,
    where: str = "1=1",
    out_fields: str = "*",
    out_sr: int = 4326,
    page_size: int = 2000,
    max_features: int | None = None,
) -> list[dict]:
    """Paginate an ArcGIS FeatureServer/MapServer layer, returning raw feature dicts
    with .attributes and .geometry (GeoJSON shape when out_sr=4326 and f=geojson).

    Uses f=geojson so shapely can consume geometry directly. Falls back to esriJson
    if the server refuses geojson (rare but possible)."""
    out: list[dict] = []
    offset = 0
    while True:
        params = {
            "where": where,
            "outFields": out_fields,
            "outSR": out_sr,
            "f": "geojson",
            "resultRecordCount": page_size,
            "resultOffset": offset,
            "returnGeometry": "true",
        }
        url = f"{layer_url}/query?" + urllib.parse.urlencode(params)
        page = get_json(url, timeout=120)
        feats = page.get("features") or []
        if not feats:
            break
        out.extend(feats)
        offset += len(feats)
        # ArcGIS signals no-more-pages by omitting exceededTransferLimit OR
        # returning fewer than page_size. Handle both.
        if len(feats) < page_size and not page.get("properties", {}).get("exceededTransferLimit"):
            break
        # Some servers put the flag at top level, not in properties.
        if not page.get("exceededTransferLimit") and len(feats) < page_size:
            break
        if max_features and len(out) >= max_features:
            out = out[:max_features]
            break
    return out
