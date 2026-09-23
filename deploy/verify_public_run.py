"""Read-only receipt for one known release-validation run; never makes model calls."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import settings
from app.db import Store
from app.provenance import runtime_manifest
from app.reporting import html_report

store=Store(settings.db_path)
cid=sys.argv[1]
comparison=store.get_comparison(cid)
assert comparison is not None
result=store.analysis_result(cid)
assert result['run']['status'] in {'completed','partial'}
sources=0
for finding in result['findings']:
    for key in ('before_source','after_source'):
        source=finding.get(key)
        if source:
            stored=store.get_span(source['id'],cid)
            assert stored and source['original_text']==stored['original_text']
            sources+=1
reviews=[f['review'] for f in result['findings'] if f.get('review')]
report=html_report(comparison,result)
assert reviews and all(review['note'] in report for review in reviews)
assert all(f.get('before_source') or f.get('after_source') for f in result['findings'])
print(json.dumps({'comparison_id':cid,'run_id':result['run']['id'],'status':result['run']['status'],
    'findings':len(result['findings']),'verified_source_references':sources,'saved_reviews':len(reviews),
    'report_contains_review':True,'api_usage':result['metadata'].get('api_usage'),
    'runtime_source_sha256':runtime_manifest()['source_sha256'],
    'run_source_sha256':result['metadata'].get('source_sha256')},ensure_ascii=False))
