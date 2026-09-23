"""Install only the BaqBaq vhost, with validation and rollback on reload failure."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

root = Path('/opt/baqbaq')
target = Path('/etc/nginx/sites-available/baqbaq.world')
link = Path('/etc/nginx/sites-enabled/baqbaq.world')
stage = sys.argv[1]
if stage not in ('http','https'):
    raise SystemExit('Expected http or https')
def others():
    return {str(p):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in Path('/etc/nginx').rglob('*') if p.is_file() and p not in (target,link)}
before = others()
baseline = root / 'nginx-other-files.json'
if baseline.exists():
    if json.loads(baseline.read_text()) != before:
        raise SystemExit('Other nginx files changed since baseline: inspect before proceeding')
else:
    baseline.write_text(json.dumps(before,indent=2))
old = target.read_bytes() if target.exists() else None
if link.exists() and (not link.is_symlink() or link.resolve() != target):
    raise SystemExit('Unexpected existing vhost link')
target.write_bytes((root / 'app' / 'deploy' / ('nginx-'+stage+'.conf')).read_bytes())
added_link = not link.exists()
if added_link:
    link.symlink_to(target)
try:
    subprocess.run(['nginx','-t'],check=True)
    assert others() == before, 'Other nginx configs changed'
    subprocess.run(['systemctl','reload','nginx'],check=True)
except Exception:
    if old is None:
        if added_link:
            link.unlink()
        target.unlink()
    else:
        target.write_bytes(old)
    raise
print('Activated BaqBaq '+stage+' only. Other nginx config hashes unchanged.')
