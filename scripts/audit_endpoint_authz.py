# -*- coding: utf-8 -*-
"""인가 전수 검사 — 실제 호출로 확인한다.

정적 분석(FastAPI _IncludedRouter) 대신 **살아 있는 서버를 직접 두드린다.**
/openapi.json에서 전 경로를 뽑아 인증 없이 GET을 보내고 응답 코드를 본다.

  401/403 → 보호됨
  200     → 무인증 노출 (설계상 공개가 아니면 결함)

부작용을 피하려고 **GET만** 보낸다. 경로 파라미터는 더미 UUID로 채운다.
"""
import json
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding='utf-8')

BASE = 'http://localhost:8000'
DUMMY = {
    'job_id': '00000000-0000-0000-0000-000000000000',
    'post_id': '00000000-0000-0000-0000-000000000000',
    'lpn': 'LPN-000000-X000', 'lpn_barcode': 'LPN-000000-X000',
    'item_id': '00000000-0000-0000-0000-000000000000',
    'id': '00000000-0000-0000-0000-000000000000',
    'instruction_id': '00000000-0000-0000-0000-000000000000',
    'order_id': '00000000-0000-0000-0000-000000000000',
    'book_id': '00000000-0000-0000-0000-000000000000',
    'employee_id': 'WM0000000', 'isbn': '0000000000000',
}
# 설계상 비로그인 허용 (고객 QR 보증서 · 로그인/로그아웃)
PUBLIC_OK = ('/api/v1/auth/login', '/api/v1/auth/logout',
             '/api/v1/certificate', '/api/v1/lpn')


def fill(path):
    out = path
    while '{' in out:
        s = out.index('{')
        e = out.index('}', s)
        name = out[s + 1:e]
        out = out[:s] + DUMMY.get(name, '0') + out[e + 1:]
    return out


def probe(url):
    req = urllib.request.Request(url, method='GET')
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return -1


spec = json.load(urllib.request.urlopen(f'{BASE}/openapi.json', timeout=20))
paths = spec.get('paths', {})

gets = [(p, m) for p, ops in paths.items() for m in ops if m.lower() == 'get']
print(f'OpenAPI 경로 {len(paths)}개 · GET 엔드포인트 {len(gets)}개')
print('인증 없이 호출해 응답 코드를 확인합니다...\n')

protected, public, holes, errors = [], [], [], []
for p, _ in sorted(gets):
    code = probe(f'{BASE}{fill(p)}')
    is_public_ok = any(p.startswith(x) for x in PUBLIC_OK)
    if code in (401, 403):
        protected.append((p, code))
    elif code == -1:
        errors.append((p, code))
    elif is_public_ok:
        public.append((p, code))
    else:
        holes.append((p, code))

print(f'✅ 인증 요구 (401/403)  {len(protected)}')
print(f'⚪ 공개 허용            {len(public)}')
print(f'🔴 무인증 응답          {len(holes)}')
print(f'⚠️  호출 실패           {len(errors)}')

if holes:
    print('\n' + '=' * 74)
    print('🔴 인증 없이 응답한 엔드포인트')
    print('=' * 74)
    for p, c in holes:
        print(f'  HTTP {c}  GET {p}')
if public:
    print('\n' + '=' * 74)
    print('⚪ 공개 허용 (설계상)')
    print('=' * 74)
    for p, c in public:
        print(f'  HTTP {c}  GET {p}')
if errors:
    print('\n⚠️ 호출 실패:', [p for p, _ in errors])
