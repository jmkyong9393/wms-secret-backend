#!/usr/bin/env bash
# 이식 스모크 테스트 - 리허설(로컬)과 컷오버(Lightsail) 양쪽에서 같은 것을 검사한다.
#
#   사용:  ./smoke-test.sh <BASE_URL> <EMPLOYEE_ID> <PASSWORD>
#   예:    ./smoke-test.sh http://localhost:8080 WM2608001 1234
#
# 검사 항목은 "이식에서 실제로 깨질 수 있는 것"으로 한정한다:
#   런타임 env 주입(BACKEND_ORIGIN 오버라이드) → rewrite 배선 → 쿠키 인증 →
#   xgboost 로드(의존성 이식) → SSE 스트림(프록시 버퍼링) 순.
set -u

BASE="${1:?BASE_URL 필요 (예: http://localhost:8080)}"
EMP="${2:?EMPLOYEE_ID 필요}"
PW="${3:?PASSWORD 필요}"
PASS=0; FAIL=0
JAR="$(mktemp)"
trap 'rm -f "$JAR"' EXIT

ok()   { PASS=$((PASS+1)); echo "  [PASS] $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  [FAIL] $1"; }

echo "== 1. 프론트 응답 (Caddy → Next) =="
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$BASE/login")
[ "$code" = "200" ] && ok "GET /login → 200" || bad "GET /login → $code"

echo "== 2. 로그인 (rewrite → 백엔드, HttpOnly 쿠키) =="
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -c "$JAR" \
  -X POST "$BASE/api/v1/auth/login" -H "Content-Type: application/json" \
  -d "{\"employee_id\":\"$EMP\",\"password\":\"$PW\"}")
[ "$code" = "200" ] && ok "POST auth/login → 200" || bad "POST auth/login → $code"
grep -q "token" "$JAR" && ok "token 쿠키 수신" || bad "token 쿠키 없음"

echo "== 3. 헬스 + 프라이싱 모델 (xgboost 이식 검증) =="
health=$(curl -s --max-time 20 "$BASE/api/v1/health")
echo "     $health"
echo "$health" | grep -q '"status": *"ok"' && ok "health ok" || bad "health 비정상"
echo "$health" | grep -q '"pricing_model": *"xgboost"' \
  && ok "pricing_model=xgboost (모델 로드됨)" \
  || bad "pricing_model이 xgboost가 아님 - 의존성 이식 실패 신호"

echo "== 4. 인증 API 3종 =="
for ep in "inventory?limit=1" "orders/picking-instructions?active_only=true&limit=1" "po/proposals"; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 25 -b "$JAR" "$BASE/api/v1/$ep")
  [ "$code" = "200" ] && ok "GET $ep → 200" || bad "GET $ep → $code"
done

echo "== 5. SSE 스트림 (프록시 버퍼링·압축 검증) =="
# 30초 안에 CONNECTED(즉시)와 heartbeat(25초 주기)가 모두 와야 한다.
# 프록시가 버퍼링하면 아무것도 오지 않는다 - gzip 결함(2026-08-26)의 회귀 검사다.
sse=$(curl -s -N --max-time 30 -b "$JAR" -H "Accept: text/event-stream" \
  "$BASE/api/v1/notifications/stream" 2>/dev/null)
echo "$sse" | grep -q "CONNECTED" && ok "CONNECTED 프레임 수신" || bad "CONNECTED 미수신 - 프록시 버퍼링 의심"
echo "$sse" | grep -q "event: heartbeat" && ok "heartbeat 프레임 수신" || bad "heartbeat 미수신"

echo
echo "결과: PASS $PASS / FAIL $FAIL"
[ "$FAIL" = "0" ] || exit 1
