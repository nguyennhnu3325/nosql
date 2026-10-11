import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from flask import Flask, jsonify
from werkzeug.security import generate_password_hash

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from auth import register_auth, validate_user, AppError

TEMPLATES = str(Path(__file__).resolve().parents[1] / 'app/templates')


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Flask(__name__, root_path=self.temp.name + '/app', template_folder=TEMPLATES)
        Path(self.app.root_path).mkdir()
        self.app.testing = True
        self.store = register_auth(self.app, MagicMock(), 'neo4j')
        self.store.has_users = MagicMock(return_value=True)
        self.store.by_id = MagicMock()
        self.store.by_username = MagicMock()
        self.store.change = MagicMock()
        self.app.add_url_rule('/', 'index', lambda: 'route search')
        self.app.add_url_rule('/api/route', 'route', lambda: jsonify(ok=True), methods=['POST'])
        self.app.add_url_rule('/api/admin/places', 'places', lambda: jsonify(ok=True), methods=['GET', 'POST'])
        self.client = self.app.test_client()
        self.user = dict(userId='u1', username='normal', displayName='Normal', role='user', active=True,
                         sessionVersion=1, passwordHash=generate_password_hash('long-password'))

    def sign_in(self, role='user', active=True, version=1):
        self.user.update(role=role, active=active)
        self.store.by_id.return_value = self.user
        with self.client.session_transaction() as session:
            session.update(user_id='u1', version=version, csrf='token')

    def test_anonymous_redirect_and_api_401(self):
        self.assertEqual(self.client.get('/').status_code, 302)
        self.assertEqual(self.client.get('/api/admin/places').status_code, 401)

    def test_user_cannot_read_or_write_admin_endpoints(self):
        self.sign_in()
        self.assertEqual(self.client.get('/api/admin/users').status_code, 403)
        self.assertEqual(self.client.post('/api/admin/places', headers={'X-CSRF-Token': 'token'}).status_code, 403)
        self.assertEqual(self.client.get('/admin/users').status_code, 403)

    def test_user_can_search_with_csrf(self):
        self.sign_in()
        self.assertEqual(self.client.post('/api/route', headers={'X-CSRF-Token': 'token'}).status_code, 200)
        self.assertEqual(self.client.post('/api/route').status_code, 403)

    def test_admin_has_access_but_requires_csrf(self):
        self.sign_in('admin')
        self.assertEqual(self.client.get('/api/admin/places').status_code, 200)
        self.assertEqual(self.client.post('/api/admin/places').status_code, 403)
        self.assertEqual(self.client.post('/api/admin/places', headers={'X-CSRF-Token': 'token'}).status_code, 200)

    def test_disabled_and_stale_sessions_are_rejected(self):
        self.sign_in(active=False)
        self.assertEqual(self.client.get('/api/admin/places').status_code, 401)
        self.sign_in(version=0)
        self.assertEqual(self.client.get('/api/admin/places').status_code, 401)

    def test_login_rotates_session_and_logout_clears_it(self):
        self.store.by_username.return_value = self.user
        self.client.get('/login')
        with self.client.session_transaction() as session:
            token = session['csrf']
        response = self.client.post('/login', data=dict(username='NORMAL', password='long-password', csrf_token=token))
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertEqual(session['user_id'], 'u1')
            self.assertNotEqual(session['csrf'], token)
            new_token = session['csrf']
        self.store.by_id.return_value = self.user
        self.assertEqual(self.client.post('/logout', data=dict(csrf_token=new_token)).status_code, 302)
        with self.client.session_transaction() as session:
            self.assertNotIn('user_id', session)

    def test_login_wrong_password(self):
        self.store.by_username.return_value = self.user
        self.client.get('/login')
        with self.client.session_transaction() as session:
            token = session['csrf']
        response = self.client.post('/login', data=dict(username='normal', password='wrong', csrf_token=token))
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as session:
            self.assertNotIn('user_id', session)

    def test_first_run_redirects_to_setup(self):
        self.store.has_users.return_value = False
        self.assertTrue(self.client.get('/login').location.endswith('/setup'))

    def registration(self, **extra):
        self.client.get('/register')
        with self.client.session_transaction() as session:
            token = session['csrf']
        return self.client.post('/register', data=dict(username='new_user', displayName='New User',
            password='password123', confirmPassword='password123', csrf_token=token, **extra))

    def test_registration_always_creates_normal_user(self):
        response = self.registration(role='admin', active='false')
        self.assertEqual(response.status_code, 302)
        self.assertIn('registered=1', response.location)
        values = self.store.change.call_args.args[0]
        self.assertEqual(values['role'], 'user')
        self.assertTrue(values['active'])

    def test_registration_mismatch_rejected_without_write(self):
        self.client.get('/register')
        with self.client.session_transaction() as session:
            token = session['csrf']
        response = self.client.post('/register', data=dict(username='new_user', displayName='New',
            password='password123', confirmPassword='different', csrf_token=token))
        self.assertEqual(response.status_code, 400)
        self.store.change.assert_not_called()

    def test_registration_duplicate_username(self):
        self.store.change.side_effect = AppError('Duplicate', 409)
        self.assertEqual(self.registration().status_code, 409)

    def test_registration_requires_csrf_and_initial_admin(self):
        self.assertEqual(self.client.post('/register', data=dict(username='new')).status_code, 403)
        self.store.has_users.return_value = False
        self.assertTrue(self.client.get('/register').location.endswith('/setup'))

    def test_reject_invalid_role_and_weak_password(self):
        valid = dict(username='user', displayName='User', password='password123')
        for values in ({**valid, 'role': 'superuser'}, {**valid, 'password': 'short'}, {**valid, 'active': 'false'}):
            with self.assertRaises(AppError):
                validate_user(values, creating=True)


if __name__ == '__main__':
    unittest.main()
