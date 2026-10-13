# Tân Phú OpenStreetMap expansion

Source: © OpenStreetMap contributors, https://www.openstreetmap.org/copyright
Data license: Open Database License (ODbL).
Boundary: https://www.openstreetmap.org/relation/6846128 — former Tân Phú district, not the smaller current ward.

The download uses OSMnx's `drive` network, retains disconnected components, and includes adjacent endpoints for boundary-crossing roads. It does not include every pedestrian path or private road. Existing application traffic is retained and merged by business ID.

- `relation.json`, `boundary.geojson`: original district boundary and assembled polygon.
- `road_network.graphml`: downloaded district driving network.
- `pois.geojson`: downloaded candidate points of interest.
- `import.json`: validated network and 150 additional named places; names already present were excluded.
- `manifest.json`: source and preparation counts.
- `result.json`: applied counts and backup location.
- `verification.json`: post-import preservation, ACCESS_TO and connectivity checks.
- `merged.graphml`: combined original and district OSM network.

The application data CSVs and GraphML were updated. Restart the running application to reload GraphML geometry. GDS `roadGraph` was invalidated and will be rebuilt by the application.

All 150 new places have ACCESS_TO. Four are outside the largest strongly connected ROAD component; routes to/from these points may be limited by OSM directionality or disconnected roads. Existing accounts and all 53 prior places were preserved.

Before import, the full logical database and original data files were saved under the backup path recorded in `result.json`. The download/import workflow is `scripts/expand_tan_phu.py`; `--apply` performs the merge, and refuses to reinsert previously prepared place IDs.
