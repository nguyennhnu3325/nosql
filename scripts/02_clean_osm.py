import ast
from pathlib import Path

import pandas as pd


# =========================================================
# 1. CẤU HÌNH ĐƯỜNG DẪN
# =========================================================

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

NODES_INPUT = DATA_DIR / "nodes.csv"
ROADS_INPUT = DATA_DIR / "roads.csv"

NODES_OUTPUT = DATA_DIR / "nodes_clean.csv"
ROADS_OUTPUT = DATA_DIR / "roads_clean.csv"


# =========================================================
# 2. HÀM HỖ TRỢ
# =========================================================

def clean_list_value(value):
    """
    Chuẩn hóa các giá trị OSM bị lưu dưới dạng chuỗi list.

    Ví dụ:
    "['Đường CN5', 'Đường CN3']"
        -> "Đường CN5 / Đường CN3"

    "['residential', 'tertiary']"
        -> "residential / tertiary"

    Giá trị thông thường được giữ nguyên.
    """

    if pd.isna(value):
        return ""

    value = str(value).strip()

    if value == "":
        return ""

    # Nếu chuỗi có dạng list Python
    if value.startswith("[") and value.endswith("]"):
        try:
            parsed = ast.literal_eval(value)

            if isinstance(parsed, list):
                # Loại bỏ phần tử rỗng và trùng
                cleaned_items = []

                for item in parsed:
                    item = str(item).strip()

                    if item and item not in cleaned_items:
                        cleaned_items.append(item)

                return " / ".join(cleaned_items)

        except (ValueError, SyntaxError):
            # Nếu parse không được thì giữ nguyên
            pass

    return value


def normalize_boolean(value):
    """
    Chuẩn hóa oneway về chuỗi true/false.
    """

    value = str(value).strip().lower()

    if value in ["true", "1", "yes"]:
        return "true"

    if value in ["false", "0", "no"]:
        return "false"

    return value


# =========================================================
# 3. ĐỌC DỮ LIỆU
# =========================================================

print("=" * 70)
print("ĐỌC DỮ LIỆU GỐC")
print("=" * 70)

nodes = pd.read_csv(
    NODES_INPUT,
    dtype={
        "junctionId": str
    }
)

roads = pd.read_csv(
    ROADS_INPUT,
    dtype={
        "roadId": str,
        "source": str,
        "target": str
    }
)

print(f"Số node ban đầu : {len(nodes)}")
print(f"Số road ban đầu : {len(roads)}")


# =========================================================
# 4. LÀM SẠCH NODE
# =========================================================

print()
print("=" * 70)
print("LÀM SẠCH NODE")
print("=" * 70)

# Chuẩn hóa ID
nodes["junctionId"] = nodes["junctionId"].astype(str).str.strip()

# Chuyển latitude/longitude về numeric
nodes["latitude"] = pd.to_numeric(
    nodes["latitude"],
    errors="coerce"
)

nodes["longitude"] = pd.to_numeric(
    nodes["longitude"],
    errors="coerce"
)

# Kiểm tra null
null_nodes = nodes[
    nodes["junctionId"].isna()
    | nodes["latitude"].isna()
    | nodes["longitude"].isna()
]

print("Node thiếu dữ liệu:", len(null_nodes))

# Loại node thiếu ID/tọa độ nếu có
nodes = nodes.dropna(
    subset=[
        "junctionId",
        "latitude",
        "longitude"
    ]
)

# Loại ID rỗng
nodes = nodes[
    nodes["junctionId"].str.strip() != ""
]

# Loại node trùng ID
duplicate_nodes = nodes.duplicated(
    subset=["junctionId"],
    keep="first"
)

print("Node trùng ID:", duplicate_nodes.sum())

nodes = nodes[
    ~duplicate_nodes
].copy()


# =========================================================
# 5. LÀM SẠCH ROAD
# =========================================================

print()
print("=" * 70)
print("LÀM SẠCH ROAD")
print("=" * 70)

# Chuẩn hóa ID
for column in ["roadId", "source", "target"]:

    roads[column] = (
        roads[column]
        .astype(str)
        .str.strip()
    )


# =========================================================
# 6. CHUẨN HÓA NAME VÀ HIGHWAY
# =========================================================

roads["name"] = roads["name"].apply(
    clean_list_value
)

roads["highway"] = roads["highway"].apply(
    clean_list_value
)

roads["oneway"] = roads["oneway"].apply(
    normalize_boolean
)


