import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from app.main import app, store
from app.fixtures import document_bytes
from app.model_adapter import ModelAdapter, ModelError
from app.gateway import Gateway
from app.agent import answer_with_tools
from scripts.evaluate_documents import run_case


CASES = json.loads((Path(__file__).resolve().parents[1]/'benchmarks/document_cases.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', CASES, ids=[c['id'] for c in CASES])
def test_document_pipeline(case):
    result = run_case(TestClient(app), case)
    assert result['errors'] == []


def seeded():
    client = TestClient(app)
    cid = client.post('/api/comparisons',json={'name':'Agent regression'}).json()['id']
    data = document_bytes(['Директор закупок:', 'Контролирует качество закупочной деятельности.'])
    for side in ('before','after'):
        client.post(f'/api/comparisons/{cid}/documents/{side}',files={'files':('test.docx',data)}).raise_for_status()
    result=client.post(f'/api/comparisons/{cid}/analyze').json()
    sid=next(f['before_span_id'] for f in result['findings'] if f.get('before_span_id'))
    return cid, result['run']['id'], sid


@pytest.mark.parametrize('reply', ['not json', '{"answer":"unsupported","source_ids":["foreign"]}', '{"answer":"unsupported","source_ids":[]}'])
def test_invalid_model_reply_is_rejected(reply, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','test-only')
    model=MagicMock(); model.responses.create.return_value.output_text=reply
    evidence=[{'id':'allowed','filename':'x','revision':'1','clause_label':'1','locator':'1','original_text':'source'}]
    with patch('openai.OpenAI',return_value=model), pytest.raises(ModelError):
        ModelAdapter('live','test').answer('question',evidence)


def test_live_agent_really_dispatches_tool_and_renders_exact_source(monkeypatch):
    cid,run,sid=seeded()
    monkeypatch.setenv('OPENAI_API_KEY','test-only')
    model=MagicMock()
    model.responses.create.side_effect=[
        SimpleNamespace(output=[SimpleNamespace(type='function_call',name='read_source',arguments=json.dumps({'value':sid}),call_id='call1')],output_text=''),
        SimpleNamespace(output=[],output_text=json.dumps({'answer':'Найдена обязанность контроля.','source_ids':[sid],'status':'MATCH'})),
    ]
    gateway=Gateway(store,cid,run)
    with patch('openai.OpenAI',return_value=model):
        reply=answer_with_tools(ModelAdapter('live','test'),gateway,'Кто контролирует?')
    assert gateway.trace[0]['tool']=='read_source'
    assert gateway.trace[0]['status']=='ok'
    assert store.get_span(sid,cid)['original_text'] in reply.text
    assert model.responses.create.call_count==2


def test_gateway_rejects_foreign_source_and_records_error():
    cid,run,sid=seeded()
    other,_=store.create_comparison('Other scope','offline')
    gateway=Gateway(store,other['id'])
    with pytest.raises(ValueError): gateway.call('read_source',sid)
    assert gateway.trace[-1]['status']=='error'


def test_agent_iteration_limit(monkeypatch):
    cid,run,sid=seeded()
    monkeypatch.setenv('OPENAI_API_KEY','test-only')
    model=MagicMock()
    model.responses.create.return_value=SimpleNamespace(output=[SimpleNamespace(type='function_call',name='read_source',arguments=json.dumps({'value':sid}),call_id='call')])
    with patch('openai.OpenAI',return_value=model), pytest.raises(ModelError,match='лимит шагов'):
        answer_with_tools(ModelAdapter('live','test'),Gateway(store,cid,run),'Кто?')
    assert model.responses.create.call_count==6


def test_model_error_marks_run_failed_not_no_risks(monkeypatch):
    cid,_,_=seeded()
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    with store.connect() as db: db.execute("UPDATE comparisons SET model_mode='live' WHERE id=?",(cid,))
    result=TestClient(app).post(f'/api/comparisons/{cid}/analyze')
    assert result.status_code==422
    assert store.latest_run(cid)['status']=='failed'


def test_coverage_uses_entire_after_set():
    cid,run,sid=seeded()
    gateway=Gateway(store,cid,run)
    result=gateway.call('check_coverage',sid)
    assert result['examined']==len(store.get_spans(cid,'after'))
    assert result['candidates']


def test_snapshot_excludes_future_uploads():
    cid,run,sid=seeded()
    response=TestClient(app).post(f'/api/comparisons/{cid}/documents/after',files={'files':('extra.docx',document_bytes(['Формирует новые требования.']))})
    response.raise_for_status()
    doc=response.json()['documents'][0]['id']
    new_source=next(s['id'] for s in store.get_spans(cid) if s['document_id']==doc)
    with pytest.raises(ValueError,match='снимок'):
        Gateway(store,cid,run).call('read_source',new_source)


@pytest.mark.parametrize('directive,expected', [
    ('Переименовать Отдел закупок в Отдел снабжения.',True),
    ('Не переименовать Отдел закупок в Отдел снабжения.',False),
    ('Проект: переименовать Отдел закупок в Отдел снабжения.',False),
])
def test_structure_transformation_needs_explicit_positive_source(directive,expected):
    case={'id':'explicit-transformation','category':'structure_roles',
          'before':['Отдел закупок.'],'after':['Отдел снабжения.',directive],
          'present':['structure_transformed'] if expected else [],
          'absent':[] if expected else ['structure_transformed']}
    assert run_case(TestClient(app),case)['errors']==[]
