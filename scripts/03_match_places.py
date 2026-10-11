from pathlib import Path

import osmnx as ox
import pandas as pd


# =========================================================
# 1. ĐƯỜNG DẪN
# =========================================================

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

GRAPH_FILE = DATA_DIR / "road_network.graphml"
PLACES_FILE = DATA_DIR / "places.csv"
OUTPUT_FILE = DATA_DIR / "places_with_junction.csv"


# =========================================================
# 2. KIỂM TRA FILE
# =========================================================

if not GRAPH_FILE.exists():
    print(f"Không tìm thấy graph: {GRAPH_FILE}")
    raise SystemExit(1)

if not PLACES_FILE.exists():
    print(f"Không tìm thấy places.csv: {PLACES_FILE}")
    raise SystemExit(1)

if PLACES_FILE.stat().st_size == 0:
    print("File places.csv đang rỗng.")
    raise SystemExit(1)


# =========================================================
# 3. ĐỌC ROAD NETWORK
# =========================================================

print("=" * 60)
print("ĐỌC ROAD NETWORK")
print("=" * 60)

G = ox.io.load_graphml(GRAPH_FILE)

print("Số node:", len(G.nodes))
print("Số edge:", len(G.edges))


# =========================================================
# 4. ĐỌC PLACE
# =========================================================

places = pd.read_csv(PLACES_FILE)

required_columns = {
    "placeId",
    "name",
    "category",
    "latitude",
    "longitude"
}

missing_columns = required_columns - set(places.columns)

if missing_columns:
    print("Thiếu các cột:", missing_columns)
    raise SystemExit(1)

print()
print("Số địa điểm:", len(places))


# =========================================================
# 5. TÌM JUNCTION GẦN NHẤT
# =========================================================

junction_ids = []
distances = []

for _, place in places.iterrows():

    longitude = float(place["longitude"])
    latitude = float(place["latitude"])

    nearest_node = ox.distance.nearest_nodes(
        G,
        X=longitude,
        Y=latitude
    )

    node_data = G.nodes[nearest_node]

    node_lon = float(node_data["x"])
    node_lat = float(node_data["y"])

    distance = ox.distance.great_circle(
        latitude,
        longitude,
        node_lat,
        node_lon
    )

    junction_ids.append(str(nearest_node))
    distances.append(round(float(distance), 2))


# =========================================================
# 6. GẮN KẾT QUẢ
# =========================================================

places["junctionId"] = junction_ids
places["accessDistanceM"] = distances


# =========================================================
# 7. XUẤT FILE
# =========================================================

places.to_csv(
    OUTPUT_FILE,
    index=False,
    encoding="utf-8-sig"
)


# =========================================================
# 8. HIỂN THỊ KẾT QUẢ
# =========================================================

print()
print("=" * 60)
print("KẾT QUẢ")
print("=" * 60)

print(
    places[
        [
            "placeId",
            "name",
            "junctionId",
            "accessDistanceM"
        ]
    ].to_string(index=False)
)

print()
print("Đã tạo:")
print(OUTPUT_FILE)