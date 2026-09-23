"""Release hygiene and source/demo coherence. Prints paths/status, never secret values."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.provenance import runtime_manifest
from app.fixtures import DEMO_BEFORE,DEMO_AFTER,document_bytes

paths=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=ROOT).decode().split('\0')
secrets=[value.encode() for key,value in dotenv_values(ROOT/'.env').items()
         if value and any(part in key for part in ('KEY','PASSWORD','SECRET')) and len(value)>12]
for relative in paths:
    path=ROOT/relative
    if not relative or not path.is_file(): continue
    assert relative!='.env', '.env must not be tracked'
    content=path.read_bytes()
    assert not any(value in content for value in secrets), 'Secret found in '+relative
page=(ROOT/'static/workspace.html').read_text(encoding='utf-8')
assert 'baqbaq-transparent.png' in page
assert 'jury-demo' not in page and 'demo-button' not in page and 'Открытое демо' not in page
assert (ROOT/'static/assets/baqbaq-transparent.png').is_file()
print('PASS: no configured secrets in release files; Live workspace and supplied brand asset present')
