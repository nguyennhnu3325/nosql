import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from network_analysis import analyze_network


class NetworkAnalysisTests(unittest.TestCase):
    def test_directed_components_and_distances(self):
        data = dict(junctions=[dict(junctionId=i) for i in range(1, 5)],
                    places=[dict(placeId='p1'), dict(placeId='p2')],
                    roads=[dict(source=a, target=b, properties=dict(lengthM=length, highway='road'))
                           for a, b, length in [(1, 2, 100), (2, 1, 100), (2, 3, 300), (1, 2, -1)]],
                    access=[dict(placeId='p1', junctionId=1, properties=dict(accessDistanceM=50)),
                            dict(placeId='p1', junctionId=2, properties=dict(accessDistanceM=True))])
        result = analyze_network(data)
        self.assertEqual(result['length']['totalKm'], .5)
        self.assertEqual(result['length']['invalidCount'], 1)
        self.assertEqual(result['degree']['isolated'], 1)
        self.assertEqual(result['degree']['average'], 2)
        self.assertEqual(result['connectivity']['weakCount'], 2)
        self.assertEqual(result['connectivity']['strongCount'], 3)
        self.assertEqual(result['connectivity']['largestStrong'], 2)
        self.assertEqual(result['access']['coveragePercent'], 50)
        self.assertEqual(result['access']['multiple'], 1)
        self.assertEqual(result['access']['invalidCount'], 1)
        self.assertEqual(sum(r['count'] for r in result['length']['histogram']), 3)

    def test_empty_graph(self):
        result = analyze_network(dict(junctions=[], places=[], roads=[], access=[]))
        self.assertEqual(result['connectivity']['strongCount'], 0)
        self.assertEqual(result['length']['totalKm'], 0)
        self.assertEqual(result['access']['coveragePercent'], 0)

    def test_quality_duplicate_ids_and_warning_threshold(self):
        data = dict(junctions=[dict(_key='j1', junctionId=1, latitude=10, longitude=106),
                               dict(_key='j2', junctionId=1, latitude=None, longitude=106)],
                    places=[dict(_key='p1', placeId='p'), dict(_key='p2', placeId='p')],
                    roads=[dict(source=1, target=1, sourceKey='j1', targetKey='j1', properties=dict(roadId='r', lengthM=20)),
                           dict(source=1, target=1, sourceKey='j1', targetKey='j2', properties=dict(roadId='r', lengthM=float('nan')))],
                    access=[dict(placeId='p', placeKey='p1', junctionId=1, junctionKey='j1', properties=dict(accessDistanceM=501)),
                            dict(placeId='p', placeKey='p1', junctionId=1, junctionKey='j1', properties=dict(accessDistanceM=500))])
        result = analyze_network(data, 500)
        self.assertEqual(result['overview']['junctions'], 2)
        self.assertEqual(result['quality']['duplicateGroups'], 3)
        self.assertEqual(result['quality']['missingCoordinates'], 1)
        self.assertEqual(result['quality']['invalidLength'], 1)
        self.assertEqual(result['quality']['selfLoops'], 1)
        self.assertEqual(result['quality']['unnamedRoads'], 2)
        self.assertEqual(result['degree']['maximum'], 3)
        self.assertEqual(result['degree']['averageIn'], 1)
        self.assertEqual(result['connectivity']['largest'], 2)
        self.assertEqual(result['connectivity']['mainPercent'], 100)
        self.assertEqual(result['access']['disconnected'], 1)
        self.assertEqual(result['access']['warningCount'], 1)
        self.assertEqual(len(result['access']['top']), 1)
        self.assertEqual(analyze_network(data, 501)['access']['warningCount'], 0)

    def test_long_chain_without_recursion_and_self_loop(self):
        data = dict(junctions=[dict(junctionId=i) for i in range(2000)], places=[], access=[],
                    roads=[dict(source=i, target=i+1, properties={}) for i in range(1999)])
        data['roads'].append(dict(source=0, target=0, properties={}))
        result = analyze_network(data)
        self.assertEqual(result['connectivity']['weakCount'], 1)
        self.assertEqual(result['connectivity']['strongCount'], 2000)
        self.assertEqual(result['degree']['isolated'], 0)

    def test_issue_lists_and_isolated_junction(self):
        data = dict(junctions=[dict(_key='j1', junctionId=1, latitude=0, longitude=0),
                               dict(_key='j2', junctionId=2, latitude=10)],
                    places=[dict(_key='p1', placeId='p1'), dict(_key='p2', placeId='p2')],
                    roads=[dict(source=1, target=1, sourceKey='j1', targetKey='j1', properties=dict(roadId='missing')),
                           dict(source=1, target=1, sourceKey='j1', targetKey='j1', properties=dict(roadId='invalid', lengthM=-1))],
                    access=[dict(placeId='p1', placeKey='p1', junctionId=1, junctionKey='j1', properties={})])
        result = analyze_network(data)
        self.assertEqual(result['degree']['isolated'], 1)
        self.assertEqual(result['degree']['maximum'], 4)
        self.assertEqual(result['connectivity']['count'], 2)
        self.assertEqual([p['placeId'] for p in result['issues']['unlinkedPlaces']], ['p2'])
        self.assertEqual([r['roadId'] for r in result['issues']['missingLengthRoads']], ['missing'])
        self.assertEqual(len(result['issues']['selfLoopRoads']), 2)
        self.assertEqual([j['junctionId'] for j in result['issues']['missingCoordinateJunctions']], [2])


if __name__ == '__main__':
    unittest.main()
