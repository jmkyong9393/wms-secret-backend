"""
프린트 브리지 에이전트 — 창고 PC에서 상시 실행하는 라벨 인쇄 중계기.

클라우드에 배포된 백엔드는 창고 사설망의 프린터(XP-423B LAN, Raw TCP 9100)에
직접 닿을 수 없다. 이 에이전트가 백엔드의 인쇄 큐를 폴링해 로컬 프린터로 중계한다.

  [클라우드 백엔드] --(LABEL_PRINT_MODE=QUEUE, label_print_jobs 적재)-->
  [이 에이전트: GET /labels/jobs/pending] --> [프린터 IP:9100 Raw TCP] --> [POST ack]

의존성 제로(파이썬 표준 라이브러리만) — 창고 PC에 파이썬만 있으면 된다.

사용법 (환경변수로 설정):
  NEXUS_API_BASE   백엔드 주소 (예: https://api.nexus-wms.com)  [기본 http://localhost:8000]
  NEXUS_BRIDGE_ID  로그인 사번 (MASTER/ADMIN 권한 계정)          [필수]
  NEXUS_BRIDGE_PW  비밀번호                                      [필수]
  PRINTER_HOST     프린터 LAN IP                                 [필수]
  PRINTER_PORT     프린터 포트                                   [기본 9100]
  POLL_SECONDS     폴링 주기(초)                                 [기본 3]
  DRY_RUN          "1"이면 프린터 전송 생략하고 성공 처리 (리허설용)

  python scripts/print_bridge_agent.py
"""
import json
import logging
import os
import socket
import sys
import time
import urllib.error
import urllib.request

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("print-bridge")

API_BASE = os.getenv("NEXUS_API_BASE", "http://localhost:8000").rstrip("/")
BRIDGE_ID = os.getenv("NEXUS_BRIDGE_ID", "")
BRIDGE_PW = os.getenv("NEXUS_BRIDGE_PW", "")
PRINTER_HOST = os.getenv("PRINTER_HOST", "")
PRINTER_PORT = int(os.getenv("PRINTER_PORT", "9100"))
POLL_SECONDS = float(os.getenv("POLL_SECONDS", "3"))
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"


class BridgeAuthError(RuntimeError):
    pass


def _request(method: str, path: str, body: dict | None = None, cookie: str | None = None):
    """표준 라이브러리만으로 JSON 요청을 보낸다. (응답 JSON, Set-Cookie 헤더) 반환."""
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if cookie:
        req.add_header("Cookie", cookie)
    with urllib.request.urlopen(req, timeout=15) as res:
        set_cookie = res.headers.get("Set-Cookie")
        raw = res.read()
        payload = json.loads(raw) if raw else None
        return payload, set_cookie


def login() -> str:
    """백엔드에 로그인해 인증 쿠키 문자열을 확보한다."""
    if not BRIDGE_ID or not BRIDGE_PW:
        raise BridgeAuthError("NEXUS_BRIDGE_ID / NEXUS_BRIDGE_PW 환경변수가 필요합니다.")
    _, set_cookie = _request(
        "POST", "/api/v1/auth/login",
        {"employee_id": BRIDGE_ID, "password": BRIDGE_PW},
    )
    if not set_cookie or "token=" not in set_cookie:
        raise BridgeAuthError("로그인 응답에 인증 쿠키가 없습니다.")
    token = set_cookie.split("token=")[1].split(";")[0]
    logger.info("백엔드 로그인 성공 (%s)", BRIDGE_ID)
    return f"token={token}"


def send_to_printer(zpl: str) -> int:
    """ZPL을 프린터 Raw TCP 포트로 전송한다. 전송 바이트 수 반환."""
    if DRY_RUN:
        logger.info("[DRY_RUN] 프린터 전송 생략 (%d chars)", len(zpl))
        return 0
    if not PRINTER_HOST:
        raise RuntimeError("PRINTER_HOST 환경변수가 필요합니다.")
    payload = zpl.encode("utf-8")
    with socket.create_connection((PRINTER_HOST, PRINTER_PORT), timeout=5) as s:
        s.sendall(payload)
    return len(payload)


def run_once(cookie: str) -> int:
    """대기 작업 1회 처리. 처리한 건수 반환."""
    jobs, _ = _request("GET", "/api/v1/labels/jobs/pending?limit=20", cookie=cookie)
    if not jobs:
        return 0
    for job in jobs:
        job_id = job["id"]
        try:
            sent = send_to_printer(job["zpl"])
            _request("POST", f"/api/v1/labels/jobs/{job_id}/ack",
                     {"success": True}, cookie=cookie)
            logger.info("인쇄 완료 lpn=%s job=%s bytes=%d", job["lpn"], job_id, sent)
        except Exception as e:
            # 프린터 장애: 실패 보고 후 다음 작업 계속 (큐에 FAILED로 기록되어 관리자가 확인)
            _request("POST", f"/api/v1/labels/jobs/{job_id}/ack",
                     {"success": False, "error": str(e)[:400]}, cookie=cookie)
            logger.error("인쇄 실패 lpn=%s job=%s err=%s", job["lpn"], job_id, e)
    return len(jobs)


def main() -> None:
    logger.info("프린트 브리지 시작 api=%s printer=%s:%s dry_run=%s",
                API_BASE, PRINTER_HOST or "(미설정)", PRINTER_PORT, DRY_RUN)
    cookie = login()
    backoff = POLL_SECONDS
    while True:
        try:
            processed = run_once(cookie)
            backoff = POLL_SECONDS  # 정상 사이클이면 폴링 주기 복원
            if processed:
                continue  # 작업이 있었으면 즉시 다음 폴링 (버스트 소화)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                logger.warning("인증 만료 - 재로그인")
                cookie = login()
                continue
            logger.error("API 오류 %s - %s초 후 재시도", e, backoff)
            backoff = min(backoff * 2, 60)
        except Exception as e:
            logger.error("폴링 실패 %s - %s초 후 재시도", e, backoff)
            backoff = min(backoff * 2, 60)
        time.sleep(backoff)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
