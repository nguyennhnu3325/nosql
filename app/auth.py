"""Neo4j users, signed login sessions, role enforcement and CSRF protection."""
import hmac
import os
import re
import secrets
import time
from datetime import timedelta
from pathlib import Path
from threading import Lock
from uuid import uuid4

from flask import Blueprint, g, jsonify, redirect, render_template, request, session, url_for
from neo4j.exceptions import ConstraintError, Neo4jError, ServiceUnavailable, SessionExpired
from werkzeug.security import check_password_hash, generate_password_hash


class AppError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def public_user(user):
    return {key: user.get(key) for key in ('userId', 'username', 'displayName', 'role', 'active')}


def validate_user(data, creating=False):
    if not isinstance(data, dict):
        raise AppError('Dữ liệu người dùng không hợp lệ.')
    username = data.get('username', '')
    if not isinstance(username, str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{3,50}', username):
        raise AppError('Tên đăng nhập gồm 3–50 chữ, số hoặc các ký tự _ . -')
    display = data.get('displayName', '')
    if not isinstance(display, str) or not 1 <= len(display.strip()) <= 100:
        raise AppError('Tên hiển thị phải có từ 1 đến 100 ký tự.')
    role = data.get('role', 'user')
    if role not in ('user', 'admin'):
        raise AppError('Vai trò phải là user hoặc admin.')
    active = data.get('active', True)
    if not isinstance(active, bool):
        raise AppError('Trạng thái tài khoản không hợp lệ.')
    password = data.get('password', '')
    if not isinstance(password, str) or ((creating or password) and not 8 <= len(password) <= 128):
        raise AppError('Mật khẩu phải có từ 8 đến 128 ký tự.')
    return dict(username=username.lower(), displayName=display.strip(), role=role,
                active=active, password=password)


class AuthStore:
    def __init__(self, driver, database):
        self.driver, self.database = driver, database
        self.ready = False
        self.lock = Lock()

    def initialize(self):
        with self.lock:
            if self.ready:
                return
            with self.driver.session(database=self.database) as db:
                for query in (
                    'CREATE CONSTRAINT app_user_id IF NOT EXISTS FOR (u:AppUser) REQUIRE u.userId IS UNIQUE',
                    'CREATE CONSTRAINT app_username IF NOT EXISTS FOR (u:AppUser) REQUIRE u.username IS UNIQUE',
                    'CREATE CONSTRAINT app_auth_state IF NOT EXISTS FOR (s:AuthState) REQUIRE s.key IS UNIQUE',
                ):
                    db.run(query).consume()
                db.run("MERGE (s:AuthState {key: 'users'}) ON CREATE SET s.revision = 0").consume()
            self.ready = True

    def read(self, query, **params):
        self.initialize()
        with self.driver.session(database=self.database) as db:
            return db.execute_read(lambda tx: [dict(r) for r in tx.run(query, **params)])

    def has_users(self):
        return self.read('MATCH (u:AppUser) RETURN count(u) AS n')[0]['n'] > 0

    def by_id(self, user_id):
        rows = self.read('MATCH (u:AppUser {userId: $id}) RETURN properties(u) AS user', id=user_id)
        return rows[0]['user'] if rows else None

    def by_username(self, username):
        rows = self.read('MATCH (u:AppUser {username: $name}) RETURN properties(u) AS user', name=username)
        return rows[0]['user'] if rows else None

    def users(self):
        return [public_user(r['user']) for r in self.read('MATCH (u:AppUser) RETURN properties(u) AS user ORDER BY u.username')]

    def change(self, data=None, user_id=None, actor_id=None, setup=False, delete=False):
        self.initialize()
        values = validate_user(data, creating=user_id is None) if not delete else None
        password_hash = generate_password_hash(values['password']) if values and values['password'] else None
        def write(tx):
            # Serialize role changes to prevent concurrent removal of all administrators.
            tx.run("MATCH (s:AuthState {key: 'users'}) SET s.revision = s.revision + 1").consume()
            if setup and tx.run('MATCH (u:AppUser) RETURN count(u) AS n').single()['n']:
                raise AppError('Admin đầu tiên đã được tạo.', 409)
            existing = None
            if user_id:
                row = tx.run('MATCH (u:AppUser {userId: $id}) RETURN properties(u) AS user', id=user_id).single()
                if not row:
                    raise AppError('Không tìm thấy người dùng.', 404)
                existing = row['user']
                removing_admin = existing['role'] == 'admin' and existing.get('active', True) and (
                    delete or values['role'] != 'admin' or not values['active'])
                if removing_admin:
                    count = tx.run("MATCH (u:AppUser {role: 'admin', active: true}) RETURN count(u) AS n").single()['n']
                    if count <= 1:
                        raise AppError('Phải giữ lại ít nhất một admin đang hoạt động.', 409)
                if actor_id == user_id and (delete or not values['active'] or values['role'] != 'admin'):
                    raise AppError('Không thể tự xóa, khóa hoặc hạ quyền tài khoản đang đăng nhập.', 409)
            if delete:
                tx.run('MATCH (u:AppUser {userId: $id}) DETACH DELETE u', id=user_id).consume()
                return None
            duplicate = tx.run('MATCH (u:AppUser {username: $name}) WHERE $id IS NULL OR u.userId <> $id RETURN u.userId AS id LIMIT 1',
                               name=values['username'], id=user_id).single()
            if duplicate:
                raise AppError('Tên đăng nhập đã tồn tại.', 409)
            props = {k: values[k] for k in ('username', 'displayName', 'role', 'active')}
            props['sessionVersion'] = (existing or {}).get('sessionVersion', 0) + 1
            if password_hash:
                props['passwordHash'] = password_hash
            identity = user_id or uuid4().hex
            query = 'MATCH (u:AppUser {userId: $id})' if user_id else 'CREATE (u:AppUser {userId: $id})'
            row = tx.run(query + ' SET u += $props RETURN properties(u) AS user', id=identity, props=props).single()
            return public_user(row['user'])
        try:
            with self.driver.session(database=self.database) as db:
                return db.execute_write(write)
        except ConstraintError:
            raise AppError('Tên đăng nhập đã tồn tại.', 409)


def register_auth(app, driver, database):
    store = AuthStore(driver, database)
    app.extensions['auth_store'] = store
    secret = os.environ.get('FLASK_SECRET_KEY')
    if not secret:
        path = Path(app.root_path).parent / 'instance' / 'session.key'
        path.parent.mkdir(exist_ok=True)
        try:
            with path.open('x', encoding='ascii') as output:
                output.write(secrets.token_hex(32))
        except FileExistsError:
            pass
        secret = path.read_text(encoding='ascii').strip()
    app.config.update(SECRET_KEY=secret, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                      SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE') == '1',
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=8), MAX_CONTENT_LENGTH=32 * 1024 * 1024)
    auth = Blueprint('auth', __name__)
    attempts, attempt_lock = {}, Lock()
    dummy_hash = generate_password_hash(secrets.token_hex(16))

    @app.errorhandler(AppError)
    def app_error(error):
        if request.path.startswith('/api/'):
            return jsonify(error=str(error)), error.status
        return render_template('message.html', message=str(error)), error.status

    @app.errorhandler(Neo4jError)
    @app.errorhandler(ServiceUnavailable)
    @app.errorhandler(SessionExpired)
    def database_error(error):
        app.logger.exception('Neo4j operation failed')
        if request.path.startswith('/api/'):
            return jsonify(error='Không thể truy cập Neo4j. Kiểm tra kết nối cơ sở dữ liệu.'), 503
        return render_template('message.html', message='Không thể truy cập Neo4j. Kiểm tra kết nối cơ sở dữ liệu.'), 503

    @app.errorhandler(413)
    def too_large(error):
        return jsonify(error='Tệp tải lên vượt quá 32 MB.'), 413

    @app.before_request
    def protect():
        g.user = None
        session.setdefault('csrf', secrets.token_hex(32))
        if request.endpoint == 'static':
            return
        user_id = session.get('user_id')
        if user_id:
            user = store.by_id(user_id)
            if user and user.get('active') and user.get('sessionVersion') == session.get('version'):
                g.user = public_user(user)
            else:
                session.pop('user_id', None)
                session.pop('version', None)
        public = request.endpoint in ('auth.login', 'auth.setup', 'auth.register')
        if not public and not g.user:
            if request.path.startswith('/api/'):
                return jsonify(error='Vui lòng đăng nhập.'), 401
            return redirect(url_for('auth.login'))
        if (request.path.startswith('/admin') or request.path.startswith('/api/admin/')) and (not g.user or g.user['role'] != 'admin'):
            raise AppError('Bạn không có quyền quản trị.', 403)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            supplied = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token', '')
            if not supplied or not hmac.compare_digest(supplied.encode('utf-8'), session['csrf'].encode('utf-8')):
                raise AppError('Phiên thao tác hết hạn. Hãy tải lại trang.', 403)

    @app.context_processor
    def session_context():
        return dict(current_user=getattr(g, 'user', None), csrf_token=session.get('csrf', ''))

    @auth.route('/setup', methods=['GET', 'POST'])
    def setup():
        if store.has_users():
            return redirect(url_for('auth.login'))
        error = None
        if request.method == 'POST':
            try:
                store.change(dict(request.form, role='admin', active=True), setup=True)
                return redirect(url_for('auth.login', created='1'))
            except AppError as exc:
                error = str(exc)
        return render_template('login.html', setup=True, error=error)

    @auth.route('/login', methods=['GET', 'POST'])
    def login():
        if g.user:
            return redirect('/admin' if g.user['role'] == 'admin' else '/')
        if not store.has_users():
            return redirect(url_for('auth.setup'))
        error = None
        if request.method == 'POST':
            key = request.remote_addr or 'local'
            now = time.monotonic()
            with attempt_lock:
                # Remove expired entries to bound memory use.
                for old in list(attempts):
                    if now - attempts[old][0] > 300:
                        del attempts[old]
                since, failures = attempts.get(key, (now, 0))
                if failures >= 10:
                    raise AppError('Đăng nhập sai quá nhiều lần. Thử lại sau 5 phút.', 429)
                attempts[key] = (since, failures + 1)
            username = request.form.get('username', '').strip().lower()
            password = request.form.get('password', '')
            user = store.by_username(username)
            valid = check_password_hash((user.get('passwordHash') or dummy_hash) if user else dummy_hash, password[:129])
            if user and valid and len(password) <= 128 and user.get('active'):
                with attempt_lock:
                    attempts.pop(key, None)
                session.clear()
                session.update(user_id=user['userId'], version=user['sessionVersion'], csrf=secrets.token_hex(32))
                session.permanent = True
                return redirect('/admin' if user['role'] == 'admin' else '/')
            error = 'Tên đăng nhập hoặc mật khẩu không đúng, hoặc tài khoản đã bị khóa.'
        return render_template('login.html', setup=False, error=error)

    @auth.route('/register', methods=['GET', 'POST'])
    def register():
        if g.user:
            return redirect('/')
        if not store.has_users():
            return redirect(url_for('auth.setup'))
        error, status = None, 200
        if request.method == 'POST':
            try:
                if request.form.get('password') != request.form.get('confirmPassword'):
                    raise AppError('Mật khẩu xác nhận không khớp.')
                # Never accept role/active supplied by the public registration form.
                store.change(dict(username=request.form.get('username', ''),
                                  displayName=request.form.get('displayName', ''),
                                  password=request.form.get('password', ''), role='user', active=True))
                return redirect(url_for('auth.login', registered='1'))
            except AppError as exc:
                error, status = str(exc), exc.status
        return render_template('login.html', setup=False, registration=True, error=error), status

    @auth.post('/logout')
    def logout():
        session.clear()
        return redirect(url_for('auth.login'))

    @auth.get('/admin/users')
    def users_page():
        return render_template('users.html')

    @auth.get('/api/admin/users')
    def users_list():
        return jsonify(store.users())

    @auth.post('/api/admin/users')
    @auth.route('/api/admin/users/<user_id>', methods=['PUT', 'DELETE'])
    def users_change(user_id=None):
        result = store.change(request.get_json(silent=True), user_id=user_id,
                              actor_id=g.user['userId'], delete=request.method == 'DELETE')
        if result and result['userId'] == g.user['userId']:
            session['version'] = store.by_id(result['userId'])['sessionVersion']
        return jsonify(result or dict(message='Đã xóa người dùng.')), 201 if request.method == 'POST' else 200

    app.register_blueprint(auth)
    return store
