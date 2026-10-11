"""Traffic editing, JSON exchange, logical backups and network analysis."""
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4

from flask import Blueprint, current_app, jsonify, render_template, request, send_file

try:
    from .auth import AppError
    from .place_admin import PlaceError, validate_place
except ImportError:
    from auth import AppError
    from place_admin import PlaceError, validate_place

FORMAT = 'neo4j-routing-traffic-v1'


def number(value, label, low=None, high=None):
    if isinstance(value, bool) or value is None:
        raise AppError(label + ' không hợp lệ.')
    try:
        value = float(value)
    except (ValueError, TypeError):
        raise AppError(label + ' phải là số.')
    if not math.isfinite(value) or (low is not None and value < low) or (high is not None and value > high):
        raise AppError(label + ' ngoài phạm vi cho phép.')
    return value


def junction_id(value):
    if isinstance(value, bool) or not re.fullmatch(r'[0-9]{1,18}', str(value)) or int(value) <= 0:
        raise AppError('Mã Junction phải là số nguyên dương tối đa 18 chữ số.')
    return int(value)


def properties(data):
    if not isinstance(data, dict):
        raise AppError('Thuộc tính phải là đối tượng JSON.')
    for key, value in data.items():
        if not isinstance(key, str) or len(key) > 100:
            raise AppError('Tên thuộc tính không hợp lệ.')
        items = value if isinstance(value, list) else [value]
        if any(not isinstance(v, (str, int, float, bool)) or (isinstance(v, float) and not math.isfinite(v)) for v in items):
            raise AppError('Thuộc tính chỉ chứa chuỗi, số, boolean hoặc danh sách giá trị.')
        if isinstance(value, list) and len({type(v) for v in items}) > 1:
            raise AppError('Danh sách thuộc tính phải có cùng kiểu dữ liệu.')
        if any(isinstance(v, int) and not -(2**63) <= v < 2**63 for v in items):
            raise AppError('Giá trị nguyên vượt giới hạn Neo4j.')
    return dict(data)


def validate_junction(data):
    p = properties(data)
    p.update(junctionId=junction_id(p.get('junctionId')),
             latitude=number(p.get('latitude'), 'Vĩ độ', -90, 90),
             longitude=number(p.get('longitude'), 'Kinh độ', -180, 180))
    return p


def validate_road(data):
    if not isinstance(data, dict):
        raise AppError('Dữ liệu đường không hợp lệ.')
    p = properties(data.get('properties'))
    road_id = p.get('roadId')
    if not isinstance(road_id, str) or not 1 <= len(road_id) <= 200:
        raise AppError('Mã đường phải có từ 1 đến 200 ký tự.')
    p['lengthM'] = number(p.get('lengthM'), 'Chiều dài đường', 0)
    for field in ('name', 'highway'):
        if field in p and (not isinstance(p[field], str) or len(p[field]) > 200):
            raise AppError('Tên đường và loại đường tối đa 200 ký tự.')
    return dict(source=junction_id(data.get('source')), target=junction_id(data.get('target')), properties=p)


def validate_snapshot(data):
    if not isinstance(data, dict) or data.get('format') != FORMAT:
        raise AppError('Tệp không đúng định dạng neo4j-routing-traffic-v1.')
    for key in ('junctions', 'places', 'roads', 'access'):
        if not isinstance(data.get(key), list):
            raise AppError('Thiếu danh sách ' + key)
    result = dict(format=FORMAT, junctions=[], places=[], roads=[], access=[])
    ids, place_ids, road_ids = set(), set(), set()
    for row in data['junctions']:
        p = validate_junction(row)
        if p['junctionId'] in ids:
            raise AppError('Trùng mã Junction trong tệp.')
        ids.add(p['junctionId'])
        result['junctions'].append(p)
    for row in data['places']:
        p = properties(row)
        pid = p.get('placeId')
        if not isinstance(pid, str) or not 1 <= len(pid) <= 200 or pid in place_ids:
            raise AppError('Mã Place không hợp lệ hoặc bị trùng.')
        try:
            p.update(validate_place(p))
        except PlaceError as error:
            raise AppError(str(error))
        place_ids.add(pid)
        result['places'].append(p)
    for row in data['roads']:
        road = validate_road(row)
        rid = road['properties']['roadId']
        if rid in road_ids or road['source'] not in ids or road['target'] not in ids:
            raise AppError('Đường bị trùng mã hoặc tham chiếu Junction không có trong tệp.')
        road_ids.add(rid)
        result['roads'].append(road)
    access_places = set()
    for row in data['access']:
        if not isinstance(row, dict):
            raise AppError('ACCESS_TO không hợp lệ.')
        pid, jid = row.get('placeId'), junction_id(row.get('junctionId'))
        if not isinstance(pid, str) or pid not in place_ids or jid not in ids or pid in access_places:
            raise AppError('ACCESS_TO bị trùng hoặc tham chiếu địa điểm/Junction không tồn tại.')
        p = properties(row.get('properties'))
        p['accessDistanceM'] = number(p.get('accessDistanceM'), 'Khoảng cách truy cập', 0)
        access_places.add(pid)
        result['access'].append(dict(placeId=pid, junctionId=jid, properties=p))
    if place_ids and not ids:
        raise AppError('Không thể có Place khi mạng Junction rỗng.')
    return result


