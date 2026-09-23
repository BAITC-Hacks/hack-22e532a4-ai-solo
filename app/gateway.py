"""Comparison-scoped tools shared by HTTP, the model agent and verification."""
import json
import time
import uuid
from .search import SearchIndex
from .semantic import similarity, authority, scope_conflicts

TOOL_NAMES = ('search_sources', 'read_source', 'find_functions', 'compare_sources', 'check_coverage', 'get_analysis')
DESCRIPTIONS = {
    'search_sources': 'Найти исходные фрагменты. value: поисковый запрос.',
    'read_source': 'Прочитать точный источник. value: source_id.',
    'find_functions': 'Найти функции текущего сравнения. value: текст запроса.',
    'compare_sources': 'Сравнить два источника. value: два source_id через запятую.',
    'check_coverage': 'Проверить все фрагменты После для исходной функции. value: source_id из До.',
    'get_analysis': 'Получить сводку и выводы текущего запуска. value: пустая строка.',
}
TOOLS = [{'type': 'function', 'name': name, 'description': DESCRIPTIONS[name], 'strict': True,
          'parameters': {'type': 'object', 'properties': {'value': {'type': 'string'}},
                         'required': ['value'], 'additionalProperties': False}} for name in TOOL_NAMES]


class Gateway:
    def __init__(self, store, comparison_id, run_id=None):
        if not store.get_comparison(comparison_id):
            raise KeyError('Сравнение не найдено')
        self.store, self.comparison_id, self.run_id = store, comparison_id, run_id
        self.request_id = 'request_' + uuid.uuid4().hex
        self.trace = []
        self.evidence = {}
        self.index = SearchIndex(store)
        snapshot = store.analysis_result(comparison_id, run_id) if run_id else {}
        self.documents = snapshot.get('documents') or store.list_documents(comparison_id)
        self.document_ids = {d['id'] for d in self.documents}
        self.function_ids = set(snapshot.get('metadata', {}).get('function_ids', []))

    def _source(self, sid):
        span = self.store.get_span(sid, self.comparison_id)
        if not span:
            raise ValueError('Источник недоступен в этом сравнении')
        if span['document_id'] not in self.document_ids:
            raise ValueError('Источник не входит в снимок запуска')
        self.evidence[sid] = span
        return span

    def call(self, name, value):
        started = time.perf_counter()
        event = {'tool': name, 'status': 'error', 'request_id': self.request_id}
        try:
            if name == 'read_source':
                result = self._source(value)
            elif name == 'search_sources':
                result = self.index.search(self.comparison_id, value)
                result = [self._source(s['id']) for s in result if s['document_id'] in self.document_ids]
            elif name == 'find_functions':
                with self.store.connect() as db:
                    functions = [dict(r) for r in db.execute('SELECT * FROM function_assertions WHERE comparison_id=?', (self.comparison_id,))]
                functions = [f for f in functions if f['document_id'] in self.document_ids and (not self.function_ids or f['id'] in self.function_ids)]
                result = sorted(functions, key=lambda f:similarity(value, f['assertion_text']), reverse=True)[:10]
                for fn in result:
                    self._source(fn['span_id'])
            elif name == 'compare_sources':
                ids = [x.strip() for x in value.split(',')]
                if len(ids) != 2:
                    raise ValueError('Нужны два source_id')
                left, right = [self._source(sid) for sid in ids]
                result = {'source_ids': ids, 'lexical_similarity': similarity(left['original_text'], right['original_text']),
                          'authorities': [authority(s['original_text']) for s in (left, right)]}
            elif name == 'check_coverage':
                old = self._source(value)
                if old['side'] != 'before':
                    raise ValueError('Проверка покрытия требует источник До')
                spans = [s for s in self.store.get_spans(self.comparison_id, 'after') if s['document_id'] in self.document_ids]
                self.index.prepare([old['original_text']] + [s['original_text'] for s in spans])
                ranked = sorted(((self.index.score(old['original_text'], s['original_text']), s) for s in spans), key=lambda x:x[0], reverse=True)
                result = {'examined': len(spans), 'candidates': [{'score':score, 'source':self._source(s['id'])} for score,s in ranked[:8]],
                          'complete': all(d['status']=='ready' and not d.get('warnings') for d in self.documents),
                          'limitation': 'Проверен загруженный комплект; сходство не доказывает эквивалентность.'}
            elif name == 'get_analysis':
                data = self.store.analysis_result(self.comparison_id, self.run_id)
                result = {'run': data['run'], 'summary': data['summary'], 'findings': [
                    {k:f[k] for k in ('id','title','summary','evidence_status','before_span_id','after_span_id')} for f in data['findings'][:40]]}
            else:
                raise ValueError('Неизвестный инструмент')
            event['status'] = 'ok'
            event['source_ids'] = list(self.evidence)
            return result
        finally:
            event['duration_ms'] = int((time.perf_counter()-started)*1000)
            self.trace.append(event)

    def persist_trace(self):
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS agent_traces (request_id TEXT PRIMARY KEY, comparison_id TEXT, run_id TEXT, trace_json TEXT)')
            db.execute('INSERT OR REPLACE INTO agent_traces VALUES (?,?,?,?)',
                       (self.request_id, self.comparison_id, self.run_id, json.dumps(self.trace)))
