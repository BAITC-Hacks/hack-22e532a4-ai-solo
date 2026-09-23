"""Read credentials locally; validate only this application's public/private boundary."""
import sys
from pathlib import Path
import httpx

access_path=Path(sys.argv[1]) if len(sys.argv)>1 else Path('/opt/baqbaq/jury-access.txt')
access=dict(line.split(': ',1) for line in access_path.read_text().splitlines() if ': ' in line)
url=sys.argv[2] if len(sys.argv)>2 else 'https://baqbaq.world'
with httpx.Client(base_url=url,timeout=30,follow_redirects=False) as client:
    for path in ('/','/landing.js','/demo/result.json','/demo/report.html','/assets/baqbaq.png','/assets/kazakhtelecom.svg'):
        response=client.get(path); assert response.status_code==200, (path,response.status_code)
        print(path,200)
    for path in ('/api/health','/api/comparisons','/docs','/app.js'):
        assert client.get(path).status_code==401,path
    assert client.get('/workspace').status_code==303
    assert client.get('/api/health',auth=('jury','wrong')).status_code==401
    response=client.get('/api/health',auth=(access['Username'],access['Password']))
    assert response.status_code==200
    print('private routes protected; authenticated health:',response.json()['release'])
    public=client.get('/demo/result.json').json()
    assert response.json()['source_sha256']==public['metadata']['source_sha256'],'Server and public demo source hashes differ'
    print('server / public demo source hash:',response.json()['source_sha256'])
    if url.startswith('https:'):
        response=client.post('/api/session',json={'password':access['Password']})
        assert response.status_code==200
        assert client.get('/workspace').status_code==200
        assert client.get('/api/comparisons').status_code==200
        assert client.post('/api/comparisons',json={'name':'must fail'},headers={'Origin':'https://untrusted.example'}).status_code==403
        print('trusted TLS + signed secure cookie + workspace + CSRF: PASS')
print('PASS: public preview, protected workspace, credentials, assets')
