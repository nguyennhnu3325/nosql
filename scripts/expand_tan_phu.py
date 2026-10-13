"""Download the former Tan Phu district and merge 150 additional named OSM POIs.

Run --apply only after reviewing data/tan_phu_expansion/manifest.json.
Existing traffic and accounts are retained. Raw downloads and backups are preserved.
"""
import argparse
import json
import os
import shutil
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx
import osmnx as ox
from neo4j import GraphDatabase
from shapely.geometry import mapping
from shapely.geometry import LineString
from shapely.ops import polygonize, unary_union

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data' / 'tan_phu_expansion'
sys.path.insert(0, str(ROOT / 'app'))
from traffic_admin import snapshot, validate_snapshot, write_snapshot
from console import database_snapshot


def normalized(text):
    return ' '.join(unicodedata.normalize('NFC', str(text)).casefold().split())


def text(value):
    if isinstance(value, list):
        return ' / '.join(dict.fromkeys(str(v) for v in value))
    return str(value) if value is not None else ''


def prepare(db):
    OUT.mkdir(parents=True, exist_ok=True)
    ox.settings.use_cache = True
    ox.settings.requests_timeout = 180
    ox.settings.log_console = True
    ox.settings.http_user_agent = 'Neo4jRouting-TanPhu-StudentProject/1.0 educational import'
    ox.settings.http_referer = 'https://www.openstreetmap.org/'
    ox.settings.overpass_url = 'https://gall.openstreetmap.de/api'
    ox.settings.overpass_rate_limit = False
    print('Loading OSM district boundary R6846128', flush=True)
    relation_path = OUT / 'relation.json'
    if relation_path.exists():
        relation = json.loads(relation_path.read_text(encoding='utf-8'))['elements'][0]
        rings = {}
        for role in ['outer', 'inner']:
            lines = [LineString([(point['lon'], point['lat']) for point in member['geometry']])
                     for member in relation['members'] if member.get('role') == role and member.get('geometry')]
            rings[role] = unary_union(list(polygonize(lines)))
        polygon = rings['outer'].difference(rings['inner'])
    else:
        boundary = ox.geocode_to_gdf('R6846128', by_osmid=True)
        polygon = boundary.geometry.iloc[0]
    if not polygon.covers(__import__('shapely').geometry.Point(106.628862, 10.806527)):
        raise RuntimeError('Boundary does not contain HUIT; refusing wrong region.')
    (OUT / 'boundary.geojson').write_text(json.dumps(mapping(polygon)), encoding='utf-8')
    graph_path = OUT / 'road_network.graphml'
    if graph_path.exists():
        graph = ox.load_graphml(graph_path)
    else:
        graph = ox.graph_from_polygon(polygon, network_type='drive', simplify=True, retain_all=True, truncate_by_edge=True)
        ox.save_graphml(graph, graph_path)
    poi_path = OUT / 'pois.geojson'
    if poi_path.exists():
        import geopandas as gpd
        pois = gpd.read_file(poi_path)
    else:
        pois = ox.features_from_polygon(polygon, tags={'amenity': True, 'shop': True, 'leisure': True, 'tourism': True})
        pois = pois.reset_index()
        # Save only fields needed by this workflow; OSM tags may contain nested values.
        fields = [field for field in ['element', 'id', 'name', 'amenity', 'shop', 'leisure', 'tourism', 'geometry'] if field in pois]
        pois = pois[fields]
        pois.to_file(poi_path, driver='GeoJSON')
    existing = db.execute_read(snapshot)
    existing_names = {normalized(p.get('name', '')) for p in existing['places']}
    existing_ids = {str(p.get('placeId')) for p in existing['places']}
    candidates, seen = [], set(existing_names)
    supported_categories = {
        'university', 'college', 'school', 'kindergarten', 'hospital', 'clinic', 'pharmacy',
        'bank', 'atm', 'marketplace', 'restaurant', 'cafe', 'fast_food', 'police', 'post_office',
        'fuel', 'parking', 'supermarket', 'mall', 'convenience', 'bakery', 'clothes',
        'electronics', 'mobile_phone', 'books', 'park', 'sports_centre', 'fitness_centre',
        'playground', 'attraction', 'hotel', 'bus_station', 'place_of_worship', 'cinema'}
    for _, poi in pois.iterrows():
        import pandas as pd
        if pd.isna(poi.get('name')) or not str(poi['name']).strip():
            continue
        point = poi.geometry.representative_point()
        if not polygon.covers(point):
            continue
        name = str(poi['name']).strip()
        category = next((str(poi[k]) for k in ['amenity', 'shop', 'leisure', 'tourism'] if k in poi and pd.notna(poi[k])), 'other')
        if category not in supported_categories:
            continue
        pid = f"OSM_{poi.get('element', 'feature')}_{int(poi['id'])}"
        candidates.append(dict(placeId=pid, name=name, category=category, latitude=point.y, longitude=point.x,
                               osmType=str(poi.get('element', 'feature')), osmId=str(int(poi['id']))))
    # Round-robin categories to avoid selecting only cafes/shops.
    buckets = {}
    for candidate in sorted(candidates, key=lambda p: (p['name'], p['placeId'])):
        buckets.setdefault(candidate['category'], []).append(candidate)
    selected = []
    while len(selected) < 150 and any(buckets.values()):
        for category in sorted(buckets):
            if not buckets[category] or len(selected) == 150:
                continue
            candidate = buckets[category].pop(0)
            name = normalized(candidate['name'])
            if name in seen or candidate['placeId'] in existing_ids:
                continue
            seen.add(name)
            selected.append(candidate)
    if len(selected) < 150:
        raise RuntimeError(f'Only {len(selected)} distinct new named POIs found; no fabricated places will be added.')
    junctions = [dict(junctionId=int(node), latitude=float(props['y']), longitude=float(props['x'])) for node, props in graph.nodes(data=True)]
    roads = []
    for source, target, key, props in graph.edges(keys=True, data=True):
        roads.append(dict(source=int(source), target=int(target), properties=dict(
            roadId=f'{source}_{target}_{key}', name=text(props.get('name')), highway=text(props.get('highway')),
            oneway=bool(props.get('oneway', False)), lengthM=round(float(props['length']), 2))))
    data = dict(format='neo4j-routing-traffic-v1', junctions=junctions, roads=roads, places=selected, access=[])
    validate_snapshot(data)
    (OUT / 'import.json').write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    manifest = dict(boundaryRelation=6846128, scope='Former Tan Phu district, Ho Chi Minh City',
                    networkType='drive', retainDisconnected=True, boundaryEdgesIncluded=True,
                    junctions=len(junctions), roads=len(roads), newPlaces=len(selected), existingPlaces=len(existing['places']),
                    source='https://www.openstreetmap.org/relation/6846128', preparedAt=datetime.now(timezone.utc).isoformat())
    (OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(manifest), flush=True)


