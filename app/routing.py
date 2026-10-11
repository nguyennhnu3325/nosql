"""Dijkstra shortest route and Yen alternative routes executed by Neo4j GDS."""
import math
from flask import Blueprint, jsonify, request

try:
    from .auth import AppError
except ImportError:
    from auth import AppError

ENDPOINTS_QUERY = """
MATCH (start:Place {placeId: $startId})-[sa:ACCESS_TO]->(source:Junction)
MATCH (end:Place {placeId: $endId})-[ea:ACCESS_TO]->(target:Junction)
RETURN start { .placeId, .name, .latitude, .longitude } AS startPlace,
       end { .placeId, .name, .latitude, .longitude } AS endPlace,
       sa.accessDistanceM AS startAccessM, ea.accessDistanceM AS endAccessM,
       source.junctionId AS sourceId, target.junctionId AS targetId
LIMIT 1
"""

PATH_PREFIX = """
MATCH (source:Junction {junctionId: $sourceId}), (target:Junction {junctionId: $targetId})
"""
PATH_RESULT = """
YIELD totalCost, nodeIds, costs
RETURN totalCost AS roadDistanceM, costs,
       [nodeId IN nodeIds | gds.util.asNode(nodeId).junctionId] AS routeNodeIds
"""
DIJKSTRA_QUERY = PATH_PREFIX + """
CALL gds.shortestPath.dijkstra.stream($graphName, {
    sourceNode: source, targetNodes: target, relationshipWeightProperty: 'lengthM'
})
""" + PATH_RESULT
YEN_QUERY = PATH_PREFIX + """
CALL gds.shortestPath.yens.stream($graphName, {
    sourceNode: source, targetNode: target, k: $k, relationshipWeightProperty: 'lengthM'
})
""" + PATH_RESULT + ' ORDER BY roadDistanceM'


def search_paths(session, endpoints, alternative_count=0, graph_name='roadGraph'):
    """Keep the Dijkstra result first; discard duplicate junction sequences."""
    if endpoints['sourceId'] == endpoints['targetId']:
        return [dict(roadDistanceM=0, costs=[0], routeNodeIds=[endpoints['sourceId']], algorithm='Dijkstra')]
    params = dict(sourceId=endpoints['sourceId'], targetId=endpoints['targetId'], graphName=graph_name)
    record = session.run(DIJKSTRA_QUERY, **params).single()
    if not record or not record['routeNodeIds'] or not math.isfinite(record['roadDistanceM']):
        return []
    primary = dict(record, algorithm='Dijkstra')
    paths, seen = [primary], {tuple(primary['routeNodeIds'])}
    if alternative_count:
        # Examine several candidates because parallel ROADs can share the same node sequence.
        candidates = session.run(YEN_QUERY, **params, k=10)
        for record in candidates:
            nodes = record['routeNodeIds']
            signature = tuple(nodes)
            if not nodes or signature in seen or not math.isfinite(record['roadDistanceM']):
                continue
            seen.add(signature)
            paths.append(dict(record, algorithm='Yen'))
            if len(paths) >= alternative_count + 1:
                break
        candidates.consume()
    return paths


def serialize_path(endpoints, path, geometry, index):
    nodes = path['routeNodeIds']
    coordinates, roads = geometry(nodes, path.get('costs'))
    start_access = float(endpoints['startAccessM'] or 0)
    end_access = float(endpoints['endAccessM'] or 0)
    road_distance = float(path['roadDistanceM'])
    total = start_access + road_distance + end_access
    return dict(routeId=index, label='Tuyến ngắn nhất' if index == 0 else f'Tuyến thay thế {index}',
                algorithm=path['algorithm'], startPlace=endpoints['startPlace'], endPlace=endpoints['endPlace'],
                startAccessM=round(start_access, 2), endAccessM=round(end_access, 2),
                roadDistanceM=round(road_distance, 2), totalDistanceM=round(total, 2),
                totalDistanceKm=round(total / 1000, 3), routeNodeCount=len(nodes),
                routeNodeIds=nodes, routeCoordinates=coordinates, routeRoads=roads)


def register_routing(app, driver, database, ensure_graph, geometry):
    routing = Blueprint('routing', __name__)

    @routing.post('/api/route')
    def find_route():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise AppError('Dữ liệu tìm đường phải là đối tượng JSON.')
        start, end = data.get('startId'), data.get('endId')
        if not isinstance(start, str) or not isinstance(end, str) or not start.strip() or not end.strip():
            raise AppError('Hãy chọn điểm đi và điểm đến.')
        if start == end:
            raise AppError('Điểm đi và điểm đến phải khác nhau.')
        count = data.get('alternativeCount', 0)
        if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 2:
            raise AppError('Số tuyến thay thế phải là 0, 1 hoặc 2.')
        with driver.session(database=database) as session:
            record = session.run(ENDPOINTS_QUERY, startId=start, endId=end).single()
            if record is None:
                raise AppError('Không tìm thấy địa điểm hoặc địa điểm chưa nối vào mạng đường.', 404)
            endpoints = dict(record)
        if endpoints['sourceId'] != endpoints['targetId']:
            ensure_graph()
        with driver.session(database=database) as session:
            paths = search_paths(session, endpoints, count)
        if not paths:
            raise AppError('Không tìm thấy tuyến đường theo hướng di chuyển giữa hai địa điểm.', 404)
        try:
            routes = [serialize_path(endpoints, path, geometry, index) for index, path in enumerate(paths)]
        except ValueError:
            raise AppError('Mạng đường đã thay đổi. Hãy tìm lại tuyến đường.', 409)
        # Retain the original top-level response fields for existing clients.
        result = dict(routes[0], routes=routes, requestedAlternativeCount=count,
                      alternativeCount=len(routes) - 1)
        result['alternativeMessage'] = (f'Tìm được {len(routes)-1} tuyến thay thế.' if len(routes) > 1
            else ('Không tìm thấy tuyến thay thế khác trong các tuyến được xét.' if count else ''))
        return jsonify(result)

    app.register_blueprint(routing)
