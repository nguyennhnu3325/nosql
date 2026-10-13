import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from place_admin import PlaceError, register_place_admin, save_place, validate_place, check_place_bounds


class PlaceAdminTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / "app/templates"))
        self.app.testing = True
        self.app.context_processor(lambda: dict(current_user=None, csrf_token="test"))
        self.driver = MagicMock()
        self.session = self.driver.session.return_value.__enter__.return_value
        register_place_admin(self.app, self.driver, "neo4j")
        self.client = self.app.test_client()
        self.values = dict(name="Cafe", category="cafe", latitude=10.8, longitude=106.6)

    def test_admin_page(self):
        self.assertEqual(self.client.get("/admin").status_code, 200)

    def test_invalid_coordinates_do_not_write(self):
        for value in (None, True, "", "NaN", "Infinity", 91):
            with self.subTest(value=value):
                response = self.client.post("/api/admin/places", json={**self.values, "latitude": value})
                self.assertEqual(response.status_code, 400)
        self.session.execute_write.assert_not_called()

    def test_create_and_update(self):
        self.session.execute_write.return_value = {**self.values, "placeId": "P_test"}
        self.assertEqual(self.client.post("/api/admin/places", json=self.values).status_code, 201)
        self.assertEqual(self.client.put("/api/admin/places/P_test", json=self.values).status_code, 200)
        self.assertEqual(self.session.execute_write.call_args.args[1], "P_test")

    def test_missing_place(self):
        self.session.execute_write.side_effect = PlaceError("Không tìm thấy địa điểm.", 404)
        self.assertEqual(self.client.put("/api/admin/places/missing", json=self.values).status_code, 404)
        self.assertEqual(self.client.delete("/api/admin/places/missing").status_code, 404)

    def test_no_junction_aborts_before_create(self):
        tx = MagicMock()
        tx.run.return_value.single.return_value = None
        with self.assertRaises(PlaceError) as caught:
            save_place(tx, "P_test", self.values, True)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(tx.run.call_count, 1)

    def test_update_returns_new_access_distance(self):
        tx = MagicMock()
        tx.run.return_value.single.side_effect = [
            {"south": 10.7, "north": 10.9, "west": 106.5, "east": 106.7},
            {"placeId": "P_test"},
            {"nodeId": "junction-node", "distance": 12.345},
            {**self.values, "placeId": "P_test", "junctionId": "J2", "accessDistanceM": 12.35},
        ]
        result = save_place(tx, "P_test", self.values, False)
        self.assertEqual(result["junctionId"], "J2")
        self.assertEqual(tx.run.call_args.kwargs["distance"], 12.35)

    def test_outside_network_is_rejected_before_mutation(self):
        tx = MagicMock()
        tx.run.return_value.single.return_value = dict(south=10.7,north=10.9,west=106.5,east=106.7)
        for coordinate in ({'latitude':10.6},{'latitude':11},{'longitude':106.4},{'longitude':107}):
            with self.subTest(coordinate=coordinate), self.assertRaises(PlaceError):
                save_place(tx,'outside',{**self.values,**coordinate},True)
        self.assertTrue(all('CREATE' not in call.args[0] for call in tx.run.call_args_list))

    def test_bounds_edges_are_allowed(self):
        tx=MagicMock()
        tx.run.return_value.single.return_value=dict(south=10.7,north=10.9,west=106.5,east=106.7)
        check_place_bounds(tx,dict(latitude=10.7,longitude=106.7))


if __name__ == "__main__":
    unittest.main()
