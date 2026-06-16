#!/usr/bin/env python3
"""The Tome — Campaign Server with persistent JSON storage"""
import json, os, uuid, hashlib, time, socket, webbrowser, urllib.parse, subprocess, threading, re, sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

PORT = int(os.environ.get('PORT', 8000))
DATA_FILE = Path(os.environ.get('DATA_DIR', Path(__file__).parent)) / 'tome_data.json'
STATIC_DIR = Path(__file__).parent

# Upstash Redis — used when env vars are set (cloud deployment).
# Falls back to local JSON file when running on the DM's machine.
REDIS_URL   = os.environ.get('UPSTASH_REDIS_REST_URL', '').rstrip('/')
REDIS_TOKEN = os.environ.get('UPSTASH_REDIS_REST_TOKEN', '')
REDIS_KEY   = 'tome_data'

MIME = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'application/javascript',
    '.json': 'application/json',
    '.svg': 'image/svg+xml',
    '.css': 'text/css',
    '.png': 'image/png',
    '.ico': 'image/x-icon',
}


def _redis(method, path, body=None):
    import urllib.request as ur
    url = f'{REDIS_URL}/{path}'
    data = json.dumps(body).encode() if body is not None else None
    req = ur.Request(url, data=data, headers={
        'Authorization': f'Bearer {REDIS_TOKEN}',
        'Content-Type': 'application/json',
    }, method=method)
    with ur.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def load():
    data = None
    if REDIS_URL and REDIS_TOKEN:
        try:
            result = _redis('GET', f'get/{REDIS_KEY}').get('result')
            if result:
                data = json.loads(result)
        except Exception as e:
            print(f'  [redis] load error: {e}')
    if data is None and DATA_FILE.exists():
        try:
            data = json.loads(DATA_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    # Validate — if corrupted or missing keys, reset to safe defaults
    if not isinstance(data, dict):
        data = {}
    for key in ('campaigns', 'dms', 'players'):
        if not isinstance(data.get(key), dict):
            data[key] = {}
    return data


def dump(data):
    if REDIS_URL and REDIS_TOKEN:
        try:
            _redis('POST', f'set/{REDIS_KEY}', data)
            return
        except Exception as e:
            print(f'  [redis] dump error: {e}')
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


def hp(pw):
    return hashlib.sha256(pw.encode()).hexdigest() if pw else ''


def empty_sheet(name=''):
    return {
        'characterName': name, 'playerName': name,
        'race': '', 'class': '', 'subclass': '', 'background': '', 'alignment': '',
        'level': 1, 'xp': 0, 'proficiencyBonus': 2,
        'strength': 10, 'dexterity': 10, 'constitution': 10,
        'intelligence': 10, 'wisdom': 10, 'charisma': 10,
        'ac': 10, 'initiative': 0, 'speed': 30,
        'hpMax': 8, 'hpCurrent': 8, 'hpTemp': 0,
        'hitDice': '1d8', 'hitDiceUsed': 0,
        'savingThrows': {}, 'skills': {},
        'attacks': [{'name': '', 'bonus': '', 'damage': '', 'damageType': ''}],
        'spellSlots': {str(i): {'total': 0, 'used': 0} for i in range(1, 10)},
        'equipment': '', 'featuresAndTraits': '',
        'personalityTraits': '', 'ideals': '', 'bonds': '', 'flaws': '',
        'notes': '', 'appearance': ''
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        try:
            first = str(args[0]) if args else ''
            parts = first.split()
            method = parts[0] if parts else ''
            path = parts[1] if len(parts) > 1 else ''
            if '/api/' in path:
                status = str(args[1]) if len(args) > 1 else ''
                print(f'  {method} {path}  [{status}]')
        except Exception:
            pass

    def do_HEAD(self):
        # Render health checks use HEAD — respond 200 so the service stays live
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()

    def cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def ok(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.cors()
        self.end_headers()
        self.wfile.write(body)

    def err(self, msg, status=400):
        self.ok({'ok': False, 'error': msg}, status)

    def body(self):
        n = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(n)) if n else {}

    def qs(self):
        return urllib.parse.parse_qs(self.path.split('?')[1] if '?' in self.path else '')

    def ep(self):
        r = self.path.split('?')[0].lstrip('/')
        return r[4:] if r.startswith('api/') else None

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.end_headers()

    def do_GET(self):
        api = self.ep()
        if api is not None:
            return self.get_api(api)
        path = self.path.split('?')[0].lstrip('/') or 'index.html'
        target = (STATIC_DIR / path).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())):
            self.send_error(403)
            return
        if target.is_file():
            data = target.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', MIME.get(target.suffix, 'application/octet-stream'))
            self.send_header('Content-Length', len(data))
            self.end_headers()
            self.wfile.write(data)
        else:
            idx = (STATIC_DIR / 'index.html').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', len(idx))
            self.end_headers()
            self.wfile.write(idx)

    def get_api(self, api):
        d = load()
        q = self.qs()

        if api == 'campaign':
            code = q.get('code', [''])[0].strip().upper()
            c = next((c for c in d['campaigns'].values()
                      if c['sessionCode'].upper() == code), None)
            if c:
                return self.ok({'ok': True, 'campaign': c})
            return self.err('Campaign not found. Check the code.', 404)

        if api == 'campaigns':
            email = q.get('dmEmail', [''])[0].lower().strip()
            camps = [c for c in d['campaigns'].values() if c.get('dmEmail') == email]
            camps.sort(key=lambda c: c.get('createdAt', 0))
            return self.ok({'ok': True, 'campaigns': camps})

        if api.startswith('campaign/'):
            cid = api[9:]
            c = d['campaigns'].get(cid)
            if c:
                return self.ok({'ok': True, 'campaign': c})
            return self.err('Campaign not found', 404)

        if api.startswith('sheet/'):
            parts = api[6:].split('/', 1)
            if len(parts) == 2:
                cid, name = parts[0], urllib.parse.unquote(parts[1])
                p = d['players'].get(f'{cid}:{name}')
                if p:
                    return self.ok({'ok': True, 'sheet': p.get('characterSheet', empty_sheet(name))})
            return self.err('Player not found', 404)

        if api.startswith('players/'):
            cid = api[8:]
            camp = d['campaigns'].get(cid, {})
            result = {}
            for pname in camp.get('players', {}).keys():
                p = d['players'].get(f'{cid}:{pname}')
                if p:
                    result[pname] = p.get('characterSheet', empty_sheet(pname))
            return self.ok({'ok': True, 'players': result})

        self.err('Unknown endpoint', 404)

    def do_POST(self):
        api = self.ep()
        if api is None:
            return self.err('Not an API route', 404)
        d = load()
        b = self.body()

        if api == 'dm/register':
            email = b.get('email', '').lower().strip()
            pw = b.get('password', '')
            name = b.get('name', '').strip()
            if not all([email, pw, name]):
                return self.err('Name, email and password are required.')
            if email in d['dms']:
                return self.err('An account with this email already exists.')
            d['dms'][email] = {'name': name, 'pw': hp(pw)}
            dump(d)
            return self.ok({'ok': True, 'dm': {'email': email, 'name': name}})

        if api == 'dm/login':
            email = b.get('email', '').lower().strip()
            pw = b.get('password', '')
            dm = d['dms'].get(email)
            if not dm or dm['pw'] != hp(pw):
                return self.err('Invalid email or password.', 401)
            return self.ok({'ok': True, 'dm': {'email': email, 'name': dm['name']}})

        if api == 'campaign':
            email = b.get('dmEmail', '').lower().strip()
            name = b.get('name', '').strip()
            if not name:
                return self.err('Campaign name is required.')
            import random
            existing = {c['sessionCode'] for c in d['campaigns'].values()}
            code = str(random.randint(100000, 999999))
            while code in existing:
                code = str(random.randint(100000, 999999))
            cid = str(uuid.uuid4())
            camp = {
                'id': cid, 'dmEmail': email, 'name': name,
                'sessionCode': code, 'createdAt': time.time() * 1000,
                'documents': [], 'players': {}
            }
            d['campaigns'][cid] = camp
            dump(d)
            return self.ok({'ok': True, 'campaign': camp})

        if api == 'player/join':
            code = b.get('code', '').strip().upper()
            name = b.get('name', '').strip()
            pw = b.get('password', '').strip()
            if not code or not name:
                return self.err('Session code and adventurer name are required.')
            camp = next((c for c in d['campaigns'].values()
                         if c['sessionCode'].upper() == code), None)
            if not camp:
                return self.err('Campaign not found. Check the code.', 404)
            key = f"{camp['id']}:{name}"
            existing = d['players'].get(key)
            if existing:
                if existing.get('pw') and existing['pw'] != hp(pw):
                    return self.err('Wrong password for this character name.', 401)
                if pw and not existing.get('pw'):
                    existing['pw'] = hp(pw)
                    d['players'][key] = existing
                    dump(d)
            else:
                d['players'][key] = {'name': name, 'campaignId': camp['id'],
                                     'pw': hp(pw), 'characterSheet': empty_sheet(name)}
                camp['players'][name] = {'joinedAt': time.time() * 1000}
                d['campaigns'][camp['id']] = camp
                dump(d)
            pdata = d['players'][key]
            return self.ok({'ok': True, 'player': {
                'name': name, 'campaignId': camp['id'],
                'campaignCode': camp['sessionCode'], 'campaignName': camp['name'],
                'characterSheet': pdata.get('characterSheet', empty_sheet(name))
            }})

        self.err('Unknown endpoint', 404)

    def do_PUT(self):
        api = self.ep()
        if api is None:
            return self.err('Not an API route', 404)
        d = load()
        b = self.body()

        if api.startswith('campaign/'):
            cid = api[9:]
            if cid not in d['campaigns']:
                # Upsert: accept campaigns pushed from client that don't exist yet
                b['id'] = cid
                b.setdefault('players', {})
                d['campaigns'][cid] = b
                dump(d)
                return self.ok({'ok': True, 'campaign': b})
            existing = d['campaigns'][cid]
            updated = {**existing, **b, 'id': cid,
                       'players': existing.get('players', b.get('players', {})),
                       'dmEmail': existing.get('dmEmail', b.get('dmEmail', ''))}
            d['campaigns'][cid] = updated
            dump(d)
            return self.ok({'ok': True, 'campaign': updated})

        if api.startswith('sheet/'):
            parts = api[6:].split('/', 1)
            if len(parts) == 2:
                cid, name = parts[0], urllib.parse.unquote(parts[1])
                key = f'{cid}:{name}'
                if key in d['players']:
                    d['players'][key]['characterSheet'] = b.get('sheet', {})
                    dump(d)
                    return self.ok({'ok': True})
                return self.err('Player not found — join the campaign first', 404)

        self.err('Unknown endpoint', 404)

    def do_DELETE(self):
        api = self.ep()
        if api is None:
            return self.err('Not an API route', 404)
        d = load()
        if api.startswith('campaign/'):
            cid = api[9:]
            if cid in d['campaigns']:
                del d['campaigns'][cid]
                d['players'] = {k: v for k, v in d['players'].items()
                                if not k.startswith(f'{cid}:')}
                dump(d)
                return self.ok({'ok': True})
            return self.err('Campaign not found', 404)
        if api.startswith('player/'):
            parts = api[7:].split('/', 1)
            if len(parts) == 2:
                cid, name = parts[0], urllib.parse.unquote(parts[1])
                key = f'{cid}:{name}'
                if key in d['players']:
                    del d['players'][key]
                camp = d['campaigns'].get(cid)
                if camp and name in camp.get('players', {}):
                    del camp['players'][name]
                    d['campaigns'][cid] = camp
                dump(d)
                return self.ok({'ok': True})
            return self.err('Player not found', 404)
        self.err('Unknown endpoint', 404)


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


