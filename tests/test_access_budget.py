import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import auth
from app.model_adapter import ModelAdapter, ModelError


@pytest.fixture
def protected(monkeypatch):
    monkeypatch.setenv('BAQBAQ_AUTH_PASSWORD_HASH', auth.password_hash('test-jury-access'))
    monkeypatch.setenv('BAQBAQ_AUTH_SECRET', 'test-secret-for-this-process-only')
    auth._attempts.clear()
    return TestClient(app, base_url='https://testserver', follow_redirects=False)


def test_public_preview_and_private_api(protected):
    for path in ('/', '/landing.js', '/demo/result.json', '/assets/baqbaq.png', '/login.html'):
        assert protected.get(path).status_code == 200
    assert protected.get('/workspace').status_code == 303
    for path in ('/api/comparisons', '/api/health', '/docs', '/openapi.json', '/app.js'):
        assert protected.get(path).status_code == 401
    assert protected.post('/api/comparisons', json={'name':'forbidden'}).status_code == 401


def test_session_signed_secure_and_csrf(protected):
    assert protected.post('/api/session',json={'password':'wrong'}).status_code == 401
    reply=protected.post('/api/session',json={'password':'test-jury-access'})
    assert reply.status_code == 200
    cookie=reply.headers['set-cookie'].lower()
    assert 'secure' in cookie and 'httponly' in cookie and 'samesite=lax' in cookie
    assert protected.get('/workspace').status_code == 200
    assert protected.get('/api/comparisons').headers['cache-control']=='no-store'
    assert protected.post('/api/comparisons',json={'name':'evil origin'},headers={'Origin':'https://evil.example'}).status_code == 403
    protected.cookies.clear()
    protected.cookies.set(auth.COOKIE,'123.any.forged')
    assert protected.get('/api/comparisons').status_code == 401


def test_login_rate_limit_and_fail_closed(protected,monkeypatch):
    for _ in range(10): protected.post('/api/session',json={'password':'wrong'})
    assert protected.post('/api/session',json={'password':'wrong'}).status_code==429
    monkeypatch.delenv('BAQBAQ_AUTH_SECRET')
    assert protected.get('/api/comparisons').status_code==503


def test_missing_hash_is_fail_closed_and_logout_ends_session(protected,monkeypatch):
    protected.post('/api/session',json={'password':'test-jury-access'}).raise_for_status()
    assert protected.get('/api/health').status_code==200
    protected.delete('/api/session').raise_for_status()
    assert protected.get('/api/health').status_code==401
    monkeypatch.delenv('BAQBAQ_AUTH_PASSWORD_HASH')
    assert protected.get('/api/health').status_code==503


def test_request_cap_is_persistent_and_failures_count(monkeypatch,tmp_path):
    from types import SimpleNamespace
    from app import budget
    monkeypatch.setattr(budget,'settings',SimpleNamespace(db_path=tmp_path/'main.db'))
    monkeypatch.setenv('BAQBAQ_DAILY_API_REQUESTS','2')
    budget.reserve_request(); budget.reserve_request()
    with pytest.raises(RuntimeError,match='Дневной лимит'): budget.reserve_request()
    (tmp_path/'api-budget.sqlite3').unlink()  # Windows also detects leaked connection handles.


def test_job_limit_prevents_provider_call(monkeypatch):
    monkeypatch.setenv('BAQBAQ_JOB_API_REQUESTS','0')
    with pytest.raises(ModelError,match='одного задания'):
        ModelAdapter('live','test').request(None,input='should never be sent')
