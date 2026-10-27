import copy
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT=Path(__file__).resolve().parents[1] if Path(__file__).parent.name=='tests' else Path(r'D:\Nhu\HK7\NoSQL\Neo4jRouting')
sys.path.insert(0,str(ROOT/'tests'))
sys.path.insert(0,str(ROOT/'app'))
import test_console as fixtures
from auth import AppError
from user_exchange import export_users, validate_user_rows, import_users
from werkzeug.security import check_password_hash

ADMIN=dict(userId='u1',username='admin',displayName='Admin',role='admin',active=True,sessionVersion=1,passwordHash='secret',email='keep@example.com')
USER=dict(userId='u2',username='normal',displayName='Normal',role='user',active=True,sessionVersion=3,passwordHash='keep-hash')
def row(name='normal',**kwargs):
    return dict(username=name,displayName='New name',role='user',active=True,**kwargs)
def transaction(users):
    tx=MagicMock()
    def run(query,**params):
        if query.startswith('MATCH (u:AppUser) RETURN properties(u)'):
            return [dict(user=copy.deepcopy(u)) for u in users]
        return MagicMock()
    tx.run.side_effect=run
    return tx
def writes(tx):
    return [c for c in tx.run.call_args_list if 'props' in c.kwargs]

class UserExchangeTests(unittest.TestCase):
    def test_export_excludes_passwords_and_private_profile(self):
        result=export_users(transaction([ADMIN]))
        self.assertEqual(set(result[0]),{'username','displayName','role','active'})
        self.assertNotIn('secret',json.dumps(result))
        self.assertNotIn('keep@example.com',json.dumps(result))

    def test_duplicate_names_case_insensitive_rejected(self):
        with self.assertRaisesRegex(AppError,'trùng'):
            validate_user_rows([row('Normal'),row('normal')])

    def test_malformed_row_or_password_rejected(self):
        for rows,password in [([],''),([dict(row(),passwordHash='bad')],''),([dict(row(),active='true')],''),([dict(row(),role='owner')],''),([row()],'short')]:
            with self.subTest(rows=rows),self.assertRaises(AppError):validate_user_rows(rows,password)

    def test_new_password_is_hashed(self):
        rows,hashes=validate_user_rows([row('newuser')],'Initial123!')
        self.assertTrue(check_password_hash(hashes['newuser'],'Initial123!'))
        tx=transaction([ADMIN])
        self.assertEqual(import_users(tx,rows,hashes,'u1')['created'],1)
        self.assertNotEqual(writes(tx)[0].kwargs['props']['passwordHash'],'Initial123!')

    def test_existing_account_keeps_password_and_profile(self):
        rows,hashes=validate_user_rows([row()])
        tx=transaction([ADMIN,USER]);result=import_users(tx,rows,hashes,'u1')
        self.assertEqual(result['updated'],1)
        props=writes(tx)[0].kwargs['props']
        self.assertNotIn('passwordHash',props)
        self.assertNotIn('email',props)
        self.assertEqual(props['sessionVersion'],4)
        self.assertIn('SET u += $props',writes(tx)[0].args[0])

    def test_validation_finishes_before_account_writes(self):
        rows,hashes=validate_user_rows([row(),row('newuser')])
        tx=transaction([ADMIN,USER])
        with self.assertRaisesRegex(AppError,'mật khẩu ban đầu'):import_users(tx,rows,hashes,'u1')
        self.assertEqual(writes(tx),[])

    def test_actor_cannot_be_disabled_or_demoted(self):
        for active,role in [(False,'admin'),(True,'user')]:
            rows,hashes=validate_user_rows([dict(username='admin',displayName='Admin',active=active,role=role)])
            tx=transaction([ADMIN,USER])
            with self.assertRaises(AppError):import_users(tx,rows,hashes,'u1')
            self.assertEqual(writes(tx),[])

    def test_unchanged_account_does_not_invalidate_session(self):
        rows,hashes=validate_user_rows([{k:ADMIN[k] for k in ('username','displayName','role','active')}])
        tx=transaction([ADMIN]);self.assertEqual(import_users(tx,rows,hashes,'u1')['unchanged'],1)
        self.assertEqual(writes(tx),[])

class UserExchangeApiTests(unittest.TestCase):
    setUp=fixtures.ConsoleTests.setUp
    def upload(self,rows,password=None,headers=None):
        body={'file':(io.BytesIO(json.dumps(dict(format='urban-route-users-v1',rows=rows)).encode()),'users.json')}
        if password is not None:body['initialPassword']=password
        return self.client.post('/api/admin/exchange/users',data=body,headers=self.headers if headers is None else headers)

    def test_permissions_and_csrf(self):
        self.user['role']='user'
        self.assertEqual(self.client.get('/api/admin/exchange/users').status_code,403)
        self.assertEqual(self.upload([row()]).status_code,403)
        self.user['role']='admin'
        self.assertEqual(self.upload([row()],headers={}).status_code,403)

    def test_export_content_and_headers(self):
        tx=transaction([ADMIN]);self.db.execute_read.side_effect=lambda fn:fn(tx)
        response=self.client.get('/api/admin/exchange/users')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['format'],'urban-route-users-v1')
        self.assertNotIn('passwordHash',response.get_data(as_text=True))
        self.assertIn('users.json',response.headers['Content-Disposition'])
        self.assertEqual(response.headers['Cache-Control'],'no-store')

    def test_invalid_upload_does_not_write(self):
        response=self.upload([dict(row(),username='bad@email.com')])
        self.assertEqual(response.status_code,400)
        self.db.execute_write.assert_not_called()

    def test_import_creates_backup_and_reports_counts(self):
        tx=transaction([ADMIN,USER])
        self.app.extensions['road_graph_dirty']=False
        self.db.execute_write.side_effect=lambda fn:fn(tx)
        with patch('console.database_snapshot',return_value=fixtures.fixture()):
            self.db.execute_read.side_effect=lambda fn:fn(tx)
            response=self.upload([row()])
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        self.assertEqual(response.json['updated'],1)
        self.assertTrue((Path(self.temp.name)/'backups/database'/response.json['backup']).is_file())
        self.assertFalse(self.app.extensions['road_graph_dirty'])

    def test_users_page_contains_import_password_field(self):
        response=self.client.get('/admin/users')
        self.assertEqual(response.status_code,200)
        self.assertIn('name="initialPassword"',response.get_data(as_text=True))

if __name__=='__main__':unittest.main()
