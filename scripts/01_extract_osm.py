import osmnx as ox
from pathlib import Path


# =========================================================
# 1. CẤU HÌNH THƯ MỤC
# =========================================================

# Thư mục gốc của project Neo4jRouting
BASE_DIR = Path(__file__).resolve().parent.parent

# Thư mục lưu dữ liệu
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


# =========================================================
# 2. CẤU HÌNH KHU VỰC NGHIÊN CỨU
# =========================================================

# Tọa độ khu vực Trường Đại học Công Thương TP.HCM - HUIT
# Format: (latitude, longitude)
CENTER_POINT = (
    10.806527,
    106.628862
)

# Bán kính lấy mạng đường, đơn vị mét
DISTANCE = 2000


# =========================================================
# 3. CẤU HÌNH OSMNX
# =========================================================

# Lưu cache để lần chạy sau không phải tải lại nếu có thể
ox.settings.use_cache = True

# Hiển thị log trong terminal
ox.settings.log_console = True

# Tăng timeout cho request
ox.settings.requests_timeout = 180


# =========================================================
# 4. TẢI MẠNG ĐƯỜNG TỪ OPENSTREETMAP
# =========================================================

print("=" * 60)
print("ĐANG TẢI MẠNG ĐƯỜNG TỪ OPENSTREETMAP")
print("=" * 60)

print(f"Tâm khu vực: {CENTER_POINT}")
print(f"Bán kính: {DISTANCE} m")
print("Loại mạng: drive")
print()

try:
    G = ox.graph.graph_from_point(
        CENTER_POINT,
        dist=DISTANCE,
        network_type="drive",
        simplify=True
    )

except Exception as error:
    print("\nKhông thể tải dữ liệu OpenStreetMap.")
    print("Lỗi:")
    print(error)

    print(
        "\nNếu lỗi có chữ 'overpass' hoặc 'connection', "
        "hãy kiểm tra Internet/VPN/firewall."
    )

    raise SystemExit(1)


# =========================================================
# 5. THỐNG KÊ GRAPH
# =========================================================

print()
print("=" * 60)
print("THỐNG KÊ MẠNG ĐƯỜNG")
print("=" * 60)

print("Số node:", len(G.nodes))
print("Số edge:", len(G.edges))


# =========================================================
# 6. LƯU GRAPH GỐC
# =========================================================

graphml_path = DATA_DIR / "road_network.graphml"

ox.io.save_graphml(
    G,
    filepath=graphml_path
)

print(f"\nĐã lưu GraphML: {graphml_path}")


# =========================================================
# 7. CHUYỂN GRAPH THÀNH GEODATAFRAME
# =========================================================

nodes, edges = ox.convert.graph_to_gdfs(G)


# =========================================================
# 8. XỬ LÝ NODE
# =========================================================

nodes_df = nodes.reset_index()

nodes_df = nodes_df.rename(
    columns={
        "osmid": "junctionId",
        "y": "latitude",
        "x": "longitude"
    }
)

# Chuyển ID thành chuỗi để sau này import Neo4j dễ hơn
nodes_df["junctionId"] = nodes_df["junctionId"].astype(str)

nodes_output = nodes_df[
    [
        "junctionId",
        "latitude",
        "longitude"
    ]
]

nodes_path = DATA_DIR / "nodes.csv"

nodes_output.to_csv(
    nodes_path,
    index=False,
    encoding="utf-8-sig"
)


# =========================================================
# 9. XỬ LÝ ROAD / EDGE
# =========================================================

edges_df = edges.reset_index()

# Tạo ID duy nhất cho từng cạnh
edges_df["roadId"] = (
    edges_df["u"].astype(str)
    + "_"
    + edges_df["v"].astype(str)
    + "_"
    + edges_df["key"].astype(str)
)

# Node đầu
edges_df["source"] = edges_df["u"].astype(str)

# Node cuối
edges_df["target"] = edges_df["v"].astype(str)

# Chiều dài đoạn đường
edges_df["lengthM"] = edges_df["length"].round(2)


# =========================================================
# 10. XỬ LÝ CÁC THUỘC TÍNH CÓ THỂ BỊ THIẾU
# =========================================================

for column in ["name", "highway", "oneway"]:

    if column not in edges_df.columns:
        edges_df[column] = ""

    edges_df[column] = (
        edges_df[column]
        .fillna("")
        .astype(str)
    )


# =========================================================
# 11. XUẤT ROAD CSV
# =========================================================

roads_output = edges_df[
    [
        "roadId",
        "source",
        "target",
        "name",
        "highway",
        "oneway",
        "lengthM"
    ]
]

roads_path = DATA_DIR / "roads.csv"

roads_output.to_csv(
    roads_path,
    index=False,
    encoding="utf-8-sig"
)


# =========================================================
# 12. THỐNG KÊ FILE KẾT QUẢ
# =========================================================

print()
print("=" * 60)
print("XUẤT DỮ LIỆU THÀNH CÔNG")
print("=" * 60)

print(f"nodes.csv : {nodes_path}")
print(f"roads.csv : {roads_path}")
print(f"GraphML   : {graphml_path}")

print()
print("Số Junction:", len(nodes_output))
print("Số ROAD:", len(roads_output))

print()
print("Hoàn thành!")