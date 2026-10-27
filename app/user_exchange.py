"""Validated, atomic exchange of account metadata; passwords never leave the server."""
from uuid import uuid4
from werkzeug.security import generate_password_hash
try:
    from .auth import AppError, validate_user
except ImportError:
    from auth import AppError, validate_user

USER_FIELDS = ('username', 'displayName', 'role', 'active')


def export_users(tx):
    return [{key: record['user'].get(key) for key in USER_FIELDS}
            for record in tx.run('MATCH (u:AppUser) RETURN properties(u) AS user ORDER BY u.username')]


def validate_user_rows(rows, initial_password=''):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
        raise AppError('Tệp phải chứa từ 1 đến 200 người dùng.')
    if not isinstance(initial_password, str) or (initial_password and not 8 <= len(initial_password) <= 128):
        raise AppError('Mật khẩu ban đầu phải có từ 8 đến 128 ký tự.')
    result, names = [], set()
    for index, row in enumerate(rows, 1):
        try:
            if not isinstance(row, dict) or set(row) != set(USER_FIELDS):
                raise AppError('Mỗi dòng phải có đúng username, displayName, role và active; không chứa mật khẩu.')
            values = validate_user(row)
            if values['username'] in names:
                raise AppError('Tên đăng nhập bị trùng trong tệp.')
            names.add(values['username'])
            result.append({key: values[key] for key in USER_FIELDS})
        except AppError as error:
            raise AppError(f'Dòng {index}: {error}', error.status) from error
    # Precompute independent salted hashes before holding the account-state lock.
    hashes = {row['username']: generate_password_hash(initial_password) for row in result} if initial_password else {}
    return result, hashes


def import_users(tx, rows, hashes, actor_id):
    tx.run("MERGE (s:AuthState {key: 'users'}) ON CREATE SET s.revision = 0 SET s.revision = s.revision + 1").consume()
    existing = {r['user']['username']: dict(r['user']) for r in tx.run('MATCH (u:AppUser) RETURN properties(u) AS user')}
    actor = next((u for u in existing.values() if u.get('userId') == actor_id), None)
    if not actor or actor.get('role') != 'admin' or not actor.get('active'):
        raise AppError('Tài khoản quản trị không còn hợp lệ. Hãy đăng nhập lại.', 403)
    proposed = {name: dict(u) for name, u in existing.items()}
    for row in rows:
        name = row['username']
        if name not in existing and name not in hashes:
            raise AppError('Cần nhập mật khẩu ban đầu để tạo tài khoản mới: ' + name)
        if name == actor['username'] and (row['role'] != 'admin' or not row['active']):
            raise AppError('Không thể tự khóa hoặc hạ quyền tài khoản đang đăng nhập.', 409)
        proposed[name] = {**proposed.get(name, {}), **row}
    if not any(u.get('role') == 'admin' and u.get('active') for u in proposed.values()):
        raise AppError('Phải giữ lại ít nhất một admin đang hoạt động.', 409)
    created = updated = unchanged = 0
    for row in rows:
        old = existing.get(row['username'])
        if old and all(old.get(key) == row[key] for key in USER_FIELDS):
            unchanged += 1
            continue
        props = dict(row)
        if old:
            identity = old['userId']
            props['sessionVersion'] = old.get('sessionVersion', 0) + 1
            query = 'MATCH (u:AppUser {userId: $id}) SET u += $props'
            updated += 1
        else:
            identity = uuid4().hex
            props.update(passwordHash=hashes[row['username']], sessionVersion=1)
            query = 'CREATE (u:AppUser {userId: $id}) SET u += $props'
            created += 1
        tx.run(query, id=identity, props=props).consume()
    return dict(created=created, updated=updated, unchanged=unchanged)