def relink_places(tx):
    counts = tx.run('MATCH (p:Place) RETURN count(p) AS n').single()['n']
    junctions = tx.run('MATCH (j:Junction) RETURN count(j) AS n').single()['n']
    if counts and not junctions:
        raise AppError('Không thể xóa Junction cuối cùng khi còn địa điểm.', 409)
    tx.run("""
        MATCH (p:Place)
        CALL (p) {
            MATCH (j:Junction)
            WITH j, point.distance(point({latitude: p.latitude, longitude: p.longitude}),
                point({latitude: j.latitude, longitude: j.longitude})) AS distance
            RETURN j, distance ORDER BY distance, j.junctionId LIMIT 1
        }
        OPTIONAL MATCH (p)-[old:ACCESS_TO]-()
        DELETE old
        WITH DISTINCT p, j, distance
        CREATE (p)-[:ACCESS_TO {accessDistanceM: round(distance * 100) / 100.0}]->(j)
    """).consume()


def write_snapshot(tx, data, replace=False):
    if replace:
        # Auth nodes are deliberately outside the traffic backup/restore scope.
        # Reconcile nodes by stable IDs rather than deleting/recreating retained nodes.
        tx.run('MATCH (:Junction)-[r:ROAD]->(:Junction) DELETE r').consume()
        tx.run('MATCH (:Place)-[a:ACCESS_TO]-() DELETE a').consume()
        tx.run('MATCH (j:Junction) WHERE NOT j.junctionId IN $ids DETACH DELETE j',
               ids=[j['junctionId'] for j in data['junctions']]).consume()
        tx.run('MATCH (p:Place) WHERE NOT p.placeId IN $ids DETACH DELETE p',
               ids=[p['placeId'] for p in data['places']]).consume()
    tx.run('UNWIND $rows AS row MERGE (j:Junction {junctionId: row.junctionId}) SET j = row', rows=data['junctions']).consume()
    tx.run('UNWIND $rows AS row MERGE (p:Place {placeId: row.placeId}) SET p = row', rows=data['places']).consume()
    tx.run('UNWIND $rows AS row MATCH ()-[r:ROAD {roadId: row.properties.roadId}]->() DELETE r', rows=data['roads']).consume()
    tx.run("""
        UNWIND $rows AS row
        MATCH (s:Junction {junctionId: row.source}), (t:Junction {junctionId: row.target})
        CREATE (s)-[r:ROAD]->(t) SET r = row.properties
    """, rows=data['roads']).consume()
    if replace:
        tx.run("""
            UNWIND $rows AS row
            MATCH (p:Place {placeId: row.placeId}), (j:Junction {junctionId: row.junctionId})
            CREATE (p)-[a:ACCESS_TO]->(j) SET a = row.properties
        """, rows=data['access']).consume()
    else:
        relink_places(tx)


def snapshot(tx):
    result = dict(format=FORMAT, createdAt=datetime.now(timezone.utc).isoformat())
    result['junctions'] = [dict(r['p']) for r in tx.run('MATCH (j:Junction) RETURN properties(j) AS p ORDER BY j.junctionId')]
    result['places'] = [dict(r['p']) for r in tx.run('MATCH (p:Place) RETURN properties(p) AS p ORDER BY p.placeId')]
    result['roads'] = [dict(r) for r in tx.run('MATCH (s:Junction)-[r:ROAD]->(t:Junction) RETURN s.junctionId AS source, t.junctionId AS target, properties(r) AS properties ORDER BY r.roadId')]
    result['access'] = [dict(r) for r in tx.run('MATCH (p:Place)-[a:ACCESS_TO]->(j:Junction) RETURN p.placeId AS placeId, j.junctionId AS junctionId, properties(a) AS properties ORDER BY p.placeId')]
    return result


