"""Local hackathon server. Python 3.9+, no third-party dependencies."""
import csv
import io
import json
import os
import secrets
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from engine import Engine, GRADES

ROOT = Path(__file__).resolve().parent
LOCK = threading.RLock()
SESSIONS = {}
STATE = ROOT / 'var/state.json'

def read(name): return json.loads((ROOT / 'data' / name).read_text())

def load():
    base = {'employees': read('employees.json')['employees'], 'history': list(csv.DictReader(io.StringIO((ROOT/'data/activity_history.csv').read_text())))}
    if STATE.exists(): base = json.loads(STATE.read_text())
    return base

DATA = load()
EVENTS = read('events.json')['events']
SKILLS = read('skills.json')
TODAY = read('employees.json')['meta']['as_of_date']

def engine(): return Engine(DATA['employees'], EVENTS, SKILLS, DATA['history'], TODAY)

def save():
    STATE.parent.mkdir(exist_ok=True)
    temp = STATE.with_suffix('.tmp'); temp.write_text(json.dumps(DATA, ensure_ascii=False)); temp.replace(STATE)

def validate_import(payload):
    employees = payload.get('employees', [])
    if isinstance(employees, dict): employees = employees.get('employees', [])
    history = payload.get('history', [])
    if isinstance(history, str): history = list(csv.DictReader(io.StringIO(history)))
    if not isinstance(employees, list) or not isinstance(history, list): raise ValueError('Ожидаются employees.json и история CSV')
    if len(employees)>2000 or len(history)>50000: raise ValueError('Слишком большой импорт')
    eng = engine(); ids=set(eng.employees); newids=set(); skills=set(eng.skills)
    for e in employees:
        if not isinstance(e,dict): raise ValueError('Некорректный профиль')
        for k in ['employee_id','full_name','role','grade','skills','last_review_date']:
            if k not in e: raise ValueError('Нет поля '+k)
        if not isinstance(e['employee_id'], str) or not e['employee_id'] or e['employee_id'] in newids: raise ValueError('ID профиля отсутствует или повторяется')
        newids.add(e['employee_id']); ids.add(e['employee_id'])
        if (e['role'],e['grade']) not in eng.profiles: raise ValueError('Неизвестная роль/грейд')
        if not isinstance(e['skills'],dict) or any(k not in skills or type(v) is not int or not 0<=v<=5 for k,v in e['skills'].items()): raise ValueError('Навыки: известный ID и целый уровень 0–5')
        from datetime import date
        date.fromisoformat(e['last_review_date'])
        if e['last_review_date']>TODAY: raise ValueError('Оценка позже даты среза')
        if e.get('career_goal') and (e['career_goal'].get('target_role'),e['career_goal'].get('target_grade')) not in eng.profiles: raise ValueError('Неизвестная карьерная цель')
    recordids=set()
    for r in history:
        if not isinstance(r,dict) or any(k not in r for k in ['record_id','employee_id','event_id','date','status']): raise ValueError('Некорректная строка истории')
        if r['employee_id'] not in ids or r['event_id'] not in eng.events: raise ValueError('Неизвестная ссылка в истории')
        if r['status'] not in {'completed','in_progress','dropped','no_show','declined','overdue'}: raise ValueError('Неизвестный статус')
        from datetime import date
        date.fromisoformat(r['date'])
        if r['date']>TODAY: raise ValueError('История позже даты среза')
        if r['record_id'] in recordids: raise ValueError('Повтор ID истории')
        recordids.add(r['record_id'])
    return employees, history

