import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from app.main import app, store
from app.fixtures import document_bytes, DEMO_BEFORE, DEMO_AFTER
from app.gateway import Gateway
from app.db import AnalysisBusy
from app.domain import FunctionAssertion
from app.ingestion import parse_file


def pair(before=DEMO_BEFORE, after=DEMO_AFTER):
    client = TestClient(app, raise_server_exceptions=False)
    cid = client.post('/api/comparisons',json={'name':'Release regression','model_mode':'offline'}).json()['id']
    for side, lines in [('before',before),('after',after)]:
        client.post(f'/api/comparisons/{cid}/documents/{side}',files={'files':('fixture.docx',document_bytes(lines))}).raise_for_status()
    response = client.post(f'/api/comparisons/{cid}/analyze')
    response.raise_for_status()
    return client,cid,response.json()


def test_demo_contains_track_control_cases():
    _,_,result = pair()
    assert {'structure_transformed','potential_loss','possible_duplicate','transferred'} <= set(result['summary'])
    loss = [f for f in result['findings'] if f['finding_type']=='potential_loss']
    assert len(loss)==1 and 'резервные копии' in loss[0]['before_function']
    dup = [f for f in result['findings'] if f['finding_type']=='possible_duplicate']
    assert len(dup)==1 and dup[0]['before_source']['side']==dup[0]['after_source']['side']=='after'


def test_negation_and_frequency_are_not_preserved():
    for changed in ['Не контролирует качество закупочной деятельности.', 'Контролирует качество закупочной деятельности ежемесячно.']:
        _,_,r=pair(['Директор закупок:','Контролирует качество закупочной деятельности.'],['Директор закупок:',changed])
        assert r['summary'].get('changed') == 1
        assert not r['summary'].get('preserved')


def test_actual_annual_obligation_is_not_lost_or_definition_extracted():
    _,_,r=pair(['1. Директор аудита:',
        '1.1. Под административным руководством понимается управление в рамках трудового законодательства.',
        '1.2. Перечень объектов аудита корректируется с учетом изменений в деятельности Общества (появление новых проектов, формирование новых подразделений и т.д.) как минимум один раз в год.'],
        ['1. Директор аудита:',
         '1.2. Перечень объектов аудита корректируется с учетом изменений в деятельности Общества (новые проекты, подразделения и т.д.) как минимум один раз в год.'])
    assert not r['summary'].get('potential_loss')
    assert all('понимается' not in f['text'] for f in r['metadata']['functions'])


def test_headings_are_not_functions_and_owner_is_complete():
    text=['Департамент непрерывного мониторинга системы внутреннего контроля (ДНМ).',
          '5. Директор департамента непрерывного мониторинга системы внутреннего контроля:',
          '5.1. Контролирует качество аудита.']
    _,_,r=pair(text,text)
    assert len(r['metadata']['functions'])==2
    assert all(f['owner'].endswith('внутреннего контроля') for f in r['metadata']['functions'])


def test_fingerprint_distinguishes_before_after():
    _,_,a=pair()
    _,_,b=pair(DEMO_AFTER,DEMO_BEFORE)
    assert a['run']['input_hash']!=b['run']['input_hash']


def test_noun_list_inherits_action_from_cited_parent():
    _,_,r=pair(['1. Главный аудитор информирует Правление о следующем:',
               '1.1. результаты выполнения программы качества и соблюдение стандартов.'],
              ['1. Главный аудитор информирует Правление о следующем:',
               '1.1. результаты выполнения программы качества.'])
    findings=[f for f in r['findings'] if (f.get('before_source') or {}).get('clause_label')=='1.1']
    assert findings and findings[0]['finding_type']=='changed'
    assert findings[0]['before_context'][0]['clause_label']=='1'


def test_history_registry_is_frozen():
    _,cid,r=pair()
    old=Gateway(store,cid,r['run']['id']).call('find_functions','качество')[0]
    fn=FunctionAssertion(old['id'],old['side'],old['owner'],old['action'],'MUTATED',old['scope'],old['authority'],old['assertion_text'],old['span_id'],old['document_id'],old['clause_label'])
    store.add_function(cid,fn)
    again=Gateway(store,cid,r['run']['id']).call('find_functions','качество')
    assert next(f for f in again if f['id']==old['id'])['object_text']==old['object_text']


def test_historical_search_filters_before_topk():
    client,cid,r=pair()
    query='Контролирует качество закупочной деятельности согласно договору поставки.'
    previous=Gateway(store,cid,r['run']['id']).call('search_sources',query)
    client.post(f'/api/comparisons/{cid}/documents/after',files={'files':('later.docx',document_bytes([query]*12))}).raise_for_status()
    again=Gateway(store,cid,r['run']['id']).call('search_sources',query)
    assert [s['id'] for s in previous]==[s['id'] for s in again]


def test_failed_xml_stays_in_manifest():
    client,cid,_=pair()
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as z: z.writestr('word/document.xml','<invalid')
    response=client.post(f'/api/comparisons/{cid}/documents/after',files={'files':('broken.docx',buf.getvalue())})
    assert response.status_code==422
    r=client.post(f'/api/comparisons/{cid}/analyze').json()
    assert r['run']['status']=='partial'
    assert 'broken.docx' in r['metadata']['incomplete_documents']


def test_two_simultaneous_runs_are_rejected():
    _,cid,_=pair()
    active=store.begin_run(cid,'offline','test','hash')
    try:
        with pytest.raises(AnalysisBusy): store.begin_run(cid,'offline','test','hash')
        assert TestClient(app).post(f'/api/comparisons/{cid}/analyze').status_code==409
    finally:
        store.finish_run(active,'failed','test cleanup')


@pytest.mark.parametrize('override',[False,True])
def test_numbering_start_and_override(tmp_path,override):
    ns='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    p=tmp_path/'numbered.docx'
    with zipfile.ZipFile(p,'w') as z:
        z.writestr('word/document.xml',f'<w:document xmlns:w="{ns}"><w:body><w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>Контролирует качество аудита.</w:t></w:r></w:p></w:body></w:document>')
        custom='<w:lvlOverride w:ilvl="0"><w:startOverride w:val="8"/></w:lvlOverride>' if override else ''
        z.writestr('word/numbering.xml',f'<w:numbering xmlns:w="{ns}"><w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:start w:val="5"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl></w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/>{custom}</w:num></w:numbering>')
    assert parse_file(p).spans[0].clause_label==('8' if override else '5')
