import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from auth import AppError
from traffic_admin import FORMAT, validate_snapshot


def sample():
    return dict(format=FORMAT,
                junctions=[dict(junctionId=1, latitude=10.8, longitude=106.6)],
                places=[dict(placeId='P1', name='Cafe', category='cafe', latitude=10.8, longitude=106.6)],
                roads=[dict(source=1, target=1, properties=dict(roadId='R1', lengthM=1))],
                access=[dict(placeId='P1', junctionId=1, properties=dict(accessDistanceM=0))])


class SnapshotTests(unittest.TestCase):
    def test_valid_roundtrip(self):
        self.assertEqual(validate_snapshot(sample()), sample())

    def test_reject_dangling_road(self):
        data = sample()
        data['roads'][0]['target'] = 2
        with self.assertRaises(AppError):
            validate_snapshot(data)

    def test_reject_duplicate_ids(self):
        for key in ('junctions', 'places', 'roads', 'access'):
            with self.subTest(key=key):
                data = sample()
                data[key].append(copy.deepcopy(data[key][0]))
                with self.assertRaises(AppError):
                    validate_snapshot(data)

    def test_reject_nonfinite_coordinates_and_negative_lengths(self):
        data = sample()
        data['junctions'][0]['latitude'] = float('nan')
        with self.assertRaises(AppError):
            validate_snapshot(data)
        data = sample()
        data['roads'][0]['properties']['lengthM'] = -1
        with self.assertRaises(AppError):
            validate_snapshot(data)

    def test_reject_wrong_format_and_invalid_place(self):
        with self.assertRaises(AppError):
            validate_snapshot({})
        data = sample()
        data['places'][0]['name'] = ''
        with self.assertRaises(AppError):
            validate_snapshot(data)


if __name__ == '__main__':
    unittest.main()
