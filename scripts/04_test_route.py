from neo4j import GraphDatabase


# =========================================================
# 1. CẤU HÌNH NEO4J
# =========================================================

URI = "bolt://localhost:7687"
USER = "neo4j"

# ĐỔI thành password UrbanRouteDB của m
PASSWORD = "12345678"

DATABASE = "neo4j"


# =========================================================
# 2. QUERY DIJKSTRA
# =========================================================

QUERY = """
MATCH
(startPlace:Place {placeId: $startId})
-[startAccess:ACCESS_TO]->
(source:Junction)

MATCH
(endPlace:Place {placeId: $endId})
-[endAccess:ACCESS_TO]->
(target:Junction)

CALL gds.shortestPath.dijkstra.stream(
    'roadGraph',
    {
        sourceNode: source,
        targetNodes: target,
        relationshipWeightProperty: 'lengthM'
    }
)

YIELD
    totalCost,
    nodeIds

RETURN
    startPlace.name AS diemDi,
    endPlace.name AS diemDen,

    round(totalCost, 2)
        AS khoangCachMangDuong,

    round(
        startAccess.accessDistanceM
        + totalCost
        + endAccess.accessDistanceM,
        2
    ) AS tongKhoangCachMet,

    [
        nodeId IN nodeIds |
        {
            junctionId:
                gds.util.asNode(nodeId).junctionId,

            latitude:
                gds.util.asNode(nodeId).latitude,

            longitude:
                gds.util.asNode(nodeId).longitude
        }
    ] AS routePoints
"""


# =========================================================
# 3. KẾT NỐI NEO4J
# =========================================================

driver = GraphDatabase.driver(
    URI,
    auth=(USER, PASSWORD)
)


# =========================================================
# 4. TEST ROUTE
# =========================================================

START_PLACE = "P001"
END_PLACE = "P010"


try:

    with driver.session(database=DATABASE) as session:

        result = session.run(
            QUERY,
            startId=START_PLACE,
            endId=END_PLACE
        )

        record = result.single()

        if record is None:
            print("Không tìm thấy tuyến đường.")
            raise SystemExit(1)

        print("=" * 70)
        print("KẾT QUẢ DIJKSTRA")
        print("=" * 70)

        print(
            "Điểm đi:",
            record["diemDi"]
        )

        print(
            "Điểm đến:",
            record["diemDen"]
        )

        print(
            "Khoảng cách mạng đường:",
            record["khoangCachMangDuong"],
            "m"
        )

        print(
            "Tổng khoảng cách:",
            record["tongKhoangCachMet"],
            "m"
        )

        print()

        route_points = record["routePoints"]

        print(
            "Số Junction trên tuyến:",
            len(route_points)
        )

        print()
        print("Các điểm trên tuyến:")

        for index, point in enumerate(
            route_points,
            start=1
        ):

            print(
                index,
                point["junctionId"],
                point["latitude"],
                point["longitude"]
            )


except Exception as error:

    print()
    print("LỖI:")
    print(error)


finally:

    driver.close()