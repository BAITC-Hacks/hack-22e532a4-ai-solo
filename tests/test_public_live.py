from fastapi.testclient import TestClient
from app.main import app, store

def test_public_live_browser_isolation(monkeypatch):
    monkeypatch.setenv('BAQBAQ_PUBLIC_LIVE','1')
    with TestClient(app) as a, TestClient(app) as b:
        assert a.get('/').status_code == 200
        assert 'baqbaq_workspace' in a.cookies
        response=a.post('/api/comparisons',json={'name':'Browser A','model_mode':'offline'},headers={'Idempotency-Key':'same-test-key'})
        assert response.status_code == 201
        own=response.json()['id']
        assert response.json()['model_mode']=='live'
        assert a.get('/api/comparisons/'+own).status_code==200
        assert b.get('/api/comparisons/'+own).status_code==404
        assert b.get('/api/comparisons').json()==[]
        other=b.post('/api/comparisons',json={'name':'Browser A'},headers={'Idempotency-Key':'same-test-key'}).json()['id']
        assert other!=own
        assert a.get('/api/comparisons/'+other+'/result').status_code==404
        assert a.post('/api/findings/missing/review',json={'decision':'confirmed'}).status_code==404
        assert a.post('/api/comparisons/'+own+'/demo').status_code==404
        assert a.get('/demo/result.json').status_code==404
        assert a.post('/api/comparisons',json={'name':'Cross site'},headers={'Origin':'https://elsewhere.invalid'}).status_code==403
        health=a.get('/api/health').json()
        assert health['protected'] is False and health['public_live'] is True

def test_old_unowned_comparisons_remain_private(monkeypatch):
    old,_=store.create_comparison('Old private comparison','offline')
    monkeypatch.setenv('BAQBAQ_PUBLIC_LIVE','1')
    with TestClient(app) as client:
        assert client.get('/api/comparisons/'+old['id']).status_code==404
        assert old['id'] not in {x['id'] for x in client.get('/api/comparisons').json()}
