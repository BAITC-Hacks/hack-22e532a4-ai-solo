"""Optional organizer-pair regression; originals remain local and unmodified."""
import json, os, sys, tempfile, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    mode='live' if '--live' in sys.argv else 'offline'
    with tempfile.TemporaryDirectory(prefix='baqbaq-real-') as work:
        os.environ['BAQBAQ_DB_PATH']=str(Path(work)/'state.db')
        os.environ['BAQBAQ_UPLOAD_DIR']=str(Path(work)/'uploads')
        os.environ['BAQBAQ_MODEL_MODE']=mode
        os.environ['BAQBAQ_AUTH_PASSWORD_HASH']=''
        os.environ['BAQBAQ_AUTH_SECRET']=''
        from fastapi.testclient import TestClient
        from app.main import app, settings
        if mode=='live':
            from app.model_adapter import ModelAdapter
            original=ModelAdapter.request
            def logged_request(self,*args,**kwargs):
                print(f'Live request {self.usage["requests"]+1} (bounded verification)',flush=True)
                return original(self,*args,**kwargs)
            ModelAdapter.request=logged_request
        client=TestClient(app)
        cid=client.post('/api/comparisons',json={'name':'Organizer regression'}).json()['id']
        for side,path in [('before',settings.demo_before),('after',settings.demo_after)]:
            if not path.exists():
                print('NOT TESTED: organizer files are not configured');return 2
            client.post(f'/api/comparisons/{cid}/documents/{side}',files={'files':(path.name,path.read_bytes())}).raise_for_status()
        start=time.perf_counter()
        response=client.post(f'/api/comparisons/{cid}/analyze')
        if response.status_code != 200:
            current=client.get(f'/api/comparisons/{cid}/result').json()
            failure={'mode':mode,'passed':False,'http_status':response.status_code,
                     'detail':response.json().get('detail'),'elapsed_seconds':round(time.perf_counter()-start,2),
                     'api_usage':current.get('metadata',{}).get('api_usage',{}),
                     'note':'Live pipeline did not complete; not an accuracy result.'}
            if mode=='live':
                (ROOT/'results/real_pair_live.json').write_text(json.dumps(failure,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(failure,ensure_ascii=False,indent=2));return 1
        data=response.json()
        anchors={}
        for old,new in [('5.4.4','5.3.3'),('9.37','9.37'),('12.1.4','12.1.4')]:
            anchors[old+' -> '+new]=[f['finding_type'] for f in data['findings']
                if (f.get('before_source') or {}).get('clause_label')==old and (f.get('after_source') or {}).get('clause_label')==new]
        report={'mode':mode,'status':data['run']['status'],'search_mode':data['metadata'].get('search_mode'),'elapsed_seconds':round(time.perf_counter()-start,2),
                'summary':data['summary'],'anchors':anchors,'passed':all(anchors.values()),
                'api_usage':data['metadata'].get('api_usage',{}),
                'extraction_warning_count':len(data['metadata'].get('extraction_warnings',[])),
                'note':'Development regression anchors, not a complete independent annotation.'}
        target='real_pair_live.json' if mode=='live' else 'real_pair_regression.json'
        (ROOT/'results'/target).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2))
        return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
