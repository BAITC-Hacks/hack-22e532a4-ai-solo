"""A reproducibility receipt for machine results (not a guarantee of LLM determinism)."""
import hashlib
import importlib.metadata
import os
from pathlib import Path


def runtime_manifest():
    root = Path(__file__).resolve().parent.parent
    files = sorted((root/'app').glob('*.py'))
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode())
        digest.update(path.read_text(encoding='utf-8').replace('\r\n', '\n').encode())
    versions = {}
    for package in ('fastapi', 'starlette', 'pypdf', 'openpyxl', 'openai', 'fastembed'):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = 'not installed'
    return {'algorithm_version': 'audit-v3', 'source_sha256': digest.hexdigest(),
            'release': os.getenv('BAQBAQ_RELEASE', 'working-tree'), 'packages': versions,
            'prompt_version': 'live-schema-v2', 'embedding_revision': 'upstream model; weights not commit-pinned',
            'semantic_determinism': 'New model responses are not guaranteed identical; stored run snapshots are fixed.'}
