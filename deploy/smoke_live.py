"""Small synthetic live check on loopback; never prints credentials or document text."""
import httpx
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.fixtures import document_bytes, DEMO_BEFORE, DEMO_AFTER

with httpx.Client(base_url='http://127.0.0.1:8022', timeout=120) as client:
    def request(method, path, **kwargs):
        response = client.request(method, path, **kwargs)
        print(method, path.split('?')[0], response.status_code, flush=True)
        response.raise_for_status()
        return response.json()
    health = request('GET', '/api/health')
    assert health['live_ready'] and health['model_mode'] == 'live'
    comparison = request('POST', '/api/comparisons', json={'name':'Deployment smoke: synthetic live'})
    path = '/api/comparisons/' + comparison['id']
    for side,lines in [('before',DEMO_BEFORE),('after',DEMO_AFTER)]:
        request('POST',path+'/documents/'+side,files={'files':(side+'.docx',document_bytes(lines),'application/vnd.openxmlformats-officedocument.wordprocessingml.document')})
    result = request('POST', path+'/analyze')
    print('run_status:', result['run']['status'], 'findings:', len(result['findings']), 'summary:',result['summary'], 'api_usage:',result['metadata'].get('api_usage'),flush=True)
    assert result['run']['status'] in ('completed','partial') and result['findings']
    answer = request('POST', path+'/ask', json={'question':'Какие изменения найдены? Укажи источники.', 'run_id':result['run']['id']})
    print('answer_present:', bool(answer.get('answer')), 'tools:',len(answer.get('trace',[])),flush=True)
    assert answer.get('answer')
    report = client.get(path+'/report', params={'format':'html','run_id':result['run']['id']})
    assert report.status_code == 200
    print('PASS: live synthetic analysis, question and report',flush=True)
