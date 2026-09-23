"""Derived CPU embedding cache; canonical documents remain in SQLite."""
import hashlib
import json
import os
from functools import lru_cache
from .semantic import similarity

MODEL = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'


@lru_cache(maxsize=2)
def encoder(model_name):
    from fastembed import TextEmbedding
    return TextEmbedding(model_name=model_name, threads=2)


class SearchIndex:
    def __init__(self, store):
        self.store = store
        self.mode = os.getenv('BAQBAQ_SEARCH_MODE', 'fastembed')
        if self.mode not in {'lexical', 'fastembed'}:
            raise ValueError('BAQBAQ_SEARCH_MODE must be lexical or fastembed')
        self.model_name = os.getenv('BAQBAQ_EMBEDDING_MODEL', MODEL)
        self.vectors = {}
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS embedding_cache (key TEXT PRIMARY KEY, vector_json TEXT NOT NULL)')

    def prepare(self, texts):
        if self.mode != 'fastembed':
            return
        unique = list(dict.fromkeys(texts))
        missing = []
        with self.store.connect() as db:
            for text in unique:
                key = hashlib.sha256((self.model_name + '\0' + text).encode()).hexdigest()
                row = db.execute('SELECT vector_json FROM embedding_cache WHERE key=?', (key,)).fetchone()
                if row:
                    self.vectors[text] = json.loads(row[0])
                else:
                    missing.append((text, key))
        if missing:
            vectors = encoder(self.model_name).embed([text for text, _ in missing], batch_size=16)
            # A failure is propagated; an explicitly selected encoder never silently falls back.
            for (text, key), vector in zip(missing, vectors):
                norm = float((vector @ vector) ** .5) or 1.
                values = (vector / norm).tolist()
                with self.store.connect() as db:
                    db.execute('INSERT OR REPLACE INTO embedding_cache VALUES (?,?)', (key, json.dumps(values)))
                self.vectors[text] = values

    def score(self, left, right):
        lexical = similarity(left, right)
        if self.mode != 'fastembed':
            return lexical
        import numpy as np
        cosine = float(np.dot(self.vectors[left], self.vectors[right]))
        # Semantic similarity proposes candidates; it never proves an exact assertion.
        semantic = max(0., (cosine - .35) / .65)
        return round(max(lexical, .55 * lexical + .45 * semantic), 4)

    def search(self, comparison_id, query, limit=8, document_ids=None):
        spans = self.store.get_spans(comparison_id)
        if document_ids is not None:
            spans = [span for span in spans if span['document_id'] in document_ids]
        self.prepare([query] + [s['original_text'] for s in spans])
        ranked = sorted(((self.score(query, s['original_text']), s) for s in spans), key=lambda x:x[0], reverse=True)
        return [dict(s, retrieval_score=score) for score, s in ranked[:limit] if score > .1]

    def rebuild(self, comparison_id):
        texts = [s['original_text'] for s in self.store.get_spans(comparison_id)]
        self.prepare(texts)
        return {'mode': self.mode, 'model': self.model_name if self.mode == 'fastembed' else None, 'spans': len(texts)}
