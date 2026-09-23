"""Read a local access file; print only status codes, never the password."""
import sys
from pathlib import Path
import httpx
import socket

if len(sys.argv) > 2:
    # Scoped to this test process; keep hostname/SNI and TLS verification intact.
    original_lookup = socket.getaddrinfo
    def pinned_lookup(host, port, *args, **kwargs):
        return original_lookup(sys.argv[2] if host in ('baqbaq.world', b'baqbaq.world') else host, port, *args, **kwargs)
    socket.getaddrinfo = pinned_lookup
    print('Testing with explicit DNS override; certificate validation remains enabled.')

access = dict(line.split(': ',1) for line in Path(sys.argv[1]).read_text().splitlines() if ': ' in line)
url = access['URL']
with httpx.Client(timeout=30, follow_redirects=False) as client:
    for path in ['/', '/api/health', '/docs']:
        response = client.get(url+path)
        print('anonymous',path,response.status_code)
        assert response.status_code == 401
    response = client.get(url+'/api/health', auth=('jury','incorrect'))
    assert response.status_code == 401
    print('incorrect password:',response.status_code)
    response = client.get(url+'/api/health', auth=(access['Username'],access['Password']))
    assert response.status_code == 200
    assert response.json()['live_ready']
    print('authenticated health:',response.status_code,'live_ready=True')
    response = client.get(url+'/', auth=(access['Username'],access['Password']))
    assert response.status_code == 200 and 'BaqBaq' in response.text
    print('authenticated UI:',response.status_code)
    response = client.get(url.replace('https:','http:')+'/')
    assert response.status_code == 301 and response.headers['location'].startswith('https://baqbaq.world')
    print('http redirect:',response.status_code)
print('PASS: trusted TLS, UI/API protection, credentials, redirect')
