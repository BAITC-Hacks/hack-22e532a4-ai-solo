"""Build an explicitly labelled, read-only demo using the actual offline pipeline."""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def main():
    with tempfile.TemporaryDirectory(prefix='baqbaq-public-') as work:
        os.environ.update(BAQBAQ_DB_PATH=str(Path(work)/'demo.db'),BAQBAQ_UPLOAD_DIR=str(Path(work)/'uploads'),
                          BAQBAQ_MODEL_MODE='offline',BAQBAQ_SEARCH_MODE='lexical',OPENAI_API_KEY='',
                          BAQBAQ_AUTH_PASSWORD_HASH='',BAQBAQ_AUTH_SECRET='')
        from fastapi.testclient import TestClient
        from app.main import app
        from app.fixtures import document_bytes,DEMO_BEFORE,DEMO_AFTER
        output=ROOT/'static'/'demo';output.mkdir(exist_ok=True)
        with TestClient(app) as client:
            cid=client.post('/api/comparisons',json={'name':'Открытый контрольный набор','model_mode':'offline'}).json()['id']
            for side,lines in [('before',DEMO_BEFORE),('after',DEMO_AFTER)]:
                data=document_bytes(lines)
                (output/f'{side}.docx').write_bytes(data)
                r=client.post(f'/api/comparisons/{cid}/documents/{side}',files={'files':(f'SYNTHETIC_{side}.docx',data)})
                r.raise_for_status()
            response=client.post(f'/api/comparisons/{cid}/analyze');response.raise_for_status()
            result=response.json()
            for doc in result['documents']: doc.pop('stored_path',None)
            required={'structure_transformed','transferred','potential_loss','possible_duplicate'}
            assert required<=set(result['summary']),result['summary']
            (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            report=client.get(f'/api/comparisons/{cid}/report',params={'format':'html','run_id':result['run']['id']})
            report.raise_for_status();(output/'report.html').write_text(report.text,encoding='utf-8')
            print(json.dumps({'status':'PASS','summary':result['summary'],'input_hash':result['run']['input_hash']},ensure_ascii=False))

if __name__=='__main__':main()