def start_tunnel():
    """Start an SSH tunnel via localhost.run and print the public URL."""
    print('  Starting public tunnel (localhost.run)...')
    try:
        proc = subprocess.Popen(
            ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'ServerAliveInterval=30',
             '-R', f'80:localhost:{PORT}', 'nokey@localhost.run'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        for line in proc.stdout:
            m = re.search(r'https?://[a-z0-9\-]+\.lhr\.life|https?://[^\s]+\.localhost\.run', line)
            if m:
                url = m.group(0).rstrip('/')
                print()
                print('  ======================================================')
                print('   PUBLIC LINK (share this with players anywhere):')
                print(f'   {url}')
                print('  ======================================================')
                print()
                return
        print('  [!] Could not get tunnel URL. Players use the WiFi link instead.')
    except FileNotFoundError:
        print('  [!] SSH not found. Players must use the same WiFi.')
    except Exception as e:
        print(f'  [!] Tunnel error: {e}')


if __name__ == '__main__':
    cloud = bool(os.environ.get('RAILWAY_ENVIRONMENT') or os.environ.get('RENDER'))
    public = '--public' in sys.argv or '-p' in sys.argv
    ip = local_ip()
    print()
    print('  ======================================================')
    print('   THE TOME -- Campaign Server')
    print('  ======================================================')
    if cloud:
        print('   Running on cloud host — always on.')
        print(f'   Data stored in  {DATA_FILE}')
    else:
        print(f'   DM opens      ->  http://localhost:{PORT}')
        print(f'   Same WiFi     ->  http://{ip}:{PORT}')
        if public:
            print('   Public link   ->  starting tunnel...')
        print()
        print(f'   Data saved in  tome_data.json')
    print('   Press Ctrl+C to stop.')
    print('  ======================================================')
    print()
    if not cloud:
        if public:
            threading.Thread(target=start_tunnel, daemon=True).start()
        try:
            webbrowser.open(f'http://localhost:{PORT}')
        except Exception:
            pass
    server = HTTPServer(('0.0.0.0', PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n  Server stopped. Data saved in tome_data.json')
