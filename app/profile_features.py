"""Private profile settings, server-recorded history and saved route snapshots."""
import base64
import binascii
import json
import math
import re
import struct
import zlib
from datetime import datetime, timezone
from uuid import uuid4

from flask import Blueprint, g, jsonify, render_template, request, session
from werkzeug.security import check_password_hash
try:
    from .auth import AppError
except ImportError:
    from auth import AppError


def validate_details(data):
    if not isinstance(data, dict):
        raise AppError('Thông tin hồ sơ không hợp lệ.')
    result = {}
    for key, limit in [('displayName', 100), ('email', 254), ('phone', 30)]:
        if key not in data:
            continue
        value = data[key]
        if not isinstance(value, str) or len(value.strip()) > limit:
            raise AppError('Thông tin '+key+' không hợp lệ.')
        result[key] = value.strip()
    if 'displayName' in result and not result['displayName']:
        raise AppError('Họ tên không được để trống.')
    if result.get('email') and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', result['email']):
        raise AppError('Email không hợp lệ.')
    if result.get('phone') and not re.fullmatch(r'[+\d ()-]{3,30}', result['phone']):
        raise AppError('Số điện thoại không hợp lệ.')
    if 'mapStyle' in data:
        if data['mapStyle'] not in ('standard', 'grayscale'):
            raise AppError('Kiểu bản đồ không hợp lệ.')
        result['mapStyle'] = data['mapStyle']
    if 'estimatedSpeedKmh' in data:
        speed = data['estimatedSpeedKmh']
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(speed) or not 1 <= speed <= 120:
            raise AppError('Tốc độ ước tính phải từ 1 đến 120 km/h.')
        result['estimatedSpeedKmh'] = speed
    if 'avatar' in data:
        value = data['avatar']
        if not isinstance(value, str) or len(value) > 360000:
            raise AppError('Avatar vượt quá dung lượng cho phép.')
        if value:
            if not value.startswith('data:image/png;base64,'):
                raise AppError('Avatar phải là ảnh PNG.')
            try:
                raw = base64.b64decode(value.split(',', 1)[1], validate=True)
                if len(raw) > 262144 or raw[:8] != b'\x89PNG\r\n\x1a\n':
                    raise ValueError()
                offset, dimensions, ended = 8, None, False
                while offset < len(raw):
                    size = struct.unpack('>I', raw[offset:offset+4])[0]
                    kind = raw[offset+4:offset+8]
                    chunk = raw[offset+8:offset+8+size]
                    crc = struct.unpack('>I', raw[offset+8+size:offset+12+size])[0]
                    if zlib.crc32(kind+chunk) & 0xffffffff != crc:
                        raise ValueError()
                    if offset == 8:
                        if kind != b'IHDR' or size != 13:
                            raise ValueError()
                        dimensions = struct.unpack('>II', chunk[:8])
                    offset += size+12
                    if kind == b'IEND':
                        ended = True
                        break
                if not ended or not dimensions or not all(1 <= n <= 512 for n in dimensions):
                    raise ValueError()
            except (ValueError, binascii.Error, struct.error):
                raise AppError('Ảnh PNG không hợp lệ; tối đa 512 × 512 px và 256 KB.')
        result['avatar'] = value
    return result


def safe_details(user):
    user = user or {}
    return {key: user.get(key, default) for key, default in (
        ('displayName',''), ('username',''), ('role','user'), ('email',''),
        ('phone',''), ('avatar',''), ('mapStyle','standard'), ('estimatedSpeedKmh',30))}


