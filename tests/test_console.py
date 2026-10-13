import copy
import io
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch
from flask import Flask
from werkzeug.security import generate_password_hash
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from auth import register_auth, AppError
from place_admin import register_place_admin
from traffic_admin import register_traffic
from console import register_console, validate_database, DB_FORMAT, restore_database


def fixture():
    return dict(format=DB_FORMAT,nodes=[dict(id='a',labels=['AppUser'],properties=dict(userId='u1',username='admin',displayName='Admin',role='admin',active=True,passwordHash='hashed',sessionVersion=1)),dict(id='j',labels=['Junction'],properties=dict(junctionId=1,latitude=10.8,longitude=106.6))],relationships=[])


class DatabaseValidationTests(unittest.TestCase):
    def test_valid_database_and_restore_rotates_sessions(self):
        data=validate_database(fixture(),'u1')
        tx=MagicMock()
        tx.run.return_value.single.return_value={'id':'new-id'}
        restore_database(tx,data)
        self.assertTrue(any('sessionVersion' in c.args[0] for c in tx.run.call_args_list))

    def test_invalid_backup_rejected(self):
        for edit in [lambda d:d.update(format='other'),lambda d:d['nodes'].append(copy.deepcopy(d['nodes'][0])),lambda d:d['nodes'][1].update(labels=['Junction) DETACH DELETE n //']),lambda d:d['nodes'][0]['properties'].update(active=False),lambda d:d['relationships'].append(dict(source='a',target='missing',type='ROAD',properties={})),lambda d:d['relationships'].append(dict(source='a',target='j',type='X] DELETE n',properties={})),lambda d:d['nodes'][1]['properties'].update(latitude=float('nan'))]:
            data=fixture();edit(data)
            with self.subTest(data=data), self.assertRaises(AppError):validate_database(data,'u1')


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(__file__).resolve().parents[1]
        self.app=Flask(__name__,root_path=self.temp.name+'/app',template_folder=str(root/'app/templates'),static_folder=str(root/'app/static'))
        Path(self.app.root_path).mkdir();self.app.testing=True
        self.driver=MagicMock();self.db=self.driver.session.return_value.__enter__.return_value
        self.store=register_auth(self.app,self.driver,'neo4j')
        self.store.has_users=MagicMock(return_value=True)
        self.user=dict(userId='u1',username='admin',displayName='Admin',role='admin',active=True,sessionVersion=1,passwordHash=generate_password_hash('password123'))
        self.store.by_id=MagicMock(return_value=self.user)
        register_place_admin(self.app,self.driver,'neo4j');register_traffic(self.app,self.driver,'neo4j');register_console(self.app,self.driver,'neo4j')
        self.client=self.app.test_client()
        with self.client.session_transaction() as session:session.update(user_id='u1',version=1,csrf='test')
        self.headers={'X-CSRF-Token':'test'}

    def test_new_pages_render(self):
        for path in ['/admin','/admin/users','/admin/network','/admin/traffic','/admin/system','/admin/settings','/profile','/network','/analysis']:
            with self.subTest(path=path):self.assertEqual(self.client.get(path).status_code,200)

    def test_admin_endpoints_forbidden_to_users(self):
        self.user['role']='user'
        for path in ['/api/admin/settings','/api/admin/system','/api/admin/database/backups','/api/admin/exchange/places','/admin/network']:
            with self.subTest(path=path):self.assertEqual(self.client.get(path).status_code,403)
        self.assertEqual(self.client.get('/profile').status_code,200)

    def test_csrf_enforced_on_new_writes(self):
        for path in ['/api/admin/users/u1/reset-password','/api/admin/database/restore','/api/admin/exchange/places','/api/admin/place-preview']:
            self.assertEqual(self.client.post(path).status_code,403)
        self.db.execute_write.assert_not_called()

    def test_profile_cannot_escalate_role(self):
        self.user['role']='user'
        self.db.execute_write.return_value=self.user
        response=self.client.put('/api/profile',headers=self.headers,json=dict(displayName='New',role='admin',active=False))
        self.assertEqual(response.status_code,200)
        tx=MagicMock();tx.run.return_value.single.return_value={'u':self.user}
        self.db.execute_write.call_args.args[0](tx)
        self.assertEqual(tx.run.call_args.kwargs['props'],dict(displayName='New'))

    def test_profile_password_requires_current_password(self):
        response=self.client.put('/api/profile',headers=self.headers,json=dict(displayName='Admin',password='newpassword',currentPassword='wrong'))
        self.assertEqual(response.status_code,400)
        self.db.execute_write.assert_not_called()

    def test_invalid_settings_do_not_write(self):
        for value in [True,0,10001,'500',None]:
            self.assertEqual(self.client.put('/api/admin/settings',headers=self.headers,json=dict(accessWarningM=value)).status_code,400)
        self.db.execute_write.assert_not_called()

    def test_restore_requires_confirmation_and_valid_file(self):
        self.assertEqual(self.client.post('/api/admin/database/restore',headers=self.headers,data={}).status_code,400)
        response=self.client.post('/api/admin/database/restore',headers=self.headers,data={'confirm':'RESTORE','file':(io.BytesIO(b'{}'),'backup.json')})
        self.assertEqual(response.status_code,400)
        self.db.execute_write.assert_not_called()

    def test_backup_path_traversal_rejected(self):
        self.assertEqual(self.client.get('/api/admin/database/backups/evil.json').status_code,400)

    def test_invalid_import_rejected_before_backup_and_write(self):
        data=dict(format='urban-route-roads-v1',rows=[dict(source=1,target=2,properties=dict(roadId='r',lengthM=-1))])
        response=self.client.post('/api/admin/exchange/roads',headers=self.headers,data={'file':(io.BytesIO(json.dumps(data).encode()),'roads.json')})
        self.assertEqual(response.status_code,400)
        self.db.execute_write.assert_not_called()

    def test_new_junction_gets_database_generated_id(self):
        tx = MagicMock()
        counter, missing, saved = MagicMock(), MagicMock(), MagicMock()
        counter.single.return_value = {'id': 42}
        missing.single.return_value = None
        tx.run.side_effect = [counter, missing, saved]
        self.db.execute_write.side_effect = lambda fn, *args: fn(tx, *args)
        with patch('traffic_admin.relink_places'):
            response = self.client.post('/api/admin/traffic/junctions', headers=self.headers,
                                        json=dict(latitude=10.8, longitude=106.6))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['junctionId'], 42)
        self.assertEqual(tx.run.call_args.kwargs['props']['junctionId'], 42)

    def test_new_road_gets_database_generated_id(self):
        tx = MagicMock()
        def query(text, **params):
            result = MagicMock()
            result.single.return_value = ({'id': 'generated-road'} if 'randomUUID' in text
                                          else {'distance': 123.456} if 'AS distance' in text
                                          else {'id': params['id']} if 'RETURN j.junctionId' in text else None)
            return result
        tx.run.side_effect = query
        self.db.execute_write.side_effect = lambda fn, *args: fn(tx, *args)
        response = self.client.post('/api/admin/traffic/roads', headers=self.headers,
                                    json=dict(source=1, target=2, properties=dict(lengthM=30)))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['roadId'], 'generated-road')
        self.assertEqual(tx.run.call_args.kwargs['props']['roadId'], 'generated-road')
        self.assertEqual(tx.run.call_args.kwargs['props']['lengthM'], 123.46)

    def test_road_rejects_identical_endpoints_before_write(self):
        response = self.client.post('/api/admin/traffic/roads', headers=self.headers,
                                    json=dict(source='1', target=1, properties=dict(lengthM=30)))
        self.assertEqual(response.status_code, 400)
        self.db.execute_write.assert_not_called()

    def test_invalid_place_import_returns_validation_error(self):
        data=dict(format='urban-route-places-v1',rows=[dict(placeId='p',name='',category='test',latitude=10.8,longitude=106.6)])
        response=self.client.post('/api/admin/exchange/places',headers=self.headers,data={'file':(io.BytesIO(json.dumps(data).encode()),'places.json')})
        self.assertEqual(response.status_code,400)
        self.db.execute_write.assert_not_called()

    def test_reset_password_invalidates_prior_sessions(self):
        self.db.execute_write.return_value=2
        response=self.client.post('/api/admin/users/u1/reset-password',headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertGreaterEqual(len(response.json['password']),8)
        tx=MagicMock();tx.run.return_value.single.return_value={'version':2}
        self.db.execute_write.call_args.args[0](tx)
        self.assertIn('sessionVersion',tx.run.call_args.args[0])
        self.assertNotEqual(tx.run.call_args.kwargs['hash'],response.json['password'])
        with self.client.session_transaction() as session:self.assertEqual(session['version'],2)

if __name__=='__main__':unittest.main()
