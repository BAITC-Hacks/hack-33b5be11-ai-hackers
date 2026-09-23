"""Read-only end-to-end smoke test against an already running web server."""
import argparse
import http.cookiejar
import json
import sys
import time
import urllib.request

parser=argparse.ArgumentParser()
parser.add_argument('--url',default='http://127.0.0.1:8000')
parser.add_argument('--employee',default='E0028')
args=parser.parse_args()
client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
def request(path,data=None):
    req=urllib.request.Request(args.url+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'})
    with client.open(req,timeout=12) as r:return json.load(r)
try:
    health=request('/api/health');print('Build:',health['build'])
    if not health['ai_configured'] or not health['ai_sdk_installed']:
        print('NOT READY: server needs both OPENAI_API_KEY and OpenAI SDK');sys.exit(2)
    request('/api/login',{'role':'employee','employee_id':args.employee})
    started=time.monotonic();result=request('/api/agent/recommend',{'employee_id':args.employee})
    elapsed=time.monotonic()-started
    print(json.dumps({'mode':result['mode'],'fallback_reason':result['fallback_reason'],'tools_used':result['tools_used'],'seconds':round(elapsed,3)},ensure_ascii=False))
    success=result['mode']=='openai' and len(result['tools_used'])==4 and elapsed<10
    print('PASS' if success else 'NOT READY: live AI or latency check failed')
    sys.exit(0 if success else 1)
except Exception as e:
    print('NOT READY:',type(e).__name__);sys.exit(1)
