# -*- coding: utf-8 -*-
"""인가 전수 검사 — 실제 호출로 확인한다.

정적 분석(FastAPI _IncludedRouter) 대신 **살아 있는 서버를 직접 두드린다.**
/openapi.json에서 전 경로를 뽑아 인증 없이 호출하고 응답 코드를 본다.

  401/403 → 보호됨
  200     → 무인증 노출 (설계상 공개가 아니면 결함)

기본은 GET만 본다. `--write`를 주면 POST/PUT/PATCH/DELETE까지 검사한다.

## 쓰기 검사가 안전한 이유

인가가 걸려 있으면 FastAPI가 **핸들러에 닿기 전에** 401을 낸다. 즉 응답 코드가
곧 "부작용이 일어났는가"의 지표다.

  401/403 → 의존성에서 차단. 핸들러 미도달 = 부작용 없음
  422     → 인가는 통과했으나 본문 검증에서 걸림. **인가 구멍이지만 부작용은 없음**
  그 외   → 핸들러 도달. **인가 구멍 + 부작용 가능** (최우선 조치 대상)

여기에 더해 본문을 비워(`{}`) 보내 필수 필드 검증에 먼저 걸리게 하고,
실행 전후 주요 테이블 건수를 대조해 부작용이 실제로 없었는지 **실측으로** 확인한다.

**로컬 스택에서만 실행한다.** 라이브(운영)에는 쓰기 검사를 돌리지 않는다.
"""
import json
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding='utf-8')

BASE = 'http://localhost:8000'
WRITE_MODE = '--write' in sys.argv
# 부작용 실측용 — 검사 전후로 건수를 대조한다
COUNT_TABLES = [
    'return_jobs', 'inventory_used_items', 'inventory', 'orders', 'order_items',
    'picking_instructions', 'picking_instruction_items', 'inventory_logs',
    'admin_audit_logs', 'users', 'order_proposals', 'notifications',
]
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


def probe(url, method='GET'):
    body = None
    headers = {}
    if method in ('POST', 'PUT', 'PATCH'):
        # 빈 본문을 보낸다. 인가를 통과해도 필수 필드 검증(422)에서 먼저 걸려
        # 핸들러에 도달하지 않는다.
        body = b'{}'
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return -1


def table_counts():
    """부작용 실측용 스냅샷. DB에 붙지 못하면 None을 돌려 건너뛴다."""
    try:
        from sqlalchemy import text
        from app.db.session import engine
        out = {}
        with engine.connect() as c:
            for t in COUNT_TABLES:
                try:
                    out[t] = c.execute(text(f'select count(*) from {t}')).scalar()
                except Exception:
                    pass
        return out
    except Exception as e:
        print(f'[건수 스냅샷 생략] DB 접속 불가: {type(e).__name__}')
        return None


spec = json.load(urllib.request.urlopen(f'{BASE}/openapi.json', timeout=20))
paths = spec.get('paths', {})

WANT = {'get', 'post', 'put', 'patch', 'delete'} if WRITE_MODE else {'get'}
targets = [(p, m.upper()) for p, ops in paths.items() for m in ops if m.lower() in WANT]
print(f'OpenAPI 경로 {len(paths)}개 · 검사 대상 {len(targets)}개 '
      f'({"쓰기 포함" if WRITE_MODE else "조회만"})')
print('인증 없이 호출해 응답 코드를 확인합니다...\n')

before = table_counts() if WRITE_MODE else None

protected, public, holes, reached, errors = [], [], [], [], []
for p, m in sorted(targets):
    code = probe(f'{BASE}{fill(p)}', m)
    is_public_ok = any(p.startswith(x) for x in PUBLIC_OK)
    if code in (401, 403):
        protected.append((p, m, code))
    elif code == -1:
        errors.append((p, m, code))
    elif is_public_ok:
        public.append((p, m, code))
    elif code == 422:
        # 인가는 통과했으나 본문 검증에서 걸렸다. 구멍이지만 핸들러 미도달.
        holes.append((p, m, code))
    else:
        # 핸들러까지 갔다. 구멍이면서 부작용 가능.
        holes.append((p, m, code))
        if m != 'GET':
            reached.append((p, m, code))

print(f'✅ 인증 요구 (401/403)  {len(protected)}')
print(f'⚪ 공개 허용            {len(public)}')
print(f'🔴 무인증 응답          {len(holes)}')
print(f'⚠️  호출 실패           {len(errors)}')
if WRITE_MODE:
    print(f'🚨 쓰기 핸들러 도달     {len(reached)}  ← 부작용 가능')

if holes:
    print('\n' + '=' * 74)
    print('🔴 인증 없이 응답한 엔드포인트')
    print('=' * 74)
    for p, m, c in holes:
        note = '  (본문 검증에서 차단 — 핸들러 미도달)' if c == 422 else ''
        print(f'  HTTP {c}  {m:6} {p}{note}')
if public:
    print('\n' + '=' * 74)
    print('⚪ 공개 허용 (설계상)')
    print('=' * 74)
    for p, m, c in public:
        print(f'  HTTP {c}  {m:6} {p}')
if errors:
    print('\n⚠️ 호출 실패:', [f'{m} {p}' for p, m, _ in errors])

# 부작용 실측 — 건수가 하나라도 바뀌었으면 검사 자체가 데이터를 건드린 것이다
if WRITE_MODE and before is not None:
    after = table_counts() or {}
    moved = {t: (before[t], after.get(t))
             for t in before if after.get(t) != before[t]}
    print('\n' + '=' * 74)
    print('부작용 실측 — 검사 전후 테이블 건수')
    print('=' * 74)
    if moved:
        print('🚨 건수가 변한 테이블이 있다. 검사가 데이터를 바꿨다:')
        for t, (b, a) in moved.items():
            print(f'  {t}: {b} → {a}')
    else:
        print(f'✅ {len(before)}개 테이블 전부 변동 없음 — 부작용 0건')