def register_profile_features(app, driver, database):
    bp = Blueprint('profile_features', __name__)

    def read(fn):
        with driver.session(database=database) as db:
            return db.execute_read(fn)

    def write(fn):
        with app.extensions['traffic_lock']:
            with driver.session(database=database) as db:
                return db.execute_write(fn)

    def record(kind, payload):
        if not getattr(g, 'user', None):
            return None
        identity = uuid4().hex
        try:
            serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            app.logger.warning('Activity payload could not be serialized')
            return None
        if len(serialized.encode('utf-8')) > 2_000_000:
            return None
        now = datetime.now(timezone.utc).isoformat()
        counter = 'routeSearchCount' if kind == 'route' else 'analysisCount'
        def save(tx):
            row = tx.run('MATCH (u:AppUser {userId:$uid}) '
                'CREATE (u)-[:HAS_ACTIVITY]->(a:ProfileActivity) '
                'SET a.id=$id,a.kind=$kind,a.createdAt=$created,a.payload=$payload '
                'SET u.'+counter+'=coalesce(u.'+counter+',0)+1 RETURN a.id AS id',
                uid=g.user['userId'],id=identity,kind=kind,created=now,payload=serialized).single()
            tx.run('MATCH (:AppUser {userId:$uid})-[:HAS_ACTIVITY]->(a:ProfileActivity) '
                'WHERE a.kind=$kind WITH a ORDER BY a.createdAt DESC SKIP 100 DETACH DELETE a',
                uid=g.user['userId'],kind=kind).consume()
            return row['id'] if row else None
        # History failure must not turn a successfully computed route into a 500.
        try:
            return write(save)
        except Exception:
            app.logger.exception('Unable to record private profile activity')
            return None

    app.extensions['record_profile_activity'] = record

    @bp.get('/history')
    def history_page():
        return render_template('route_history.html')

    @bp.get('/saved-routes')
    def saved_routes_page():
        return render_template('saved_routes.html')

    @bp.route('/api/profile/details', methods=['GET','PUT'])
    def details():
        if request.method == 'PUT':
            props = validate_details(request.get_json(silent=True))
            def save(tx):
                row = tx.run('MATCH (u:AppUser {userId:$uid}) SET u += $props RETURN properties(u) AS u',
                    uid=g.user['userId'],props=props).single()
                if not row:
                    raise AppError('Tài khoản không còn tồn tại.',404)
                return safe_details(row['u'])
            return jsonify(write(save))
        return jsonify(safe_details(app.extensions['auth_store'].by_id(g.user['userId'])))

    @bp.get('/api/profile/activity')
    def activity():
        def query(tx):
            user = tx.run('MATCH (u:AppUser {userId:$uid}) RETURN properties(u) AS u',uid=g.user['userId']).single()
            rows = tx.run('MATCH (:AppUser {userId:$uid})-[:HAS_ACTIVITY]->(a:ProfileActivity) '
                'WHERE $admin OR a.kind="route" '
                'RETURN properties(a) AS a ORDER BY a.createdAt DESC',uid=g.user['userId'],admin=g.user['role']=='admin')
            history=[]
            for row in rows:
                item=dict(row['a']); payload=json.loads(item.pop('payload'))
                if item['kind']=='route':
                    route=payload['routes'][0]
                    item.update(start=route['startPlace'].get('name'),end=route['endPlace'].get('name'),
                        distanceM=route['totalDistanceM'],minutes=payload['estimatedMinutes'],routeCount=len(payload['routes']))
                else:
                    item['summary']=payload
                history.append(item)
            saved=[]
            for row in tx.run('MATCH (:AppUser {userId:$uid})-[:SAVED_ROUTE]->(s:ProfileSavedRoute) '
                             'RETURN properties(s) AS s ORDER BY s.createdAt DESC',uid=g.user['userId']):
                item=dict(row['s']); route=json.loads(item.pop('payload'))
                item.update(start=route['startPlace'].get('name'),end=route['endPlace'].get('name'),distanceM=route['totalDistanceM'])
                saved.append(item)
            u=user['u'] if user else {}
            return dict(history=history,saved=saved,stats=dict(searches=u.get('routeSearchCount',0),saved=len(saved),analyses=u.get('analysisCount',0) if g.user['role']=='admin' else 0))
        return jsonify(read(query))

    @bp.get('/api/profile/activity/<identity>')
    def activity_item(identity):
        def query(tx):
            row=tx.run('MATCH (:AppUser {userId:$uid})-[:HAS_ACTIVITY]->(a:ProfileActivity {id:$id}) RETURN properties(a) AS a',uid=g.user['userId'],id=identity).single()
            if not row: raise AppError('Không tìm thấy lịch sử của bạn.',404)
            item=dict(row['a'])
            if item['kind']=='analysis' and g.user['role']!='admin':
                raise AppError('Chỉ quản trị viên được xem kết quả phân tích.',403)
            item['payload']=json.loads(item['payload'])
            return item
        return jsonify(read(query))

    @bp.post('/api/profile/saved-routes')
    def save_route():
        data=request.get_json(silent=True)
        if not isinstance(data,dict) or not isinstance(data.get('historyId'),str) or type(data.get('routeIndex')) is not int:
            raise AppError('Hãy chọn tuyến trong lịch sử tìm đường.')
        def save(tx):
            # Lock the owning account to serialize limit checks and repeated saves.
            tx.run('MATCH (u:AppUser {userId:$uid}) SET u.profileRevision=coalesce(u.profileRevision,0)+1',uid=g.user['userId']).consume()
            row=tx.run('MATCH (:AppUser {userId:$uid})-[:HAS_ACTIVITY]->(a:ProfileActivity {id:$id,kind:"route"}) RETURN a.payload AS payload',uid=g.user['userId'],id=data['historyId']).single()
            if not row: raise AppError('Không tìm thấy lịch sử của bạn.',404)
            routes=json.loads(row['payload'])['routes']; index=data['routeIndex']
            if not 0<=index<len(routes): raise AppError('Tuyến không hợp lệ.')
            identity=data['historyId']+':'+str(index)
            existing=tx.run('MATCH (:AppUser {userId:$uid})-[:SAVED_ROUTE]->(s:ProfileSavedRoute {id:$id}) RETURN s.id AS id',uid=g.user['userId'],id=identity).single()
            if existing: return dict(id=identity,message='Tuyến đã có trong danh sách yêu thích.')
            count=tx.run('MATCH (:AppUser {userId:$uid})-[:SAVED_ROUTE]->(s:ProfileSavedRoute) RETURN count(s) AS n',uid=g.user['userId']).single()['n']
            if count>=100: raise AppError('Tối đa 100 tuyến đã lưu. Hãy bỏ một tuyến trước.',409)
            tx.run('MATCH (u:AppUser {userId:$uid}) CREATE (u)-[:SAVED_ROUTE]->(s:ProfileSavedRoute) '
                   'SET s.id=$id,s.createdAt=$created,s.payload=$payload',uid=g.user['userId'],id=identity,
                   created=datetime.now(timezone.utc).isoformat(),payload=json.dumps(routes[index],ensure_ascii=False)).consume()
            return dict(id=identity,message='Đã lưu tuyến yêu thích.')
        return jsonify(write(save))

    @bp.route('/api/profile/saved-routes/<path:identity>',methods=['GET','DELETE'])
    def saved_item(identity):
        def query(tx):
            row=tx.run('MATCH (:AppUser {userId:$uid})-[:SAVED_ROUTE]->(s:ProfileSavedRoute {id:$id}) RETURN s.payload AS payload',uid=g.user['userId'],id=identity).single()
            if not row: raise AppError('Không tìm thấy tuyến đã lưu của bạn.',404)
            if request.method=='DELETE':
                tx.run('MATCH (:AppUser {userId:$uid})-[:SAVED_ROUTE]->(s:ProfileSavedRoute {id:$id}) DETACH DELETE s',uid=g.user['userId'],id=identity).consume()
                return dict(message='Đã bỏ lưu tuyến.')
            return json.loads(row['payload'])
        return jsonify(write(query) if request.method=='DELETE' else read(query))

    @bp.delete('/api/profile/account')
    def delete_account():
        data=request.get_json(silent=True)
        if not isinstance(data,dict) or data.get('confirm')!='DELETE' or not isinstance(data.get('password'),str) or not 1 <= len(data['password']) <= 128:
            raise AppError('Nhập DELETE và mật khẩu hiện tại để xóa tài khoản.')
        store=app.extensions['auth_store'];store.initialize()
        def remove(tx):
            tx.run("MATCH (s:AuthState {key:'users'}) SET s.revision=coalesce(s.revision,0)+1").consume()
            row=tx.run('MATCH (u:AppUser {userId:$uid}) RETURN properties(u) AS u',uid=g.user['userId']).single()
            if not row or not check_password_hash(row['u']['passwordHash'],data['password']):
                raise AppError('Mật khẩu hiện tại không đúng.')
            if row['u']['role']=='admin':
                count=tx.run("MATCH (u:AppUser {role:'admin',active:true}) RETURN count(u) AS n").single()['n']
                if count<=1: raise AppError('Không thể xóa quản trị viên hoạt động cuối cùng.',409)
            tx.run('MATCH (:AppUser {userId:$uid})-[:HAS_ACTIVITY|SAVED_ROUTE]->(n) '
                   'WHERE n:ProfileActivity OR n:ProfileSavedRoute DETACH DELETE n',uid=g.user['userId']).consume()
            tx.run('MATCH (u:AppUser {userId:$uid}) DETACH DELETE u',uid=g.user['userId']).consume()
        write(remove);session.clear()
        return jsonify(message='Đã xóa tài khoản và dữ liệu cá nhân.')

    app.register_blueprint(bp)