def register_traffic(app, driver, database):
    api = Blueprint('traffic', __name__)
    lock = RLock()
    app.extensions['traffic_lock'] = lock
    app.extensions['road_graph_dirty'] = True
    backup_dir = Path(app.root_path).parent / 'backups'
    backup_dir.mkdir(exist_ok=True)

    def read(fn):
        with driver.session(database=database) as db:
            return db.execute_read(fn)

    def write(fn, *args):
        with driver.session(database=database) as db:
            result = db.execute_write(fn, *args)
        app.extensions['road_graph_dirty'] = True
        return result

    @app.before_request
    def traffic_lock():
        # Routing and traffic writes share a lock so GDS cannot be used mid-refresh.
        if request.path == '/api/route' or request.path.startswith('/api/admin/traffic') or request.path.startswith('/api/admin/places') or request.path in ('/api/network', '/api/analysis'):
            from flask import g
            lock.acquire()
            g.traffic_locked = True

    @app.teardown_request
    def traffic_unlock(error):
        from flask import g
        if getattr(g, 'traffic_locked', False):
            g.traffic_locked = False
            lock.release()

    @api.get('/network')
    @api.get('/analysis')
    def network_page():
        return render_template('network.html', analysis=request.path == '/analysis')

    @api.get('/admin/traffic')
    def data_page():
        return render_template('traffic.html')

    @api.get('/api/network')
    def network_data():
        def query(tx):
            junctions = [dict(r) for r in tx.run('MATCH (j:Junction) RETURN toString(j.junctionId) AS junctionId, j.latitude AS latitude, j.longitude AS longitude ORDER BY j.junctionId')]
            roads = [dict(r) for r in tx.run('MATCH (s:Junction)-[r:ROAD]->(t:Junction) RETURN r.roadId AS roadId, r.name AS name, r.highway AS highway, r.lengthM AS lengthM, toString(s.junctionId) AS source, toString(t.junctionId) AS target ORDER BY r.roadId')]
            return dict(junctions=junctions, roads=roads)
        return jsonify(read(query))

    @api.get('/api/analysis')
    def analysis_data():
        def query(tx):
            nodes = [r['id'] for r in tx.run('MATCH (j:Junction) RETURN j.junctionId AS id')]
            roads = [dict(r) for r in tx.run('MATCH (s:Junction)-[r:ROAD]->(t:Junction) RETURN s.junctionId AS source, t.junctionId AS target, r.highway AS highway, r.lengthM AS lengthM')]
            categories = [dict(r) for r in tx.run('MATCH (p:Place) RETURN coalesce(p.category, "Chưa phân loại") AS category, count(*) AS count ORDER BY count DESC')]
            return nodes, roads, categories
        nodes, roads, categories = read(query)
        adjacency = {n: set() for n in nodes}
        degrees = Counter()
        kinds = Counter()
        total = 0
        for r in roads:
            adjacency[r['source']].add(r['target'])
            adjacency[r['target']].add(r['source'])
            degrees.update([r['source'], r['target']])
            kinds[str(r['highway'] or 'Chưa phân loại')] += 1
            total += float(r['lengthM'] or 0)
        unseen, components = set(nodes), []
        while unseen:
            stack, size = [unseen.pop()], 0
            while stack:
                node = stack.pop()
                size += 1
                adjacent = adjacency[node] & unseen
                unseen.difference_update(adjacent)
                stack.extend(adjacent)
            components.append(size)
        return jsonify(junctionCount=len(nodes), roadCount=len(roads), placeCount=sum(r['count'] for r in categories),
                       totalDirectedLengthM=round(total, 2), averageLengthM=round(total / len(roads), 2) if roads else 0,
                       weakComponentCount=len(components), largestComponent=max(components, default=0),
                       isolatedJunctionCount=sum(not neighbors for neighbors in adjacency.values()),
                       topJunctions=[dict(junctionId=str(k), degree=v) for k, v in degrees.most_common(10)],
                       roadTypes=[dict(type=k, count=v) for k, v in kinds.most_common()], categories=categories)

    @api.get('/api/admin/traffic/export')
    def export_data():
        data = read(snapshot)
        response = jsonify(data)
        response.headers['Content-Disposition'] = 'attachment; filename="traffic-export.json"'
        return response

    def uploaded():
        file = request.files.get('file')
        if not file:
            raise AppError('Hãy chọn tệp JSON.')
        try:
            return validate_snapshot(json.load(file.stream))
        except (ValueError, UnicodeError):
            raise AppError('Không thể đọc tệp JSON.')

    def backup():
        data = read(snapshot)
        filename = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ_') + uuid4().hex[:8] + '.json'
        path = backup_dir / filename
        path.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        return filename

    @api.post('/api/admin/traffic/import')
    def import_data():
        data = uploaded()
        saved = backup()
        write(write_snapshot, data, False)
        return jsonify(message='Đã nhập/gộp dữ liệu và nối lại ACCESS_TO.', backup=saved)

    @api.get('/api/admin/traffic/backups')
    def backups():
        return jsonify([dict(name=p.name, size=p.stat().st_size) for p in sorted(backup_dir.glob('*.json'), reverse=True)])

    @api.post('/api/admin/traffic/backups')
    def create_backup():
        return jsonify(name=backup(), message='Đã sao lưu dữ liệu giao thông.'), 201

    def backup_path(name):
        if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z_[a-f0-9]{8}\.json', name):
            raise AppError('Tên bản sao lưu không hợp lệ.')
        path = backup_dir / name
        if not path.is_file():
            raise AppError('Không tìm thấy bản sao lưu.', 404)
        return path

    @api.get('/api/admin/traffic/backups/<name>')
    def download_backup(name):
        return send_file(backup_path(name), as_attachment=True)

    @api.post('/api/admin/traffic/restore')
    def restore():
        if request.form.get('confirm') != 'RESTORE':
            raise AppError('Nhập RESTORE để xác nhận thay thế toàn bộ dữ liệu giao thông.')
        if request.files.get('file'):
            data = uploaded()
        else:
            path = backup_path(request.form.get('name', ''))
            try:
                data = validate_snapshot(json.loads(path.read_text(encoding='utf-8')))
            except (ValueError, UnicodeError):
                raise AppError('Bản sao lưu không đọc được.')
        saved = backup()
        write(write_snapshot, data, True)
        return jsonify(message='Đã khôi phục dữ liệu giao thông. Tài khoản được giữ nguyên.', backup=saved)

    @api.route('/api/admin/traffic/junctions', methods=['POST'])
    @api.route('/api/admin/traffic/junctions/<identity>', methods=['PUT', 'DELETE'])
    def change_junction(identity=None):
        data = validate_junction(request.get_json(silent=True)) if request.method != 'DELETE' else None
        jid = junction_id(identity) if identity else data['junctionId']
        if data and data['junctionId'] != jid:
            raise AppError('Không thể thay đổi mã Junction khi sửa.')
        def change(tx):
            found = tx.run('MATCH (j:Junction {junctionId: $id}) RETURN j.junctionId AS id', id=jid).single()
            if request.method == 'POST' and found:
                raise AppError('Mã Junction đã tồn tại.', 409)
            if request.method != 'POST' and not found:
                raise AppError('Không tìm thấy Junction.', 404)
            if request.method == 'DELETE':
                tx.run('MATCH (j:Junction {junctionId: $id}) DETACH DELETE j', id=jid).consume()
            else:
                tx.run('MERGE (j:Junction {junctionId: $id}) SET j += $props', id=jid, props=data).consume()
            relink_places(tx)
        write(change)
        return jsonify(message='Đã cập nhật Junction và ACCESS_TO.')

    @api.route('/api/admin/traffic/roads', methods=['POST'])
    @api.route('/api/admin/traffic/roads/<path:identity>', methods=['PUT', 'DELETE'])
    def change_road(identity=None):
        data = validate_road(request.get_json(silent=True)) if request.method != 'DELETE' else None
        rid = identity or data['properties']['roadId']
        if data and data['properties']['roadId'] != rid:
            raise AppError('Không thể thay đổi mã đường khi sửa.')
        def change(tx):
            existing = tx.run('MATCH ()-[r:ROAD {roadId: $id}]->() RETURN properties(r) AS props LIMIT 1', id=rid).single()
            found = existing is not None
            if request.method == 'POST' and found:
                raise AppError('Mã đường đã tồn tại.', 409)
            if request.method != 'POST' and not found:
                raise AppError('Không tìm thấy đường.', 404)
            if data:
                if existing:
                    data['properties'] = {**existing['props'], **data['properties']}
                for endpoint in (data['source'], data['target']):
                    if not tx.run('MATCH (j:Junction {junctionId: $id}) RETURN j.junctionId AS id', id=endpoint).single():
                        raise AppError('Junction đầu/cuối không tồn tại.', 409)
            tx.run('MATCH ()-[r:ROAD {roadId: $id}]->() DELETE r', id=rid).consume()
            if data:
                tx.run('MATCH (s:Junction {junctionId: $source}), (t:Junction {junctionId: $target}) CREATE (s)-[r:ROAD]->(t) SET r = $props',
                       source=data['source'], target=data['target'], props=data['properties']).consume()
        write(change)
        return jsonify(message='Đã cập nhật đường. ROAD có hướng từ Junction đầu tới Junction cuối.')

    app.register_blueprint(api)
