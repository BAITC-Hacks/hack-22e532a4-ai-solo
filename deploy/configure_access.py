"""Migrate existing jury credentials to app sessions, preserving the API key and other env values."""
import hashlib
import os
import pwd
import secrets
from pathlib import Path

root=Path('/opt/baqbaq').resolve()
env=root/'app/.env'
if env.resolve().parent != root/'app': raise SystemExit('Unexpected .env path')
access=dict(line.split(': ',1) for line in (root/'jury-access.txt').read_text().splitlines() if ': ' in line)
salt=secrets.token_hex(16)
digest=hashlib.pbkdf2_hmac('sha256',access['Password'].encode(),salt.encode(),240000).hex()
values={'BAQBAQ_AUTH_PASSWORD_HASH':salt+':'+digest,'BAQBAQ_AUTH_SECRET':secrets.token_hex(32),
        'BAQBAQ_DAILY_API_REQUESTS':'200','BAQBAQ_JOB_API_REQUESTS':'80','BAQBAQ_RELEASE':'0.3.0',
        'BAQBAQ_PUBLIC_LIVE':'1','BAQBAQ_MODEL_MODE':'live'}
lines=env.read_text().splitlines()
existing={line.partition('=')[0] for line in lines if '=' in line}
saved=dict(line.split('=',1) for line in lines if '=' in line and not line.startswith('#'))
for key in ('BAQBAQ_AUTH_PASSWORD_HASH','BAQBAQ_AUTH_SECRET'):
    if saved.get(key): values[key]=saved[key]
# Initial migration only: existing sessions survive subsequent releases.
lines=[line for line in lines if line.partition('=')[0] not in values]
temporary=env.with_suffix('.env.new')
with temporary.open('w',encoding='utf-8') as output:
    os.chmod(temporary,0o600)
    output.write('\n'.join(lines+[f'{key}={value}' for key,value in values.items()])+'\n')
account=pwd.getpwnam('baqbaq')
os.chown(temporary,account.pw_uid,account.pw_gid)
temporary.replace(env)
print('Configured open Live with isolated browser workspaces and API caps. No secrets printed.')
