"""Run on the target after securely uploading a local env; never logs secrets."""
import os
import pwd
import secrets
import subprocess
from pathlib import Path
from dotenv import dotenv_values

root = Path('/opt/baqbaq')
source = root / 'incoming.env'
values = dotenv_values(source)
key = values.get('OPENAI_API_KEY', '').strip()
if not key or '\n' in key or '\r' in key:
    raise SystemExit('Missing or invalid key')
account = pwd.getpwnam('baqbaq')
for name in ['data', 'uploads', 'cache']:
    path = root / name
    path.mkdir(exist_ok=True, mode=0o700)
    os.chown(path, account.pw_uid, account.pw_gid)
env = root / 'app' / '.env'
if env.exists():
    raise SystemExit('Existing deployment env: refusing to overwrite')
env.write_text('\n'.join([
    'BAQBAQ_MODEL_MODE=live', 'BAQBAQ_SEARCH_MODE=fastembed',
    'OPENAI_MODEL=gpt-4.1-mini', 'OPENAI_BASE_URL=https://api.openai.com/v1',
    'OPENAI_API_KEY=' + key,
    'BAQBAQ_DB_PATH=/opt/baqbaq/data/baqbaq.db',
    'BAQBAQ_UPLOAD_DIR=/opt/baqbaq/uploads',
    'BAQBAQ_DEMO_BEFORE=/opt/baqbaq/no-organizer-before.docx',
    'BAQBAQ_DEMO_AFTER=/opt/baqbaq/no-organizer-after.docx', ''
]), encoding='utf-8')
env.chmod(0o600)
os.chown(env, account.pw_uid, account.pw_gid)
source.unlink()
password = secrets.token_urlsafe(24)
hashed = subprocess.run(['openssl','passwd','-6','-stdin'], input=password+'\n',text=True,capture_output=True,check=True).stdout.strip()
htpasswd = root / 'jury.htpasswd'
htpasswd.write_text('jury:' + hashed + '\n')
htpasswd.chmod(0o640)
os.chown(htpasswd, 0, pwd.getpwnam('www-data').pw_gid)
access = root / 'jury-access.txt'
access.write_text('URL: https://baqbaq.world\nUsername: jury\nPassword: '+password+'\n')
access.chmod(0o600)
print('Server env and access credentials created; secrets not printed.')
