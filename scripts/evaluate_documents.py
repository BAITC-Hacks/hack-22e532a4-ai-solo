"""Document-level evaluation through the public API, isolated from user state."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run_case(client, case):
    from app.fixtures import document_bytes
    response = client.post('/api/comparisons', json={'name':'Synthetic '+case['id'], 'model_mode':'offline'})
    response.raise_for_status()
    cid = response.json()['id']
    fmt = case.get('format','docx')
    for side, revision in [('before','8'),('after','9')]:
        response = client.post(f'/api/comparisons/{cid}/documents/{side}',files={
            'files':(f'synthetic_revision_{revision}.{fmt}',document_bytes(case[side],fmt))})
        response.raise_for_status()
    if case.get('blank_pdf'):
        response = client.post(f'/api/comparisons/{cid}/documents/after',files={'files':('scan.pdf',document_bytes([],'pdf'))})
        response.raise_for_status()
    response = client.post(f'/api/comparisons/{cid}/analyze')
    response.raise_for_status()
    data = response.json()
    types = Counter(f['finding_type'] for f in data['findings'])
    errors = []
    for kind in case.get('present',[]):
        if not types[kind]: errors.append('missing '+kind)
    for kind in case.get('absent',[]):
        if types[kind]: errors.append('unexpected '+kind)
    if case.get('status') and data['run']['status']!=case['status']:
        errors.append('status '+data['run']['status'])
    if case.get('owner') and not any(f['before_owner']==case['owner'] for f in data['findings']):
        errors.append('owner mismatch')
    sources = {}
    for finding in data['findings']:
        for key in ('before_source','after_source'):
            s = finding.get(key)
            if not s: continue
            response = client.get(f'/api/comparisons/{cid}/sources/{s["id"]}')
            if response.status_code!=200 or response.json()['original_text']!=s['original_text']:
                errors.append('unresolved source')
            sources[s['id']]={k:s[k] for k in ('id','document_id','side','filename','revision','locator','clause_label','original_text')}
    foreign = client.post('/api/comparisons',json={'name':'Isolation '+case['id']}).json()['id']
    for sid in sources:
        if client.get(f'/api/comparisons/{foreign}/sources/{sid}').status_code!=404:
            errors.append('cross-comparison leak')
    if case.get('review'):
        fid = next(f['id'] for f in data['findings'] if f['finding_type']=='preserved')
        client.post(f'/api/findings/{fid}/review',json={'decision':'rejected','note':'EVALUATION_REVIEW'}) .raise_for_status()
    for fmt in ('markdown','html'):
        report = client.get(f'/api/comparisons/{cid}/report',params={'format':fmt,'run_id':data['run']['id']})
        if report.status_code!=200 or data['run']['id'] not in report.text:
            errors.append('report '+fmt)
        if case.get('review') and 'EVALUATION_REVIEW' not in report.text:
            errors.append('missing review in '+fmt)
    if case.get('history'):
        old = client.get(f'/api/comparisons/{cid}/result',params={'run_id':data['run']['id']}).json()
        client.post(f'/api/comparisons/{cid}/analyze').raise_for_status()
        new = client.get(f'/api/comparisons/{cid}/result',params={'run_id':data['run']['id']}).json()
        if old != new: errors.append('historical run changed')
    return {'id':case['id'],'category':case['category'],'input':case,'actual':dict(types),
            'run_status':data['run']['status'],'sources':list(sources.values()),
            'errors':errors,'status':'FAIL' if errors else 'PASS'}


def main():
    with tempfile.TemporaryDirectory(prefix='baqbaq-evaluation-') as work:
        os.environ['BAQBAQ_DB_PATH']=str(Path(work)/'evaluation.db')
        os.environ['BAQBAQ_UPLOAD_DIR']=str(Path(work)/'uploads')
        os.environ['BAQBAQ_MODEL_MODE']='offline'
        os.environ['BAQBAQ_AUTH_PASSWORD_HASH']=''
        os.environ['BAQBAQ_AUTH_SECRET']=''
        os.environ.setdefault('BAQBAQ_SEARCH_MODE','lexical')
        from fastapi.testclient import TestClient
        from app.main import app
        raw=(ROOT/'benchmarks/document_cases.json').read_bytes()
        cases=json.loads(raw)
        if len(cases)!=24: raise ValueError('Expected exactly 24 cases')
        with TestClient(app) as client:
            results=[]
            for case in cases:
                try: result=run_case(client,case)
                except Exception as exc:
                    result={'id':case['id'],'category':case['category'],'input':case,'status':'FAIL','errors':[str(exc)]}
                results.append(result)
                print(case['id'],result['status'],result.get('errors',[]),flush=True)
        report={'dataset_sha256':hashlib.sha256(raw).hexdigest(),'timestamp_utc':datetime.now(timezone.utc).isoformat(),
                'git_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                'git_dirty':bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip()),
                'mode':'offline','search_mode':os.environ['BAQBAQ_SEARCH_MODE'],'live_verification':'NOT TESTED',
                'passed':sum(r['status']=='PASS' for r in results),'total':len(results),'results':results}
        suffix = '_fastembed' if report['search_mode']=='fastembed' else ''
        (ROOT/f'results/document_evaluation{suffix}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        lines=['# Сквозная проверка документов','',f"Результат: {report['passed']}/{report['total']}. Режим: offline; поиск: {report['search_mode']}.",'',
               'Проверяет загрузку → анализ → источники/изоляцию → экспорт. Синтетические данные; не независимая оценка точности. Live: NOT TESTED.','',
               '| Случай | Статус | Ошибки |','|---|---|---|']
        lines += [f"| {r['id']} | {r['status']} | {'; '.join(r.get('errors',[]))} |" for r in results]
        (ROOT/f'results/document_evaluation{suffix}.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
        return 0 if report['passed']==report['total'] else 1


if __name__=='__main__':
    raise SystemExit(main())
