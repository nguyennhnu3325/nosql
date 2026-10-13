import math
from uuid import uuid4

from flask import Blueprint, jsonify, render_template, request


class PlaceError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def validate_place(data):
    if not isinstance(data, dict):
        raise PlaceError("Dữ liệu phải là đối tượng JSON.")
    values = {}
    for field in ("name", "category"):
        value = data.get(field)
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 200:
            raise PlaceError("Tên và loại địa điểm phải có từ 1 đến 200 ký tự.")
        values[field] = value.strip()
    for field, limit in (("latitude", 90), ("longitude", 180)):
        raw = data.get(field)
        if raw is None or isinstance(raw, bool):
            raise PlaceError("Tọa độ không hợp lệ.")
        try:
            value = float(raw)
        except (ValueError, TypeError):
            raise PlaceError("Tọa độ phải là số.")
        if not math.isfinite(value) or not -limit <= value <= limit:
            raise PlaceError(f"{field} phải nằm trong [-{limit}, {limit}].")
        values[field] = value
    return values


RESULT = """
RETURN p.placeId AS placeId, p.name AS name, p.category AS category,
       p.latitude AS latitude, p.longitude AS longitude,
       j.junctionId AS junctionId, a.accessDistanceM AS accessDistanceM
"""


def check_place_bounds(tx, values, existing_id=None):
    bounds = tx.run("""MATCH (j:Junction) WHERE j.latitude IS NOT NULL AND j.longitude IS NOT NULL
        RETURN min(toFloat(j.latitude)) AS south, max(toFloat(j.latitude)) AS north,
               min(toFloat(j.longitude)) AS west, max(toFloat(j.longitude)) AS east""").single()
    if not bounds or bounds['south'] is None:
        raise PlaceError('Chưa có mạng Junction hợp lệ.', 409)
    if not (bounds['south'] <= values['latitude'] <= bounds['north'] and
            bounds['west'] <= values['longitude'] <= bounds['east']):
        # Historical Places may already lie outside the current network. Allow
        # unchanged coordinates, but never add or move a Place outside its bounds.
        existing = tx.run('MATCH (p:Place {placeId:$id}) RETURN p.latitude AS latitude,p.longitude AS longitude',id=existing_id).single() if existing_id else None
        if existing and existing['latitude']==values['latitude'] and existing['longitude']==values['longitude']:
            return
        raise PlaceError('Place nằm ngoài phạm vi dữ liệu Junction. Hãy chọn vị trí trong vùng mạng lưới.')


def save_place(tx, place_id, values, creating):
    check_place_bounds(tx, values, place_id if not creating else None)
    if not creating:
        record = tx.run("""
            MATCH (p:Place {placeId: $placeId})
            SET p.placeId = p.placeId
            RETURN p.placeId AS placeId
        """, placeId=place_id).single()
        if record is None:
            raise PlaceError("Không tìm thấy địa điểm.", 404)
    nearest = tx.run("""
        MATCH (j:Junction)
        WHERE j.latitude IS NOT NULL AND j.longitude IS NOT NULL
        WITH j, point.distance(
            point({latitude: $latitude, longitude: $longitude}),
            point({latitude: toFloat(j.latitude), longitude: toFloat(j.longitude)})
        ) AS distance
        WHERE distance IS NOT NULL
        RETURN elementId(j) AS nodeId, distance
        ORDER BY distance, j.junctionId LIMIT 1
    """, **values).single()
    if nearest is None:
        raise PlaceError("Chưa có Junction có tọa độ hợp lệ. Hãy nhập mạng đường trước.", 409)
    query = ("CREATE (p:Place {placeId: $placeId})" if creating else
             "MATCH (p:Place {placeId: $placeId})") + """
        SET p.name = $name, p.category = $category,
            p.latitude = $latitude, p.longitude = $longitude
        WITH p
        OPTIONAL MATCH (p)-[old:ACCESS_TO]-()
        DELETE old
        WITH DISTINCT p
        MATCH (j:Junction) WHERE elementId(j) = $nodeId
        CREATE (p)-[a:ACCESS_TO {accessDistanceM: $distance}]->(j)
    """ + RESULT
    record = tx.run(query, placeId=place_id, **values,
                    nodeId=nearest["nodeId"], distance=round(nearest["distance"], 2)).single()
    if record is None:
        raise PlaceError("Junction không còn tồn tại. Hãy thử lại.", 409)
    return dict(record)


def delete_place(tx, place_id):
    record = tx.run("""
        MATCH (p:Place {placeId: $placeId})
        DETACH DELETE p
        RETURN count(p) AS deleted
    """, placeId=place_id).single()
    if record is None or record["deleted"] == 0:
        raise PlaceError("Không tìm thấy địa điểm.", 404)


def register_place_admin(app, driver, database):
    admin = Blueprint("place_admin", __name__)

    @admin.errorhandler(PlaceError)
    def place_error(error):
        return jsonify(error=str(error)), error.status

    @admin.route("/admin")
    def page():
        return render_template("manage.html", kind="places")

    @admin.route("/api/admin/places", methods=["GET"])
    def list_places():
        try:
            with driver.session(database=database) as session:
                result = session.run("MATCH (p:Place) OPTIONAL MATCH (p)-[a:ACCESS_TO]->(j:Junction) "
                                     + RESULT + " ORDER BY p.name, p.placeId")
                return jsonify([dict(record) for record in result])
        except Exception:
            app.logger.exception("Cannot list Places")
            return jsonify(error="Không thể đọc dữ liệu Neo4j."), 500

    @admin.route("/api/admin/places", methods=["POST"])
    @admin.route("/api/admin/places/<place_id>", methods=["PUT", "DELETE"])
    def mutate(place_id=None):
        try:
            if request.method == "DELETE":
                with driver.session(database=database) as session:
                    session.execute_write(delete_place, place_id)
                return jsonify(message="Đã xóa địa điểm.")
            values = validate_place(request.get_json(silent=True))
            creating = request.method == "POST"
            if creating:
                place_id = "P_" + uuid4().hex
            with driver.session(database=database) as session:
                place = session.execute_write(save_place, place_id, values, creating)
            return jsonify(place), 201 if creating else 200
        except PlaceError:
            raise
        except Exception:
            app.logger.exception("Cannot mutate Place")
            return jsonify(error="Không thể cập nhật Neo4j. Hãy thử lại."), 500

    app.register_blueprint(admin)
