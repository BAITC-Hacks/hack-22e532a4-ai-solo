"""Optional organizer-pair regression; originals remain local and unmodified."""
import json, os, sys, tempfile, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    with tempfile.TemporaryDirectory(prefix='baqbaq-real-') as work:
        os.environ['BAQBAQ_DB_PATH']=str(Path(work)/'state.db')
        os.environ['BAQBAQ_UPLOAD_DIR']=str(Path(work)/'uploads')
        os.environ['BAQBAQ_MODEL_MODE']='offline'
        from fastapi.testclient import TestClient
        from app.main import app, settings
        client=TestClient(app)
        cid=client.post('/api/comparisons',json={'name':'Organizer regression'}).json()['id']
        for side,path in [('before',settings.demo_before),('after',settings.demo_after)]:
            if not path.exists():
                print('NOT TESTED: organizer files are not configured');return 2
            client.post(f'/api/comparisons/{cid}/documents/{side}',files={'files':(path.name,path.read_bytes())}).raise_for_status()
        start=time.perf_counter()
        response=client.post(f'/api/comparisons/{cid}/analyze')
        response.raise_for_status();data=response.json()
        anchors={}
        for old,new in [('5.4.4','5.3.3'),('9.37','9.37'),('12.1.4','12.1.4')]:
            anchors[old+' -> '+new]=[f['finding_type'] for f in data['findings']
                if (f.get('before_source') or {}).get('clause_label')==old and (f.get('after_source') or {}).get('clause_label')==new]
        report={'mode':'offline','search_mode':data['metadata'].get('search_mode'),'elapsed_seconds':round(time.perf_counter()-start,2),
                'summary':data['summary'],'anchors':anchors,'passed':all(anchors.values()),
                'note':'Development regression anchors, not a complete independent annotation.'}
        (ROOT/'results/real_pair_regression.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2))
        return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
