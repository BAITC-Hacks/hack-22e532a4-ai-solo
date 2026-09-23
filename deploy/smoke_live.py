"""Small synthetic live check on loopback; never prints credentials or document text."""
import httpx

with httpx.Client(base_url='http://127.0.0.1:8022', timeout=600) as client:
    def request(method, path, **kwargs):
        response = client.request(method, path, **kwargs)
        print(method, path.split('?')[0], response.status_code, flush=True)
        response.raise_for_status()
        return response.json()
    health = request('GET', '/api/health')
    assert health['live_ready'] and health['model_mode'] == 'live'
    comparison = request('POST', '/api/comparisons', json={'name':'Deployment smoke: synthetic live'})
    path = '/api/comparisons/' + comparison['id']
    demo = request('POST', path+'/demo')
    assert demo.get('dataset') == 'synthetic'
    result = request('POST', path+'/analyze')
    print('run_status:', result['run']['status'], 'findings:', len(result['findings']), flush=True)
    assert result['run']['status'] in ('completed','partial') and result['findings']
    answer = request('POST', path+'/ask', json={'question':'Какие изменения найдены? Укажи источники.', 'run_id':result['run']['id']})
    print('answer_present:', bool(answer.get('answer')), 'tools:',len(answer.get('trace',[])),flush=True)
    assert answer.get('answer')
    report = client.get(path+'/report', params={'format':'html','run_id':result['run']['id']})
    assert report.status_code == 200
    print('PASS: live synthetic analysis, question and report',flush=True)
