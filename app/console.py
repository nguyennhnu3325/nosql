"""Explorer, administration settings, profile and application database backups."""
import json
import math
import re
import secrets
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from flask import Blueprint, jsonify, render_template, request, g, session, send_file
from werkzeug.security import check_password_hash, generate_password_hash
try:
    from .network_analysis import analyze_network
    from .auth import AppError, public_user
    from .place_admin import validate_place, save_place, PlaceError, check_place_bounds
    from .traffic_admin import snapshot, write_snapshot, validate_junction, validate_road, properties, relink_places
except ImportError:
    from network_analysis import analyze_network
    from auth import AppError, public_user
    from place_admin import validate_place, save_place, PlaceError, check_place_bounds
    from traffic_admin import snapshot, write_snapshot, validate_junction, validate_road, properties, relink_places

DEFAULTS = dict(accessWarningM=500)
DB_FORMAT = 'urban-route-database-v1'


def validate_database(data, actor_id):
    if not isinstance(data, dict) or data.get('format') != DB_FORMAT:
        raise AppError('Không đúng định dạng bản sao lưu CSDL ứng dụng.')
    nodes, links = data.get('nodes'), data.get('relationships')
    if not isinstance(nodes, list) or not isinstance(links, list):
        raise AppError('Thiếu nodes hoặc relationships.')
    ids = set()
    admins = []
    for n in nodes:
        if not isinstance(n, dict) or not isinstance(n.get('id'), str) or n['id'] in ids:
            raise AppError('Node thiếu mã hoặc bị trùng.')
        ids.add(n['id'])
        labels = n.get('labels')
        if not isinstance(labels, list) or not labels or any(not isinstance(x,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,99}',x) for x in labels):
            raise AppError('Nhãn node không hợp lệ.')
        props = properties(n.get('properties'))
        if 'AppUser' in labels:
            if props.get('role') not in ('admin','user') or not isinstance(props.get('active'), bool) or not isinstance(props.get('passwordHash'),str):
                raise AppError('Dữ liệu tài khoản không hợp lệ.')
            if props.get('role') == 'admin' and props.get('active'):
                admins.append(props.get('userId'))
    if actor_id not in admins:
        raise AppError('Bản sao lưu phải chứa tài khoản admin hiện tại đang hoạt động để có thể tiếp tục quản trị.')
    for r in links:
        if not isinstance(r,dict) or r.get('source') not in ids or r.get('target') not in ids or not isinstance(r.get('type'),str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,99}',r['type']):
            raise AppError('Relationship không hợp lệ hoặc thiếu node đầu/cuối.')
        properties(r.get('properties'))
    return data


def database_snapshot(tx):
    return dict(format=DB_FORMAT, createdAt=datetime.now(timezone.utc).isoformat(),
        nodes=[dict(r) for r in tx.run('MATCH (n) RETURN elementId(n) AS id, labels(n) AS labels, properties(n) AS properties')],
        relationships=[dict(r) for r in tx.run('MATCH (s)-[r]->(t) RETURN elementId(s) AS source, elementId(t) AS target, type(r) AS type, properties(r) AS properties')])


def restore_database(tx, data):
    tx.run('MATCH (n) DETACH DELETE n').consume()
    node_ids = {}
    for n in data['nodes']:
        labels = ':'.join(n['labels'])  # validated identifier alphabet; never accept Cypher text
        node_ids[n['id']] = tx.run('CREATE (n:'+labels+') SET n = $props RETURN elementId(n) AS id',props=n['properties']).single()['id']
    tx.run('MATCH (u:AppUser) SET u.sessionVersion=coalesce(u.sessionVersion,0)+$offset',offset=secrets.randbelow(1000000000)+1000000000).consume()
    for r in data['relationships']:
        tx.run('MATCH (s), (t) WHERE elementId(s)=$source AND elementId(t)=$target CREATE (s)-[r:'+r['type']+']->(t) SET r=$props',
            source=node_ids[r['source']],target=node_ids[r['target']],props=r['properties']).consume()


def register_console(app, driver, database):
    bp = Blueprint('console', __name__)
    @bp.errorhandler(PlaceError)
    def place_error(error):
        return jsonify(error=str(error)), error.status

    base = Path(app.root_path).parent
    backup_dir = base / 'backups' / 'database'
    backup_dir.mkdir(parents=True,exist_ok=True)

    def read(fn):
        with driver.session(database=database) as db:
            return db.execute_read(fn)

    def write(fn, invalidate=True):
        with app.extensions['traffic_lock']:
            with driver.session(database=database) as db:
                result = db.execute_write(fn)
            if invalidate: app.extensions['road_graph_dirty'] = True
            return result

    def settings():
        rows = read(lambda tx: [dict(r['p']) for r in tx.run("MATCH (c:AppConfig {key:'main'}) RETURN properties(c) AS p")])
        return {**DEFAULTS, **({k:v for k,v in rows[0].items() if k in DEFAULTS} if rows else {})}

    def gds_status():
        try:
            def query(tx):
                version = tx.run('RETURN gds.version() AS version').single()['version']
                exists = tx.run("CALL gds.graph.exists('roadGraph') YIELD exists RETURN exists").single()['exists']
                info = dict(tx.run("CALL gds.graph.list('roadGraph') YIELD nodeCount, relationshipCount RETURN nodeCount, relationshipCount").single()) if exists else {}
                return dict(available=True,version=version,exists=exists,dirty=bool(app.extensions.get('road_graph_dirty')),**info)
            return read(query)
        except Exception:
            return dict(available=False,exists=False,dirty=bool(app.extensions.get('road_graph_dirty')),message='GDS chưa sẵn sàng hoặc tài khoản DB thiếu quyền.')

    @bp.get('/admin/network')
    def management(): return render_template('manage.html',kind='junctions')

    @bp.get('/admin/system')
    @bp.get('/admin/settings')
    def system_page(): return render_template('system.html',settings_page=request.path.endswith('settings'))

    @bp.get('/profile')
    def profile_page(): return render_template('profile.html')

    @bp.get('/api/explorer')
    def explorer():
        def query(tx):
            data = snapshot(tx)
            data['junctions'] = [dict(r['p'],elementId=r['id']) for r in tx.run('MATCH (j:Junction) RETURN j {.*, junctionId:toString(j.junctionId)} AS p, elementId(j) AS id')]
            data['places'] = [dict(r['p'],elementId=r['id']) for r in tx.run('MATCH (p:Place) RETURN properties(p) AS p, elementId(p) AS id')]
            data['roads'] = [dict(r) for r in tx.run('MATCH (s:Junction)-[r:ROAD]->(t:Junction) RETURN toString(s.junctionId) AS source,toString(t.junctionId) AS target,properties(r) AS properties,elementId(r) AS elementId')]
            data['access'] = [dict(r) for r in tx.run('MATCH (p:Place)-[r:ACCESS_TO]->(j:Junction) RETURN p.placeId AS placeId,toString(j.junctionId) AS junctionId,properties(r) AS properties,elementId(r) AS elementId')]
            return data
        return jsonify(read(query))

    @bp.get('/api/analysis/details')
    def analysis_details():
        def analysis_snapshot(tx):
            return dict(
                junctions=[dict(r['p'], _key=r['key']) for r in tx.run('MATCH (j:Junction) RETURN properties(j) AS p, elementId(j) AS key')],
                places=[dict(r['p'], _key=r['key']) for r in tx.run('MATCH (p:Place) RETURN properties(p) AS p, elementId(p) AS key')],
                roads=[dict(r) for r in tx.run('MATCH (s:Junction)-[r:ROAD]->(t:Junction) RETURN s.junctionId AS source, t.junctionId AS target, elementId(s) AS sourceKey, elementId(t) AS targetKey, properties(r) AS properties')],
                access=[dict(r) for r in tx.run('MATCH (p:Place)-[r:ACCESS_TO]->(j:Junction) RETURN p.placeId AS placeId, j.junctionId AS junctionId, elementId(p) AS placeKey, elementId(j) AS junctionKey, properties(r) AS properties')])
        data = read(analysis_snapshot)
        return jsonify(network=analyze_network(data, settings()['accessWarningM']))

    @bp.get('/api/admin/settings')
    def get_settings(): return jsonify(settings())

    @bp.put('/api/admin/settings')
    def update_settings():
        data = request.get_json(silent=True)
        value = data.get('accessWarningM') if isinstance(data,dict) else None
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 1 <= value <= 10000:
            raise AppError('Ngưỡng cảnh báo phải từ 1 đến 10.000 m.')
        write(lambda tx: tx.run("MERGE (c:AppConfig {key:'main'}) SET c.accessWarningM=$value",value=value).consume(), invalidate=False)
        return jsonify(message='Đã lưu cấu hình.',accessWarningM=value)

    @bp.get('/api/admin/system')
    def system_status():
        driver.verify_connectivity()
        counts = read(lambda tx: dict(tx.run('MATCH (n) RETURN count(n) AS nodeCount').single()))
        return jsonify(database=database,neo4j='Đã kết nối',gds=gds_status(),**counts)

    @bp.route('/api/profile',methods=['GET','PUT'])
    def profile():
        store = app.extensions['auth_store']
        if request.method == 'GET': return jsonify(g.user)
        data = request.get_json(silent=True)
        if not isinstance(data,dict) or not isinstance(data.get('displayName'),str) or not 1 <= len(data['displayName'].strip()) <= 100:
            raise AppError('Tên hiển thị phải từ 1 đến 100 ký tự.')
        current = store.by_id(g.user['userId'])
        password = data.get('password','')
        if not isinstance(password,str) or (password and not 8 <= len(password) <= 128): raise AppError('Mật khẩu mới phải từ 8 đến 128 ký tự.')
        if password and (not isinstance(data.get('currentPassword'),str) or not check_password_hash(current['passwordHash'],data['currentPassword'])):
            raise AppError('Mật khẩu hiện tại không đúng.')
        props = dict(displayName=data['displayName'].strip())
        if password: props.update(passwordHash=generate_password_hash(password),sessionVersion=current.get('sessionVersion',0)+1)
        def change(tx):
            row=tx.run('MATCH (u:AppUser {userId:$id}) SET u += $props RETURN properties(u) AS u',id=current['userId'],props=props).single()
            if not row: raise AppError('Tài khoản không còn tồn tại.',404)
            return row['u']
        user = write(change, invalidate=False)
        session['version'] = user.get('sessionVersion',0)
        return jsonify(public_user(user))

    @bp.post('/api/admin/users/<user_id>/reset-password')
    def reset_password(user_id):
        store=app.extensions['auth_store']
        user=store.by_id(user_id)
        if not user: raise AppError('Không tìm thấy người dùng.',404)
        password=secrets.token_urlsafe(12)
        def change(tx):
            row=tx.run('MATCH (u:AppUser {userId:$id}) SET u.passwordHash=$hash, u.sessionVersion=coalesce(u.sessionVersion,0)+1 RETURN u.sessionVersion AS version',id=user_id,hash=generate_password_hash(password)).single()
            if not row: raise AppError('Không tìm thấy người dùng.',404)
            return row['version']
        version=write(change, invalidate=False)
        if user_id==g.user['userId']: session['version']=version
        return jsonify(message='Đã reset mật khẩu và kết thúc các phiên cũ.',password=password)

    @bp.get('/api/admin/place-policy')
    def policy():
        bounds=read(lambda tx: dict(tx.run('MATCH (j:Junction) RETURN min(j.latitude) AS south,max(j.latitude) AS north,min(j.longitude) AS west,max(j.longitude) AS east').single()))
        return jsonify(bounds=bounds,**settings())

    @bp.post('/api/admin/place-preview')
    def place_preview():
        values=validate_place(request.get_json(silent=True))
        def query(tx):
            check_place_bounds(tx,values,request.get_json(silent=True).get('placeId'))
            row=tx.run("""MATCH (j:Junction) WITH j, point.distance(point({latitude:$latitude,longitude:$longitude}),point({latitude:toFloat(j.latitude),longitude:toFloat(j.longitude)})) AS distance
                RETURN toString(j.junctionId) AS junctionId,distance ORDER BY distance,j.junctionId LIMIT 1""",**values).single()
            return dict(row) if row else None
        try: result=read(query)
        except PlaceError as error: raise AppError(str(error),error.status)
        if not result: raise AppError('Chưa có Junction hợp lệ.')
        return jsonify(**result,warning=result['distance']>settings()['accessWarningM'],threshold=settings()['accessWarningM'])

    @bp.route('/api/admin/exchange/<kind>',methods=['GET','POST'])
    def exchange(kind):
        if kind not in ('places','junctions','roads'): raise AppError('Loại dữ liệu không hợp lệ.',404)
        if request.method=='GET':
            data=read(snapshot)
            rows=data[kind]
            result=jsonify(format='urban-route-'+kind+'-v1',rows=rows)
            result.headers['Content-Disposition']='attachment; filename="'+kind+'.json"'
            return result
        file=request.files.get('file')
        if not file: raise AppError('Hãy chọn tệp JSON.')
        try: data=json.load(file.stream)
        except (ValueError,UnicodeError): raise AppError('Tệp JSON không hợp lệ.')
        if not isinstance(data,dict) or data.get('format')!='urban-route-'+kind+'-v1' or not isinstance(data.get('rows'),list): raise AppError('Tệp không đúng mẫu export của mục này.')
        rows=data['rows']
        # Validate every row before writing; execute_write makes the entire import atomic.
        if kind=='places':
            for row in rows:
                validate_place(row)
                if not isinstance(row.get('placeId'),str) or not 1<=len(row['placeId'])<=200: raise AppError('Place thiếu placeId.')
        elif kind=='junctions': rows=[validate_junction(r) for r in rows]
        else: rows=[validate_road(r) for r in rows]
        ids=[r['properties']['roadId'] if kind=='roads' else r['placeId' if kind=='places' else 'junctionId'] for r in rows]
        if len(ids)!=len(set(ids)): raise AppError('Tệp chứa mã bị trùng.')
        saved=save_backup()
        def change(tx):
            if kind=='places':
                for row in rows:
                    existing=tx.run('MATCH (p:Place {placeId:$id}) RETURN p.placeId AS id',id=row['placeId']).single()
                    save_place(tx,row['placeId'],validate_place(row),not bool(existing))
            elif kind=='junctions':
                tx.run('UNWIND $rows AS row MERGE (j:Junction {junctionId:row.junctionId}) SET j += row',rows=rows).consume()
                relink_places(tx)
            else:
                for row in rows:
                    count=tx.run('MATCH (j:Junction) WHERE j.junctionId IN $ids RETURN count(j) AS n',ids=list(set([row['source'],row['target']]))).single()['n']
                    if count!=len(set([row['source'],row['target']])): raise AppError('Junction đầu/cuối không tồn tại.')
                tx.run('UNWIND $rows AS row MATCH ()-[r:ROAD {roadId:row.properties.roadId}]->() DELETE r',rows=rows).consume()
                tx.run('UNWIND $rows AS row MATCH (s:Junction {junctionId:row.source}),(t:Junction {junctionId:row.target}) CREATE (s)-[r:ROAD]->(t) SET r=row.properties',rows=rows).consume()
        try: write(change)
        except PlaceError as error: raise AppError(str(error),error.status)
        return jsonify(message='Đã nhập '+str(len(rows))+' dòng.',backup=saved)

    def save_backup():
        data=read(database_snapshot)
        name=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ_')+uuid4().hex[:8]+'.json'
        (backup_dir/name).write_text(json.dumps(data,ensure_ascii=False,allow_nan=False),encoding='utf-8')
        return name

    def backup_path(name):
        if not isinstance(name,str) or not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z_[a-f0-9]{8}.json',name): raise AppError('Tên bản sao lưu không hợp lệ.')
        path=backup_dir/name
        if not path.is_file(): raise AppError('Không tìm thấy bản sao lưu.',404)
        return path

    @bp.route('/api/admin/database/backups',methods=['GET','POST'])
    def backups():
        if request.method=='POST':
            with app.extensions['traffic_lock']: name=save_backup()
            return jsonify(message='Đã sao lưu CSDL ứng dụng.',name=name),201
        return jsonify([dict(name=p.name,size=p.stat().st_size) for p in sorted(backup_dir.glob('*.json'),reverse=True)])

    @bp.get('/api/admin/database/backups/<name>')
    def download(name): return send_file(backup_path(name),as_attachment=True)

    @bp.post('/api/admin/database/restore')
    def restore():
        if request.form.get('confirm')!='RESTORE': raise AppError('Nhập RESTORE để xác nhận phục hồi CSDL.')
        file=request.files.get('file')
        try: data=json.load(file.stream) if file and file.filename else json.loads(backup_path(request.form.get('name')).read_text(encoding='utf-8'))
        except (ValueError,UnicodeError): raise AppError('Tệp sao lưu không đọc được.')
        data=validate_database(data,g.user['userId'])
        with app.extensions['traffic_lock']:
            saved=save_backup()
            write(lambda tx: restore_database(tx,data))
        user=app.extensions['auth_store'].by_id(g.user['userId'])
        session['version']=user['sessionVersion']
        return jsonify(message='Đã phục hồi CSDL ứng dụng.',backup=saved)

    app.register_blueprint(bp)
