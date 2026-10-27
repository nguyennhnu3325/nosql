from pathlib import Path
import os

if __package__:
    from .place_admin import register_place_admin
    from .auth import register_auth
    from .traffic_admin import register_traffic
    from .routing import register_routing
    from .console import register_console
else:
    from place_admin import register_place_admin
    from auth import register_auth
    from traffic_admin import register_traffic
    from routing import register_routing
    from console import register_console

import osmnx as ox

from flask import Flask, render_template, jsonify, request
from neo4j import GraphDatabase


# =========================================================
# CẤU HÌNH NEO4J
# =========================================================

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")

# SỬA THÀNH PASSWORD NEO4J CỦA M
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "12345678")

NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE", "neo4j")


# =========================================================
# LOAD MẠNG ĐƯỜNG OSM TỪ GRAPHML
# =========================================================

BASE_DIR = Path(__file__).resolve().parent.parent

GRAPHML_FILE = (
    BASE_DIR
    / "data"
    / "road_network.graphml"
)

print("Đang đọc mạng đường OSM...")

OSM_GRAPH = ox.io.load_graphml(
    GRAPHML_FILE
)

print(
    "OSM Graph:",
    len(OSM_GRAPH.nodes),
    "nodes -",
    len(OSM_GRAPH.edges),
    "edges"
)


# =========================================================
# KHỞI TẠO FLASK
# =========================================================

app = Flask(__name__)


# =========================================================
# KẾT NỐI NEO4J
# =========================================================

driver = GraphDatabase.driver(
    NEO4J_URI,
    auth=(
        NEO4J_USER,
        NEO4J_PASSWORD
    )
)


# =========================================================
# ĐẢM BẢO GDS roadGraph TỒN TẠI
# =========================================================

register_auth(app, driver, NEO4J_DATABASE)
register_place_admin(app, driver, NEO4J_DATABASE)
register_traffic(app, driver, NEO4J_DATABASE)


def ensure_road_graph():

    with driver.session(
        database=NEO4J_DATABASE
    ) as session:

        result = session.run(
            """
            CALL gds.graph.exists('roadGraph')
            YIELD exists
            RETURN exists
            """
        ).single()

        if result and result["exists"] and app.extensions.get("road_graph_dirty"):
            session.run("CALL gds.graph.drop('roadGraph') YIELD graphName RETURN graphName").consume()
            result = None

        if result and result["exists"]:

            print(
                "roadGraph đã tồn tại."
            )

            return

        print(
            "Đang tạo roadGraph..."
        )

        result = session.run(
            """
            CALL gds.graph.project(
                'roadGraph',
                'Junction',
                {
                    ROAD: {
                        orientation: 'NATURAL',
                        properties: 'lengthM'
                    }
                }
            )
            YIELD
                graphName,
                nodeCount,
                relationshipCount

            RETURN
                graphName,
                nodeCount,
                relationshipCount
            """
        ).single()

        app.extensions["road_graph_dirty"] = False

        if result:

            print(
                "Đã tạo roadGraph:",
                result["nodeCount"],
                "nodes -",
                result["relationshipCount"],
                "relationships"
            )


# =========================================================
# CHUẨN HÓA GIÁ TRỊ OSM
# =========================================================

def normalize_osm_value(value):

    if value is None:
        return ""

    if isinstance(
        value,
        (list, tuple)
    ):

        return " / ".join(
            str(item)
            for item in value
        )

    return str(value)


# =========================================================
# TÌM NODE TRONG GRAPHML
# =========================================================

def get_osm_node(junction_id):

    # Thử trực tiếp
    if junction_id in OSM_GRAPH:
        return junction_id

    # Thử dạng chuỗi
    junction_string = str(
        junction_id
    )

    if junction_string in OSM_GRAPH:
        return junction_string

    # Thử dạng integer
    try:

        junction_integer = int(
            junction_id
        )

        if junction_integer in OSM_GRAPH:
            return junction_integer

    except (
        ValueError,
        TypeError
    ):
        pass

    return None


# =========================================================
# TẠO GEOMETRY THẬT CỦA ROUTE
# =========================================================