def apply(db):
    data = validate_snapshot(json.loads((OUT / 'import.json').read_text(encoding='utf-8')))
    backup = ROOT / 'backups' / ('tan_phu_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(parents=True)
    before = db.execute_read(database_snapshot)
    (backup / 'database.json').write_text(json.dumps(before, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    for filename in ['road_network.graphml', 'nodes.csv', 'roads.csv', 'nodes_clean.csv', 'roads_clean.csv', 'places.csv', 'places_with_junction.csv']:
        path = ROOT / 'data' / filename
        if path.exists():
            shutil.copy2(path, backup / filename)
    graph = nx.compose(ox.load_graphml(ROOT / 'data' / 'road_network.graphml'), ox.load_graphml(OUT / 'road_network.graphml'))
    ox.save_graphml(graph, OUT / 'merged.graphml')
    def merge(tx):
        # Refuse stale preparations, rather than adding fewer than 150 new places.
        ids = [p['placeId'] for p in data['places']]
        if tx.run('MATCH (p:Place) WHERE p.placeId IN $ids RETURN count(p) AS n', ids=ids).single()['n']:
            raise RuntimeError('Prepared places already exist; prepare again before applying.')
        write_snapshot(tx, data, False)
        return dict(tx.run('MATCH (p:Place) WHERE p.placeId IN $ids OPTIONAL MATCH (p)-[a:ACCESS_TO]->(:Junction) RETURN count(DISTINCT p) AS places, count(a) AS access', ids=ids).single())
    result = db.execute_write(merge)
    shutil.copy2(OUT / 'merged.graphml', ROOT / 'data' / 'road_network.graphml')
    after = db.execute_read(snapshot)
    import pandas as pd
    pd.DataFrame(after['junctions']).to_csv(ROOT / 'data' / 'nodes_clean.csv', index=False, encoding='utf-8-sig')
    pd.DataFrame([dict(source=r['source'], target=r['target'], **r['properties']) for r in after['roads']]).to_csv(ROOT / 'data' / 'roads_clean.csv', index=False, encoding='utf-8-sig')
    for stem in ['nodes', 'roads']:
        shutil.copy2(ROOT / 'data' / f'{stem}_clean.csv', ROOT / 'data' / f'{stem}.csv')
    pd.DataFrame(after['places']).to_csv(ROOT / 'data' / 'places.csv', index=False, encoding='utf-8-sig')
    access = {row['placeId']: row for row in after['access']}
    pd.DataFrame([dict(p, junctionId=access.get(p['placeId'], {}).get('junctionId'),
                       accessDistanceM=access.get(p['placeId'], {}).get('properties', {}).get('accessDistanceM'))
                  for p in after['places']]).to_csv(ROOT / 'data' / 'places_with_junction.csv', index=False, encoding='utf-8-sig')
    if db.run("CALL gds.graph.exists('roadGraph') YIELD exists RETURN exists").single()['exists']:
        db.run("CALL gds.graph.drop('roadGraph')").consume()
    report = dict(result, totalJunctions=len(after['junctions']), totalRoads=len(after['roads']), totalPlaces=len(after['places']), backup=str(backup))
    (OUT / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    with GraphDatabase.driver(os.getenv('NEO4J_URI', 'bolt://localhost:7687'), auth=(os.getenv('NEO4J_USER', 'neo4j'), os.getenv('NEO4J_PASSWORD', '12345678'))) as driver:
        with driver.session(database=os.getenv('NEO4J_DATABASE', 'neo4j')) as db:
            apply(db) if args.apply else prepare(db)
