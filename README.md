# prometheus-doi-lands-data

DOI-Lands boundary mask feed for **PROMETHEUS** (situational awareness for DOI Emergency Management / EHSD). Keyless federal sources → slim GeoJSON + JSON on the GitHub CDN, weekly cron.

**Phase 1 covers NPS only.** BLM / FWS / BIA / BOR / BOEM added in later phases.

## Files

- `data/lands-nps.geojson` — 422 NPS unit polygons, Douglas-Peucker simplified (0.001° ≈ 111m). Properties: `bureau`, `unit_code`, `unit_name`, `unit_type`, `region`, `state`, `acres` (authoritative unsimplified), `acres_simplified` (derived).
- `data/lands-index.json` — fast-filter index: national totals + per-unit `{unit_code, unit_name, bbox, centroid, acres, unit_type, region, state}`. No geometry — for panels that only need the roll-up + a click-to-drilldown.
- `data/lands-nps-benchmark.json` — acreage-error benchmark under tolerance sweep (transparency: operators can see what precision they're getting).

## Consumers

- `prometheus-hazard-lands-data` — intersects hazard polygons (fire/weather/quake) against this mask and publishes the DOI-Lands-scoped hazard feeds.
- `prometheus-sa` (the app) — reads only the intersected hazard feeds; never touches this raw mask directly (would be a 20MB payload).

## Source

- NPS Land Resources Division ArcGIS FeatureServer: `services1.arcgis.com/fBc8EJBxQRMcHlei/.../National_Park_Service_Boundaries/FeatureServer/0`
- Keyless, `f=geojson`, paginated at 2000/request.
- Attribution: **National Park Service — Land Resources Division**.

## Numbers (2026-07-08 first run)

- 422 features (matches published NPS unit count).
- 85,191,005 total acres (matches published NPS ~85M figure).
- 0.013% national roll-up error at the locked simplification tolerance.
- 20 MB raw / ~4 MB gzipped over the CDN. Refresh weekly.
