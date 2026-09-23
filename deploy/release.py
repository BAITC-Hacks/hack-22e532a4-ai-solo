"""Scoped overlay upgrade. Does not remove files or alter unrelated services/vhosts."""
import datetime
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile

root=Path('/opt/baqbaq').resolve()
app=root/'app'
archive=Path(sys.argv[1]).resolve()
if archive.parent != root or not archive.is_file() or app.resolve()!=root/'app':
    raise SystemExit('Unexpected release paths')
stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
backup=root/'backups'/stamp
backup.mkdir(parents=True,mode=0o700)
os.chmod(backup,0o700)
with tarfile.open(backup/'app.tgz','w:gz') as old:
    old.add(app,arcname='app')
os.chmod(backup/'app.tgz',0o600)
vhost=Path('/etc/nginx/sites-available/baqbaq.world')
(backup/'nginx.conf').write_bytes(vhost.read_bytes())
for database in (root/'data').glob('*.db'):
    with sqlite3.connect(database) as source, sqlite3.connect(backup/database.name) as target:
        source.backup(target)
subprocess.run([str(root/'venv/bin/python'),'-m','pip','freeze'],check=True,stdout=(backup/'requirements.txt').open('w'))
with tarfile.open(archive,'r:gz') as release:
    for member in release.getmembers():
        target=(app/member.name).resolve()
        if not target.is_relative_to(app) or member.issym() or member.islnk() or member.name in {'.env','data','uploads'} or '/.env' in member.name:
            raise SystemExit('Unsafe release member')
    release.extractall(app,filter='data')
subprocess.run([str(root/'venv/bin/python'),'-m','pip','install','--no-cache-dir','-r',str(app/'requirements.txt')],check=True)
subprocess.run([str(root/'venv/bin/python'),'-m','pip','check'],check=True)
subprocess.run([str(root/'venv/bin/python'),str(app/'deploy/configure_access.py')],check=True)
subprocess.run(['systemctl','restart','baqbaq'],check=True)
print('Backup:',backup,'; only BaqBaq updated. Nginx unchanged pending access checks.',flush=True)