class Handler(BaseHTTPRequestHandler):
    def send(self, body, status=200, content_type='application/json; charset=utf-8', cookie=None):
        raw = json.dumps(body, ensure_ascii=False).encode() if content_type.startswith('application/json') else body
        self.send_response(status); self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(raw))); self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'")
        if cookie: self.send_header('Set-Cookie',cookie)
        self.end_headers(); self.wfile.write(raw)

    def session(self):
        cookies=dict(pair.strip().split('=',1) for pair in self.headers.get('Cookie','').split(';') if '=' in pair)
        session=SESSIONS.get(cookies.get('cq_session',''))
        if not session: raise PermissionError('Войдите в демо')
        return session

    def do_GET(self):
        try:
            url=urlparse(self.path); query=parse_qs(url.query)
            if not url.path.startswith('/api/'):
                paths={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}
                if url.path not in paths: return self.send({'error':'Не найдено'},404)
                file=ROOT/'web'/paths[url.path]
                return self.send(file.read_bytes(),content_type={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8'}[file.suffix])
            if url.path=='/api/demo': return self.send({'employees':[{'employee_id':e['employee_id'],'full_name':e['full_name'],'role':e['role']} for e in DATA['employees']], 'demo':True})
            s=self.session()
            with LOCK:
                eng=engine()
                if url.path=='/api/profile':
                    eid=query.get('id',[s.get('employee_id')])[0]
                    if s['role']!='hr' and eid!=s['employee_id']: raise PermissionError('Доступ только к своему профилю')
                    hours=float(query.get('hours',['80'])[0])
                    if not 1<=hours<=1000: raise ValueError('Бюджет от 1 до 1000 часов')
                    return self.send(eng.profile(eid,hours))
                if url.path=='/api/hr':
                    if s['role']!='hr': raise PermissionError('Нужна роль HR')
                    return self.send(eng.hr())
            self.send({'error':'Не найдено'},404)
        except PermissionError as e: self.send({'error':str(e)},403)
        except (ValueError,KeyError,TypeError) as e: self.send({'error':str(e)},400)

    def do_POST(self):
        try:
            origin=self.headers.get('Origin')
            if origin and urlparse(origin).netloc != self.headers.get('Host'): raise PermissionError('Запрос с другого сайта запрещён')
            length=int(self.headers.get('Content-Length',0))
            if not 0<length<=8_000_000: raise ValueError('Недопустимый размер запроса')
            payload=json.loads(self.rfile.read(length))
            if self.path=='/api/login':
                role=payload.get('role')
                if role=='hr':
                    if not secrets.compare_digest(str(payload.get('password','')), os.environ.get('HR_PASSWORD','hackalem-demo')): raise PermissionError('Неверный пароль HR')
                    s={'role':'hr'}
                elif role=='employee' and payload.get('employee_id') in engine().employees: s={'role':'employee','employee_id':payload['employee_id']}
                else: raise ValueError('Неизвестный пользователь')
                token=secrets.token_urlsafe(32); SESSIONS[token]=s
                return self.send(s,cookie=f'cq_session={token}; HttpOnly; SameSite=Strict; Path=/')
            s=self.session()
            with LOCK:
                if self.path=='/api/complete':
                    eid=payload.get('employee_id',s.get('employee_id'))
                    if s['role']!='hr' and eid!=s['employee_id']: raise PermissionError('Нет доступа')
                    eng=engine(); e=eng.employees[eid]; event=eng.events[payload['event_id']]
                    reasons=eng.candidate(e,event,eng.levels(e),eng.records(eid))
                    if reasons: raise ValueError('; '.join(reasons))
                    # Simulation records are explicitly marked and applied exactly once.
                    row={'record_id':'DEMO_'+secrets.token_hex(8),'employee_id':eid,'event_id':event['event_id'],'date':TODAY,'due_date':'','status':'completed','completion_pct':100,'score':'','feedback_rating':'','assigned_by':'self','simulated':True}
                    DATA['history'].append(row)
                    # A same-day review predates a completion recorded by this endpoint.
                    if e['last_review_date']==TODAY: e['skills']=eng.apply(e['skills'],event)
                    save(); return self.send({'ok':True})
                if self.path=='/api/import':
                    if s['role']!='hr': raise PermissionError('Импорт доступен HR')
                    employees,history=validate_import(payload)
                    merged={e['employee_id']:e for e in DATA['employees']}; merged.update({e['employee_id']:e for e in employees})
                    records={r['record_id']:r for r in DATA['history']}; records.update({r['record_id']:r for r in history})
                    DATA['employees']=list(merged.values()); DATA['history']=list(records.values()); save()
                    return self.send({'employees':len(employees),'history':len(history)})
            self.send({'error':'Не найдено'},404)
        except PermissionError as e: self.send({'error':str(e)},403)
        except (ValueError,KeyError,TypeError,AttributeError) as e: self.send({'error':str(e)},400)

if __name__=='__main__':
    port=int(os.environ.get('PORT','8000'))
    print(f'Career Quest: http://127.0.0.1:{port} | Локальное демо | HR: пароль в README',flush=True)
    ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()
