"""Real GDS integration. Isolated fixture label and GDS graph are removed in finally."""
import ast
import os
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from neo4j import GraphDatabase
from routing import search_paths, serialize_path


class FixtureSession:
    def __init__(self, session, label):
        self.db, self.label = session, label
    def run(self, query, **params):
        return self.db.run(query.replace(':Junction', ':' + self.label), **params)
    def session(self, **kwargs):
        return self
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


def main():
    driver = GraphDatabase.driver(os.environ.get('NEO4J_URI', 'bolt://localhost:7687'),
        auth=(os.environ.get('NEO4J_USER', 'neo4j'), os.environ.get('NEO4J_PASSWORD', '12345678')))
    graph = 'routing_smoke_' + uuid4().hex
    label = 'RoutingSmoke_' + uuid4().hex
    try:
        with driver.session(database=os.environ.get('NEO4J_DATABASE', 'neo4j')) as db:
            for n in range(1,6):
                db.run('CREATE (n:' + label + ' {junctionId: $id, latitude: $lat, longitude: 106.6})',
                       id=900000000000000+n, lat=10.8+n/1000).consume()
            for source, target, length, rid in [(1,2,100,'AB'),(1,2,110,'AB_parallel'),(2,4,100,'BD'),
                    (1,3,140,'AC'),(3,4,160,'CD'),(2,3,30,'BC')]:
                db.run('MATCH (s:' + label + ' {junctionId: $source}), (t:' + label + ' {junctionId: $target}) '
                       'CREATE (s)-[:ROAD {roadId: $rid, lengthM: $length, name: $rid, highway: "test"}]->(t)',
                       source=900000000000000+source, target=900000000000000+target, length=length, rid=rid).consume()
            db.run('CALL gds.graph.project($graph, $label, {ROAD: {orientation: "NATURAL", properties: "lengthM"}}) '
                   'YIELD nodeCount RETURN nodeCount', graph=graph, label=label).consume()
            endpoints = dict(sourceId=900000000000001, targetId=900000000000004,
                startPlace=dict(placeId='test_start'), endPlace=dict(placeId='test_end'), startAccessM=10, endAccessM=20)
            session = FixtureSession(db, label)
            paths = search_paths(session, endpoints, 2, graph_name=graph)
            assert [p['roadDistanceM'] for p in paths] == [200,290,300], paths
            tree = ast.parse((Path(__file__).resolve().parents[1] / 'app/app.py').read_text(encoding='utf-8'))
            funcs = {'get_osm_node','normalize_osm_value','build_route_geometry'}
            selected = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in funcs], type_ignores=[])
            ns = dict(driver=session, NEO4J_DATABASE='neo4j', OSM_GRAPH={})
            exec(compile(selected, 'app/app.py', 'exec'), ns)
            for index,path in enumerate(paths):
                result = serialize_path(endpoints, path, ns['build_route_geometry'], index)
                assert result['totalDistanceM'] == path['roadDistanceM']+30
                assert abs(sum(r['lengthM'] for r in result['routeRoads'])-path['roadDistanceM']) < 1e-6
                assert len(result['routeCoordinates']) == len(path['routeNodeIds'])
            assert search_paths(session, dict(endpoints, targetId=900000000000005), 2, graph_name=graph) == []
            assert search_paths(session, dict(endpoints, sourceId=900000000000004, targetId=900000000000001), 2, graph_name=graph) == []
            print('GDS OK: Dijkstra=200m; unique alternatives=290m,300m; geometry costs, disconnected and one-way cases verified.')
    finally:
        with driver.session(database=os.environ.get('NEO4J_DATABASE', 'neo4j')) as db:
            if db.run('CALL gds.graph.exists($graph) YIELD exists RETURN exists', graph=graph).single()['exists']:
                db.run('CALL gds.graph.drop($graph) YIELD graphName RETURN graphName', graph=graph).consume()
            db.run('MATCH (n:' + label + ') DETACH DELETE n').consume()
        driver.close()


if __name__ == '__main__':
    main()
