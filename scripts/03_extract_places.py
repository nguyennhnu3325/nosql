from pathlib import Path

import osmnx as ox
import pandas as pd


# =========================================================
# 1. ĐƯỜNG DẪN
# =========================================================

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_FILE = DATA_DIR / "places.csv"


# =========================================================
# 2. KHU VỰC NGHIÊN CỨU
# =========================================================

# HUIT - 140 Lê Trọng Tấn
CENTER_POINT = (
    10.806527,
    106.628862
)

DISTANCE = 2000

MAX_PLACES = 50


# =========================================================
# 3. CÁC LOẠI POI CẦN LẤY
# =========================================================

tags = {
    "amenity": [
        "university",
        "college",
        "school",
        "kindergarten",
        "hospital",
        "clinic",
        "pharmacy",
        "bank",
        "atm",
        "marketplace",
        "restaurant",
        "cafe",
        "fast_food",
        "police",
        "post_office",
        "fuel",
        "parking"
    ],

    "shop": [
        "supermarket",
        "mall",
        "convenience",
        "bakery",
        "clothes",
        "electronics",
        "mobile_phone",
        "books"
    ],

    "leisure": [
        "park",
        "sports_centre",
        "fitness_centre",
        "playground"
    ],

    "tourism": [
        "attraction",
        "hotel"
    ]
}


# =========================================================
# 4. TẢI POI
# =========================================================

print("=" * 70)
print("ĐANG TẢI POI TỪ OPENSTREETMAP")
print("=" * 70)

pois = ox.features.features_from_point(
    CENTER_POINT,
    tags=tags,
    dist=DISTANCE
)

print("Tổng đối tượng lấy được:", len(pois))


# =========================================================
# 5. CHỈ GIỮ POI CÓ TÊN
# =========================================================

if "name" not in pois.columns:
    print("Không tìm thấy thuộc tính name.")
    raise SystemExit(1)

pois = pois[
    pois["name"].notna()
].copy()

pois["name"] = (
    pois["name"]
    .astype(str)
    .str.strip()
)

pois = pois[
    pois["name"] != ""
].copy()


# =========================================================
# 6. LẤY TỌA ĐỘ ĐẠI DIỆN
# =========================================================

pois["representative_point"] = (
    pois.geometry.representative_point()
)

pois["longitude"] = (
    pois["representative_point"].x
)

pois["latitude"] = (
    pois["representative_point"].y
)


# =========================================================
# 7. XÁC ĐỊNH CATEGORY
# =========================================================

def get_category(row):

    amenity = row.get("amenity")

    if pd.notna(amenity):
        return str(amenity)

    shop = row.get("shop")

    if pd.notna(shop):
        return str(shop)

    leisure = row.get("leisure")

    if pd.notna(leisure):
        return str(leisure)

    tourism = row.get("tourism")

    if pd.notna(tourism):
        return str(tourism)

    return "other"


pois["category"] = pois.apply(
    get_category,
    axis=1
)


# =========================================================
# 8. CHỈ GIỮ CỘT CẦN THIẾT
# =========================================================

places = pois[
    [
        "name",
        "category",
        "latitude",
        "longitude"
    ]
].copy()


# =========================================================
# 9. LOẠI DUPLICATE
# =========================================================

places = places.drop_duplicates(
    subset=[
        "name",
        "latitude",
        "longitude"
    ]
)

places = places.reset_index(drop=True)


# =========================================================
# 10. LOẠI HUIT TỪ OSM NẾU ĐÃ CÓ
#     VÌ TA SẼ THÊM HUIT THỦ CÔNG LÀ P001
# =========================================================

places = places[
    ~places["name"]
    .str.lower()
    .str.contains(
        "đại học công thương|huit|cong thuong",
        regex=True,
        na=False
    )
].copy()


# =========================================================
# 11. CHỌN POI ĐA DẠNG THEO CATEGORY
# =========================================================

selected_rows = []

grouped = {
    category: group.reset_index(drop=True)
    for category, group
    in places.groupby("category")
}

categories = list(grouped.keys())

index = 0

while len(selected_rows) < (MAX_PLACES - 1):

    added = False

    for category in categories:

        group = grouped[category]

        if index < len(group):

            selected_rows.append(
                group.iloc[index]
            )

            added = True

            if len(selected_rows) >= (MAX_PLACES - 1):
                break

    if not added:
        break

    index += 1


if selected_rows:

    selected = pd.DataFrame(
        selected_rows
    )

else:

    selected = pd.DataFrame(
        columns=[
            "name",
            "category",
            "latitude",
            "longitude"
        ]
    )


# =========================================================
# 12. THÊM HUIT LÀ ĐỊA ĐIỂM ĐẦU TIÊN
# =========================================================

huit = pd.DataFrame(
    [
        {
            "name": "HUIT",
            "category": "university",
            "latitude": 10.806527,
            "longitude": 106.628862
        }
    ]
)

places_final = pd.concat(
    [
        huit,
        selected
    ],
    ignore_index=True
)


# =========================================================
# 13. GIỚI HẠN ĐÚNG 50 PLACE
# =========================================================

places_final = places_final.head(
    MAX_PLACES
).copy()


# =========================================================
# 14. TẠO placeId
# =========================================================

places_final["placeId"] = [
    f"P{i:03d}"
    for i in range(
        1,
        len(places_final) + 1
    )
]

places_final = places_final[
    [
        "placeId",
        "name",
        "category",
        "latitude",
        "longitude"
    ]
]


# =========================================================
# 15. XUẤT CSV
# =========================================================

places_final.to_csv(
    OUTPUT_FILE,
    index=False,
    encoding="utf-8-sig"
)


# =========================================================
# 16. THỐNG KÊ
# =========================================================

print()
print("=" * 70)
print("KẾT QUẢ")
print("=" * 70)

print(
    "Số Place:",
    len(places_final)
)

print()

print(
    places_final[
        [
            "placeId",
            "name",
            "category"
        ]
    ].to_string(
        index=False
    )
)

print()
print("Phân bố category:")

print(
    places_final[
        "category"
    ].value_counts()
)

print()
print(
    "Đã tạo:",
    OUTPUT_FILE
)