import base64
import json
import struct
import sys
import unittest
import zlib
from pathlib import Path
from threading import RLock
from unittest.mock import MagicMock
from flask import Flask, g
from werkzeug.security import generate_password_hash

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app'))
from auth import AppError
from profile_features import validate_details, register_profile_features


def png(width=1,height=1):
    def chunk(kind,value):
        return struct.pack('>I',len(value))+kind+value+struct.pack('>I',zlib.crc32(kind+value)&0xffffffff)
    raw=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0\0\0\0\0'))+chunk(b'IEND',b'')
    return 'data:image/png;base64,'+base64.b64encode(raw).decode()


class ProfileValidationTests(unittest.TestCase):
    def test_valid_avatar_and_no_role_escalation(self):
        result=validate_details(dict(displayName=' Tên ',email='name@example.com',phone='+84 123456',avatar=png(),mapStyle='grayscale',estimatedSpeedKmh=30,role='admin',active=False))
        self.assertEqual(result['displayName'],'Tên')
        self.assertNotIn('role',result);self.assertNotIn('active',result)

    def test_invalid_input_and_avatar_rejected(self):
        for data in [[],dict(displayName=' '),dict(email='bad'),dict(phone='<script>'),dict(mapStyle='javascript:'),dict(estimatedSpeedKmh=True),dict(estimatedSpeedKmh=float('nan')),dict(estimatedSpeedKmh=0),dict(avatar='data:image/svg+xml;base64,QQ=='),dict(avatar=png(1024)),dict(avatar='data:image/png;base64,YQ==')]:
            with self.subTest(data=str(data)[:100]),self.assertRaises(AppError):validate_details(data)


class ProfileApiTests(unittest.TestCase):
    def setUp(self):
        self.app=Flask(__name__);self.app.secret_key='test';self.app.testing=True
        self.driver=MagicMock();self.db=self.driver.session.return_value.__enter__.return_value
        self.tx=MagicMock();self.db.execute_read.side_effect=lambda fn:fn(self.tx);self.db.execute_write.side_effect=lambda fn:fn(self.tx)
        self.app.extensions['traffic_lock']=RLock();self.store=MagicMock();self.app.extensions['auth_store']=self.store
        self.user=dict(userId='me',displayName='Name',username='name',role='user',passwordHash=generate_password_hash('password123'))
        self.store.by_id.return_value=self.user
        @self.app.before_request
        def user():g.user=self.user
        @self.app.errorhandler(AppError)
        def error(e):return {'error':str(e)},e.status
        register_profile_features(self.app,self.driver,'neo4j');self.client=self.app.test_client()

    def test_details_return_no_password_hash(self):
        response=self.client.get('/api/profile/details')
        self.assertEqual(response.status_code,200);self.assertNotIn('passwordHash',response.json)
        self.assertEqual(response.json['estimatedSpeedKmh'],30)

    def test_missing_owned_history_is_not_found(self):
        self.tx.run.return_value.single.return_value=None
        self.assertEqual(self.client.get('/api/profile/activity/other-person').status_code,404)
        self.assertEqual(self.tx.run.call_args.kwargs['uid'],'me')
        self.assertEqual(self.client.get('/api/profile/saved-routes/other-person').status_code,404)
        self.assertEqual(self.client.post('/api/profile/saved-routes',json=dict(historyId='other-person',routeIndex=0)).status_code,404)

    def test_normal_user_cannot_reopen_analysis_snapshot(self):
        self.tx.run.return_value.single.return_value={'a':{'id':'old','kind':'analysis','payload':'{}'}}
        self.assertEqual(self.client.get('/api/profile/activity/old').status_code,403)
        self.user['role']='admin'
        self.assertEqual(self.client.get('/api/profile/activity/old').status_code,200)

    def test_cannot_save_client_fabricated_snapshot(self):
        self.assertEqual(self.client.post('/api/profile/saved-routes',json={'routes':[{'totalDistanceM':1}]}).status_code,400)
        self.db.execute_write.assert_not_called()

    def test_delete_requires_password_and_preserves_last_admin(self):
        self.assertEqual(self.client.delete('/api/profile/account',json={}).status_code,400)
        self.tx.run.return_value.single.return_value={'u':self.user}
        self.assertEqual(self.client.delete('/api/profile/account',json=dict(confirm='DELETE',password='wrong')).status_code,400)
        self.user['role']='admin'
        self.tx.run.return_value.single.side_effect=[{'u':self.user},{'n':1}]
        self.assertEqual(self.client.delete('/api/profile/account',json=dict(confirm='DELETE',password='password123')).status_code,409)
        self.assertFalse(any('DETACH DELETE' in c.args[0] for c in self.tx.run.call_args_list))

    def test_saved_route_is_copied_from_owned_history(self):
        route={'totalDistanceM':200,'startPlace':{'name':'A'},'endPlace':{'name':'B'}}
        self.tx.run.return_value.single.side_effect=[{'payload':json.dumps({'routes':[route]})},None,{'n':0}]
        response=self.client.post('/api/profile/saved-routes',json=dict(historyId='owned',routeIndex=0,payload='fake'))
        self.assertEqual(response.status_code,200)
        calls=[c for c in self.tx.run.call_args_list if 'CREATE (u)-[:SAVED_ROUTE]' in c.args[0]]
        self.assertEqual(json.loads(calls[0].kwargs['payload']),route)
        self.assertEqual(calls[0].kwargs['uid'],'me')


if __name__=='__main__':unittest.main()
