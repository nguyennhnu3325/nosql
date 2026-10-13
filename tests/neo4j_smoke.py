"""Opt-in integration checks. Every graph change is rolled back.
Run: python tests/neo4j_smoke.py
Requires a running Neo4j with the application's traffic data.
"""
import io
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from flask import Flask
from neo4j import GraphDatabase
from werkzeug.security import check_password_hash
from auth import AppError, register_auth
from place_admin import register_place_admin
from console import register_console, DB_FORMAT
from traffic_admin import register_traffic, snapshot, validate_snapshot, write_snapshot


class TransactionDriver:
    """Reuse one explicit transaction across requests without committing it."""
    def __init__(self, tx):
        self.tx = tx

    def verify_connectivity(self):
        self.tx.run("RETURN 1 AS ok").consume()

    def session(self, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute_read(self, fn, *args, **kwargs):
        return fn(self.tx, *args, **kwargs)

    execute_write = execute_read

    def run(self, *args, **kwargs):
        return self.tx.run(*args, **kwargs)


def check(response, expected=200):
    assert response.status_code == expected, (response.status_code, response.get_data(as_text=True))
    return response.get_json()


def main():
    driver = GraphDatabase.driver(os.environ.get('NEO4J_URI', 'bolt://localhost:7687'),
        auth=(os.environ.get('NEO4J_USER', 'neo4j'), os.environ.get('NEO4J_PASSWORD', '12345678')))
    with driver.session(database=os.environ.get('NEO4J_DATABASE', 'neo4j')) as db:
        tx = db.begin_transaction()
        try:
            original = validate_snapshot(snapshot(tx))
            facade = TransactionDriver(tx)
            with TemporaryDirectory() as directory:
                Path(directory, 'app').mkdir()
                templates = str(Path(__file__).resolve().parents[1] / 'app/templates')
                app = Flask(__name__, root_path=str(Path(directory, 'app')), template_folder=templates)
                app.testing = True
                store = register_auth(app, facade, 'neo4j')
                store.ready = True
                tx.run("MERGE (s:AuthState {key: 'users'}) ON CREATE SET s.revision = 0").consume()
                register_place_admin(app, facade, 'neo4j')
                register_traffic(app, facade, 'neo4j')
                register_console(app, facade, 'neo4j')
                client = app.test_client()
                admin = store.change(dict(username='smoke_admin_20261009', displayName='Test Admin',
                                          password='test-password-123', role='admin'))
                full = store.by_id(admin['userId'])
                assert check_password_hash(full['passwordHash'], 'test-password-123')
                assert full['passwordHash'] != 'test-password-123'
                with client.session_transaction() as session:
                    session.update(user_id=admin['userId'], version=full['sessionVersion'], csrf='smoke')
                headers = {'X-CSRF-Token': 'smoke'}
                for path in ('/admin', '/admin/users', '/admin/traffic', '/network', '/analysis'):
                    check(client.get(path))
                visitor = app.test_client()
                check(visitor.get('/register'))
                with visitor.session_transaction() as session:
                    signup_token = session['csrf']
                signup = dict(username='smoke_registered_20261009', displayName='Registered User',
                              password='test-password-123', confirmPassword='test-password-123',
                              role='admin', csrf_token=signup_token)
                check(visitor.post('/register', data=signup), 302)
                registered = store.by_username(signup['username'])
                assert registered['role'] == 'user'
                check(visitor.post('/register', data=signup), 409)
                check(visitor.post('/login', data=dict(username=signup['username'],
                    password=signup['password'], csrf_token=signup_token)), 302)
                check(visitor.get('/network'))
                check(visitor.get('/api/admin/users'), 403)
                with visitor.session_transaction() as session:
                    logout_token = session['csrf']
                check(visitor.post('/logout', data=dict(csrf_token=logout_token)), 302)
                user = check(client.post('/api/admin/users', json=dict(username='smoke_user_20261009',
                    displayName='Test User', password='test-password-123', role='user'), headers=headers), 201)
                assert 'passwordHash' not in user
                normal = app.test_client()
                with normal.session_transaction() as session:
                    session.update(user_id=user['userId'], version=store.by_id(user['userId'])['sessionVersion'], csrf='normal')
                check(normal.get('/network'))
                check(normal.get('/analysis'))
                check(normal.get('/api/network'))
                check(normal.get('/api/analysis'))
                check(normal.get('/api/admin/traffic/export'), 403)
                check(normal.get('/api/admin/users'), 403)
                check(normal.post('/api/admin/traffic/backups', headers={'X-CSRF-Token': 'normal'}), 403)
                check(client.put('/api/admin/users/' + user['userId'], json=dict(username=user['username'],
                    displayName='Renamed', password='', role='admin', active=True), headers=headers))
                check(normal.get('/api/network'), 401)
                check(client.delete('/api/admin/users/' + user['userId'], headers=headers))
                check(client.delete('/api/admin/users/' + admin['userId'], headers=headers), 409)
                if len([u for u in store.users() if u['role'] == 'admin' and u['active']]) == 1:
                    try:
                        store.change(dict(username=admin['username'], displayName='Test Admin', role='user', active=True),
                                     user_id=admin['userId'])
                    except AppError as error:
                        assert error.status == 409
                    else:
                        raise AssertionError('Last admin should not be demoted')
                network = check(client.get('/api/network'))
                analysis = check(client.get('/api/analysis'))
                assert analysis['junctionCount'] == len(network['junctions'])
                exported = check(client.get('/api/admin/traffic/export'))
                validate_snapshot(exported)
                jid = 900000000000001
                check(client.post('/api/admin/traffic/junctions', json=dict(junctionId=jid, latitude=10.8, longitude=106.6), headers=headers))
                check(client.put('/api/admin/traffic/junctions/' + str(jid), json=dict(junctionId=jid, latitude=10.81, longitude=106.61), headers=headers))
                road = dict(source=jid, target=original['junctions'][0]['junctionId'], properties=dict(roadId='smoke_road', name='Smoke', highway='test', lengthM=100))
                check(client.post('/api/admin/traffic/roads', json=road, headers=headers))
                road['properties']['lengthM'] = 120
                check(client.put('/api/admin/traffic/roads/smoke_road', json=road, headers=headers))
                check(client.delete('/api/admin/traffic/roads/smoke_road', headers=headers))
                check(client.delete('/api/admin/traffic/junctions/' + str(jid), headers=headers))
                saved = check(client.post('/api/admin/traffic/backups', headers=headers), 201)
                check(client.get('/api/admin/traffic/backups'))
                check(client.get('/api/admin/traffic/backups/' + saved['name']))
                check(client.post('/api/admin/traffic/restore', data=dict(name=saved['name'], confirm='wrong'), headers=headers), 400)
                imported = check(client.post('/api/admin/traffic/import', data={'file': (io.BytesIO(json.dumps(exported).encode()), 'traffic.json')}, headers=headers))
                assert imported['backup']
                restored = check(client.post('/api/admin/traffic/restore', data=dict(name=saved['name'], confirm='RESTORE'), headers=headers))
                assert restored['backup']
                assert store.by_id(admin['userId'])['role'] == 'admin'
                # Verify exact graph snapshot roundtrip inside the rollback-only transaction.
                write_snapshot(tx, original, True)
                after = validate_snapshot(snapshot(tx))
                assert after == original
                explorer = check(client.get('/api/explorer'))
                assert len(explorer['places']) == len(original['places'])
                details = check(client.get('/api/analysis/details'))
                assert details['accessCount'] == len(original['access'])
                check(client.get('/admin/network'))
                check(client.get('/admin/system'))
                check(client.get('/admin/settings'))
                check(client.get('/profile'))
                check(client.get('/api/admin/system'))
                check(client.put('/api/admin/settings',json=dict(accessWarningM=350),headers=headers))
                assert check(client.get('/api/admin/settings'))['accessWarningM']==350
                first = original['junctions'][0]
                place_values=dict(name='Smoke place',category='test',latitude=first['latitude'],longitude=first['longitude'])
                preview=check(client.post('/api/admin/place-preview',json=place_values,headers=headers))
                assert preview['distance'] < 1
                place=check(client.post('/api/admin/places',json=place_values,headers=headers),201)
                place_values['latitude']=0
                check(client.post('/api/admin/places',json=place_values,headers=headers),400)
                check(client.delete('/api/admin/places/'+place['placeId'],headers=headers))
                profile=check(client.put('/api/profile',json=dict(displayName='Smoke renamed'),headers=headers))
                assert profile['displayName']=='Smoke renamed'
                exchange=check(client.get('/api/admin/exchange/places'))
                check(client.post('/api/admin/exchange/places',data={'file':(io.BytesIO(json.dumps(exchange).encode()),'places.json')},headers=headers))
                saved_db=check(client.post('/api/admin/database/backups',headers=headers),201)
                check(client.get('/api/admin/database/backups/'+saved_db['name']))
                # Restore a tiny application DB in this explicit rollback-only transaction.
                current=store.by_id(admin['userId'])
                small=dict(format=DB_FORMAT,nodes=[dict(id='admin',labels=['AppUser'],properties=current),dict(id='state',labels=['AuthState'],properties=dict(key='users',revision=0)),dict(id='j',labels=['Junction'],properties=dict(junctionId=1,latitude=10.8,longitude=106.6)),dict(id='p',labels=['Place'],properties=dict(placeId='smoke-restored',name='Restored',category='test',latitude=10.8,longitude=106.6))],relationships=[dict(source='p',target='j',type='ACCESS_TO',properties=dict(accessDistanceM=0))])
                check(client.post('/api/admin/database/restore',data={'confirm':'RESTORE','file':(io.BytesIO(json.dumps(small).encode()),'database.json')},headers=headers))
                restored_data=check(client.get('/api/explorer'))
                assert len(restored_data['places'])==1 and len(restored_data['access'])==1
                assert store.by_id(admin['userId'])['sessionVersion'] > current['sessionVersion']
                print('Neo4j integration OK: users, roles, traffic CRUD, explorer, analysis, place bounds, settings, profile, scoped import/export, database backup/restore; all writes rolled back.')
        finally:
            tx.rollback()
    driver.close()


if __name__ == '__main__':
    main()