def build_route_geometry(route_node_ids, route_costs=None):
    """Use current Neo4j roads; retain OSM curves only for unchanged edges."""
    with driver.session(database=NEO4J_DATABASE) as session:
        if len(route_node_ids) == 1:
            point = session.run("MATCH (j:Junction {junctionId: $id}) RETURN j.latitude AS lat, j.longitude AS lon",
                                id=route_node_ids[0]).single()
            coordinates = [float(point['lat']), float(point['lon'])]
            return [coordinates, coordinates[:]], []
        pairs = [dict(index=i, source=source, target=target,
                      segmentCost=(route_costs[i+1] - route_costs[i]) if route_costs else None)
                 for i, (source, target) in enumerate(zip(route_node_ids, route_node_ids[1:]))]
        records = [dict(record) for record in session.run("""
            UNWIND $pairs AS pair
            MATCH (s:Junction {junctionId: pair.source})-[r:ROAD]->(t:Junction {junctionId: pair.target})
            WHERE pair.segmentCost IS NULL OR abs(r.lengthM - pair.segmentCost) < 0.000001
            WITH pair, s, t, r ORDER BY r.lengthM, r.roadId
            WITH pair, s, t, collect(r)[0] AS road
            RETURN pair.index AS index, pair.source AS source, pair.target AS target,
                   s.latitude AS sourceLat, s.longitude AS sourceLon,
                   t.latitude AS targetLat, t.longitude AS targetLon,
                   properties(road) AS road
            ORDER BY index
        """, pairs=pairs)]
    if len(records) != len(pairs):
        raise ValueError('Mạng đường đã thay đổi. Hãy tìm lại tuyến đường.')
    route_coordinates, road_segments = [], []
    for record in records:
        road = record['road']
        source_coord = [float(record['sourceLat']), float(record['sourceLon'])]
        target_coord = [float(record['targetLat']), float(record['targetLon'])]
        coordinates = [source_coord, target_coord]
        source, target = get_osm_node(record['source']), get_osm_node(record['target'])
        if source is not None and target is not None:
            edges = OSM_GRAPH.get_edge_data(source, target) or {}
            for key, edge in edges.items():
                road_id = f"{record['source']}_{record['target']}_{key}"
                unchanged = (road.get('roadId') == road_id
                    and abs(float(edge.get('length', 0)) - float(road.get('lengthM', 0))) < 0.1
                    and abs(float(OSM_GRAPH.nodes[source]['y']) - source_coord[0]) < 1e-7
                    and abs(float(OSM_GRAPH.nodes[source]['x']) - source_coord[1]) < 1e-7
                    and abs(float(OSM_GRAPH.nodes[target]['y']) - target_coord[0]) < 1e-7
                    and abs(float(OSM_GRAPH.nodes[target]['x']) - target_coord[1]) < 1e-7)
                if unchanged and edge.get('geometry') is not None:
                    coordinates = [[float(lat), float(lon)] for lon, lat in edge['geometry'].coords]
                    distance = lambda point: sum((a-b)**2 for a,b in zip(point, source_coord))
                    if distance(coordinates[-1]) < distance(coordinates[0]):
                        coordinates.reverse()
                    break
        route_coordinates.extend(coordinates[1:] if route_coordinates else coordinates)
        name = normalize_osm_value(road.get('name')) or 'Đường không tên'
        length = round(float(road.get('lengthM', 0)), 2)
        if road_segments and road_segments[-1]['name'] == name:
            road_segments[-1]['lengthM'] = round(road_segments[-1]['lengthM'] + length, 2)
        else:
            road_segments.append(dict(name=name, highway=normalize_osm_value(road.get('highway')), lengthM=length))
    return route_coordinates, road_segments


PLACES_QUERY = """
MATCH
    (p:Place)
    -[:ACCESS_TO]->
    (:Junction)

RETURN DISTINCT
    p.placeId AS placeId,
    p.name AS name,
    p.category AS category

ORDER BY
    p.name
"""


# =========================================================
# TRANG CHỦ
# =========================================================

@app.route("/")
def index():

    return render_template(
        "index.html"
    )


# =========================================================
# API DANH SÁCH PLACE
# =========================================================

@app.route(
    "/api/places",
    methods=["GET"]
)
def get_places():

    try:

        with driver.session(
            database=NEO4J_DATABASE
        ) as session:

            result = session.run(
                PLACES_QUERY
            )

            places = []

            for record in result:

                places.append(
                    {
                        "placeId":
                            record[
                                "placeId"
                            ],

                        "name":
                            record[
                                "name"
                            ],

                        "category":
                            record[
                                "category"
                            ]
                    }
                )

        return jsonify(
            places
        )

    except Exception as error:

        print(
            "Lỗi API places:",
            error
        )

        return jsonify(
            {
                "error":
                    str(error)
            }
        ), 500


register_routing(app, driver, NEO4J_DATABASE, ensure_road_graph, build_route_geometry)
register_console(app, driver, NEO4J_DATABASE)
if __package__:
    from .profile_features import register_profile_features
else:
    from profile_features import register_profile_features
register_profile_features(app, driver, NEO4J_DATABASE)


# =========================================================
# CHẠY SERVER
# =========================================================

if __name__ == "__main__":

    try:

        # Kiểm tra kết nối Neo4j
        driver.verify_connectivity()

        print(
            "Kết nối Neo4j thành công."
        )


        # Đảm bảo graph GDS tồn tại
        ensure_road_graph()


        # Chạy Flask
        app.run(
            host="127.0.0.1",
            port=5000,
            debug=os.environ.get("FLASK_DEBUG") == "1",

            # Không reload để tránh
            # load GraphML 2 lần
            use_reloader=False
        )

    finally:

        driver.close()