import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock


class RoadGeometryTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / 'app/app.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        names = {'build_route_geometry', 'get_osm_node', 'normalize_osm_value', 'ensure_road_graph'}
        selected = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names], type_ignores=[])
        self.driver = MagicMock()
        self.db = self.driver.session.return_value.__enter__.return_value
        self.ns = dict(driver=self.driver, NEO4J_DATABASE='neo4j', OSM_GRAPH={}, app=SimpleNamespace(extensions={'road_graph_dirty': True}))
        exec(compile(selected, str(source), 'exec'), self.ns)
        self.record = dict(index=0, source=1, target=2, sourceLat=10.8, sourceLon=106.6,
                           targetLat=10.81, targetLon=106.61,
                           road=dict(roadId='new-road', name='New Road', highway='test', lengthM=123))

    def test_new_imported_road_uses_current_db_coordinates(self):
        self.db.run.return_value = [self.record]
        coordinates, roads = self.ns['build_route_geometry']([1, 2])
        self.assertEqual(coordinates, [[10.8, 106.6], [10.81, 106.61]])
        self.assertEqual(roads[0]['name'], 'New Road')
        self.assertEqual(roads[0]['lengthM'], 123)

    def test_missing_segment_is_not_silently_omitted(self):
        self.db.run.return_value = []
        with self.assertRaises(ValueError):
            self.ns['build_route_geometry']([1, 2])

    def test_places_access_same_junction(self):
        self.db.run.return_value.single.return_value = dict(lat=10.8, lon=106.6)
        coordinates, roads = self.ns['build_route_geometry']([1])
        self.assertEqual(coordinates, [[10.8, 106.6], [10.8, 106.6]])
        self.assertEqual(roads, [])

    def test_gds_invalidated_graph_is_rebuilt(self):
        exists = MagicMock()
        exists.single.return_value = {'exists': True}
        create = MagicMock()
        create.single.return_value = dict(nodeCount=2, relationshipCount=1)
        self.db.run.side_effect = [exists, MagicMock(), create]
        self.ns['ensure_road_graph']()
        self.assertIn('gds.graph.drop', self.db.run.call_args_list[1].args[0])
        self.assertIn('gds.graph.project', self.db.run.call_args_list[2].args[0])
        self.assertFalse(self.ns['app'].extensions['road_graph_dirty'])


if __name__ == '__main__':
    unittest.main()
