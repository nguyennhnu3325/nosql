import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from flask import Flask
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from auth import AppError
from routing import search_paths, register_routing


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.endpoints = dict(sourceId=1, targetId=4, startPlace=dict(placeId='P1'),
            endPlace=dict(placeId='P2'), startAccessM=10, endAccessM=20)
        self.primary = dict(roadDistanceM=200, costs=[0,100,200], routeNodeIds=[1,2,4])
        self.session = MagicMock()
        self.session.run.return_value.single.return_value = self.primary

    def test_dijkstra_only(self):
        paths = search_paths(self.session, self.endpoints)
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0]['algorithm'], 'Dijkstra')
        self.assertEqual(self.session.run.call_count, 1)

    def test_alternatives_remove_duplicate_node_sequences(self):
        first = MagicMock()
        first.single.return_value = self.primary
        candidates = MagicMock()
        candidates.__iter__.return_value = iter([self.primary,
            dict(self.primary, roadDistanceM=210),
            dict(roadDistanceM=290, costs=[0,100,130,290], routeNodeIds=[1,2,3,4]),
            dict(roadDistanceM=300, costs=[0,140,300], routeNodeIds=[1,3,4])])
        self.session.run.side_effect = [first, candidates]
        paths = search_paths(self.session, self.endpoints, 2)
        self.assertEqual([p['roadDistanceM'] for p in paths], [200,290,300])
        self.assertEqual(paths[1]['algorithm'], 'Yen')

    def test_no_path_and_same_junction(self):
        self.session.run.return_value.single.return_value = dict(roadDistanceM=float('inf'), costs=[], routeNodeIds=[])
        self.assertEqual(search_paths(self.session, self.endpoints), [])
        self.session.reset_mock()
        paths = search_paths(self.session, dict(self.endpoints, targetId=1), 2)
        self.assertEqual(paths[0]['roadDistanceM'], 0)
        self.assertEqual(len(paths), 1)
        self.session.run.assert_not_called()

    def test_endpoint_validation_and_response_compatibility(self):
        app = Flask(__name__)
        app.testing = True
        @app.errorhandler(AppError)
        def error(exc):
            return {'error': str(exc)}, exc.status
        driver = MagicMock()
        db = driver.session.return_value.__enter__.return_value
        start_result, path_result = MagicMock(), MagicMock()
        start_result.single.return_value = self.endpoints
        path_result.single.return_value = self.primary
        db.run.side_effect = [start_result, path_result]
        geometry = MagicMock(return_value=([[10,106],[11,107]], []))
        ensure = MagicMock()
        register_routing(app, driver, 'neo4j', ensure, geometry)
        client = app.test_client()
        for body in ([], {}, {'startId':'P1','endId':'P1'},
                     {'startId':'P1','endId':'P2','alternativeCount':True},
                     {'startId':'P1','endId':'P2','alternativeCount':3}):
            self.assertEqual(client.post('/api/route', json=body).status_code, 400)
        response = client.post('/api/route', json=dict(startId='P1', endId='P2'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['totalDistanceM'], 230)
        self.assertEqual(response.json['routes'][0]['totalDistanceM'], 230)
        geometry.assert_called_once_with([1,2,4], [0,100,200])
        ensure.assert_called_once()


if __name__ == '__main__':
    unittest.main()
