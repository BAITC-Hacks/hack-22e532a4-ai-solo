from app.analysis import Analyzer
from app.model_adapter import ModelAdapter
from app.main import store
from test_release_regressions import pair


def fake_functions(task,payload,_):
    if '"functions"' not in task: return {'matches':[]}
    source=next(s for s in payload if 'Контролирует качество' in s['text'])
    return {'functions':[{'source_id':source['source_id'],'quote':source['text'],
        'owner':'Вымышленный владелец','owner_source_id':source['source_id'],'action':'Контролирует',
        'object':'качество','scope':'закупки','authority':'контролирует'}]}


def test_unverified_owner_is_not_published_as_fact(monkeypatch):
    _,cid,_=pair()
    adapter=ModelAdapter('live','test')
    monkeypatch.setattr(adapter,'structured',fake_functions)
    functions=adapter.extract_functions(store,cid,'before')
    assert functions[0].owner=='Не установлен'
    assert len(adapter.extraction_warnings)==1
    assert functions[0].context_span_id is None


def test_invalid_quote_is_rejected_and_flagged_not_replaced(monkeypatch):
    _,cid,_=pair()
    adapter=ModelAdapter('live','test')
    def response(*args):
        result=fake_functions(*args)
        result['functions'][0]['quote']='Invented assertion not present in document'
        return result
    monkeypatch.setattr(adapter,'structured',response)
    assert adapter.extract_functions(store,cid,'before')==[]
    assert 'отклонена' in adapter.extraction_warnings[0]['reason']


def test_validation_warning_makes_run_partial_and_suppresses_loss(monkeypatch):
    _,cid,_=pair()
    adapter=ModelAdapter('live','test')
    monkeypatch.setattr(adapter,'structured',fake_functions)
    monkeypatch.setattr(adapter,'review_findings',lambda *args:{'mode':'live','reviewed':0,'note':'test-only'})
    result=Analyzer(store,adapter).run(cid)
    assert result['run']['status']=='partial'
    assert result['metadata']['extraction_warnings']
    assert not result['summary'].get('potential_loss')