# =========================================================
# 7. CHUẨN HÓA LENGTH
# =========================================================

roads["lengthM"] = pd.to_numeric(
    roads["lengthM"],
    errors="coerce"
)

invalid_length = roads[
    roads["lengthM"].isna()
    | (roads["lengthM"] <= 0)
]

print(
    "ROAD có lengthM không hợp lệ:",
    len(invalid_length)
)

# Loại các ROAD không có trọng số hợp lệ
roads = roads[
    roads["lengthM"].notna()
    & (roads["lengthM"] > 0)
].copy()


# =========================================================
# 8. LOẠI SELF-LOOP
# =========================================================

self_loops = roads[
    roads["source"] == roads["target"]
]

print("Self-loop phát hiện:", len(self_loops))

if len(self_loops) > 0:

    print("\nDanh sách self-loop bị loại:")

    print(
        self_loops[
            [
                "roadId",
                "source",
                "target",
                "name",
                "lengthM"
            ]
        ].to_string(index=False)
    )

roads = roads[
    roads["source"] != roads["target"]
].copy()


# =========================================================
# 9. KIỂM TRA SOURCE/TARGET TỒN TẠI
# =========================================================

valid_node_ids = set(
    nodes["junctionId"]
)

invalid_source = roads[
    ~roads["source"].isin(valid_node_ids)
]

invalid_target = roads[
    ~roads["target"].isin(valid_node_ids)
]

print()
print(
    "ROAD có source không tồn tại:",
    len(invalid_source)
)

print(
    "ROAD có target không tồn tại:",
    len(invalid_target)
)

# Giữ lại ROAD có cả source và target hợp lệ
roads = roads[
    roads["source"].isin(valid_node_ids)
    & roads["target"].isin(valid_node_ids)
].copy()


# =========================================================
# 10. KIỂM TRA ROAD ID TRÙNG
# =========================================================

duplicate_road_ids = roads.duplicated(
    subset=["roadId"],
    keep="first"
)

print(
    "ROAD trùng roadId:",
    duplicate_road_ids.sum()
)

roads = roads[
    ~duplicate_road_ids
].copy()


# =========================================================
# 11. KIỂM TRA PARALLEL EDGE
# =========================================================

parallel_mask = roads.duplicated(
    subset=[
        "source",
        "target"
    ],
    keep=False
)

parallel_edges = roads[
    parallel_mask
]

parallel_pairs = (
    parallel_edges[
        ["source", "target"]
    ]
    .drop_duplicates()
)

print(
    "Số cặp source-target có parallel edge:",
    len(parallel_pairs)
)

print(
    "Số ROAD thuộc parallel edge:",
    len(parallel_edges)
)

print(
    "Lưu ý: parallel edge được GIỮ NGUYÊN."
)


# =========================================================
# 12. THỐNG KÊ TÊN ĐƯỜNG
# =========================================================

unnamed_roads = roads[
    roads["name"].fillna("").str.strip() == ""
]

print()
print(
    "ROAD không có tên:",
    len(unnamed_roads)
)

print(
    "ROAD một chiều:",
    (roads["oneway"] == "true").sum()
)

print(
    "ROAD hai chiều / không đánh dấu one-way:",
    (roads["oneway"] == "false").sum()
)


# =========================================================
# 13. SẮP XẾP CỘT
# =========================================================

nodes = nodes[
    [
        "junctionId",
        "latitude",
        "longitude"
    ]
]

roads = roads[
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


# =========================================================
# 14. XUẤT FILE SẠCH
# =========================================================

nodes.to_csv(
    NODES_OUTPUT,
    index=False,
    encoding="utf-8-sig"
)

roads.to_csv(
    ROADS_OUTPUT,
    index=False,
    encoding="utf-8-sig"
)


# =========================================================
# 15. THỐNG KÊ KẾT QUẢ CUỐI
# =========================================================

print()
print("=" * 70)
print("KẾT QUẢ SAU KHI LÀM SẠCH")
print("=" * 70)

print(
    f"Node trước : {len(pd.read_csv(NODES_INPUT))}"
)

print(
    f"Node sau   : {len(nodes)}"
)

print()

print(
    f"ROAD trước : {len(pd.read_csv(ROADS_INPUT))}"
)

print(
    f"ROAD sau   : {len(roads)}"
)

print()

print(
    "File node sạch:",
    NODES_OUTPUT
)

print(
    "File road sạch:",
    ROADS_OUTPUT
)

print()
print("LÀM SẠCH DỮ LIỆU HOÀN TẤT.")