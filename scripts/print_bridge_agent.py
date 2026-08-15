"""
프린트 브리지 에이전트 — 창고/시연 PC에서 상시 실행하는 라벨 인쇄 중계기.

클라우드에 배포된 백엔드는 창고 사설망의 프린터(XP-423B)에 직접 닿을 수 없다.
이 에이전트가 백엔드의 인쇄 큐를 폴링해 로컬 프린터로 중계한다.

  [클라우드 백엔드] --(LABEL_PRINT_MODE=QUEUE, label_print_jobs 적재)-->
  [이 에이전트: GET /labels/jobs/pending] --> [프린터] --> [POST ack]

프린터로 보내는 마지막 구간은 두 방식 중 하나를 쓴다 (PRINTER_MODE로 선택):

  TCP (기본) : 프린터가 LAN에 붙어 IP를 가진 경우. Raw TCP 9100 직결.
               파이썬 표준 라이브러리만 쓴다.
  USB        : 프린터를 PC에 USB로 직결한 경우. Windows 프린터 큐에 RAW로 밀어넣는다.
               pywin32 필요(`pip install pywin32`)하고 Windows에서만 동작하며,
               해당 프린터가 OS에 정상 설치되어 있어야 한다.

설정은 **같은 폴더의 `bridge_config.json`**에서 읽는다. 파일이 없으면 환경변수를 쓰고,
둘 다 있으면 환경변수가 우선한다(임시로 값 하나만 바꿔 실행할 때 편하도록).

  bridge_config.json 예시:
  {
    "NEXUS_API_BASE": "https://nexus-wms.p-e.kr",
    "NEXUS_BRIDGE_ID": "WM2608001",
    "NEXUS_BRIDGE_PW": "1234",
    "PRINTER_MODE": "TCP",
    "PRINTER_HOST": "192.168.0.7"
  }

설정 항목:
  NEXUS_API_BASE   백엔드 주소 (예: https://nexus-wms.p-e.kr)   [기본 http://localhost:8000]
  NEXUS_BRIDGE_ID  브리지 전용 로그인 사번 (MASTER/ADMIN 권한)   [필수]
  NEXUS_BRIDGE_PW  비밀번호                                      [필수]
  PRINTER_MODE     TCP | USB                                     [기본 TCP]
  PRINTER_HOST     프린터 LAN IP                                 [TCP 모드에서 필수]
  PRINTER_PORT     프린터 포트                                   [기본 9100]
  PRINTER_NAME     Windows 프린터 이름 (제어판 표기 그대로)       [USB 모드에서 필수]
  POLL_SECONDS     폴링 주기(초)                                 [기본 3]
  DRY_RUN          "1"이면 프린터 전송 생략하고 성공 처리 (리허설용)

`NEXUS_BRIDGE_ID`는 **인쇄를 누른 사람이 아니라 브리지가 큐를 꺼낼 권한을 얻는 서비스
계정**이다. 큐 조회는 사용자별 필터 없이 대기 중인 전 건을 가져오므로, 누가(어느 계정이)
인쇄를 눌렀든 이 계정 하나로 전부 중계된다 — 계정을 여러 개 등록할 필요가 없다.

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
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("print-bridge")

def _app_dir() -> Path:
    """
    설정 파일을 찾을 기준 폴더.

    PyInstaller 단일파일(.exe)로 묶이면 __file__은 임시 해제 폴더를 가리키므로
    설정 파일을 찾지 못한다. 이 경우 실행파일 자신의 위치를 기준으로 삼는다.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


_CONFIG_PATH = _app_dir() / "bridge_config.json"


def _load_config() -> dict:
    """같은 폴더의 bridge_config.json을 읽는다. 없거나 깨져 있으면 빈 설정으로 진행한다."""
    if not _CONFIG_PATH.exists():
        return {}
    try:
        with _CONFIG_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("설정 파일을 읽지 못해 환경변수만 사용합니다 (%s): %s", _CONFIG_PATH.name, e)
        return {}


_CONFIG = _load_config()


def _setting(key: str, default: str = "") -> str:
    """환경변수 > 설정 파일 > 기본값 순으로 설정을 읽는다."""
    env = os.getenv(key)
    if env not in (None, ""):
        return env
    value = _CONFIG.get(key)
    return str(value) if value not in (None, "") else default


API_BASE = _setting("NEXUS_API_BASE", "http://localhost:8000").rstrip("/")
BRIDGE_ID = _setting("NEXUS_BRIDGE_ID")
BRIDGE_PW = _setting("NEXUS_BRIDGE_PW")
PRINTER_MODE = _setting("PRINTER_MODE", "TCP").strip().upper()
PRINTER_HOST = _setting("PRINTER_HOST")
PRINTER_PORT = int(_setting("PRINTER_PORT", "9100"))
PRINTER_NAME = _setting("PRINTER_NAME")
POLL_SECONDS = float(_setting("POLL_SECONDS", "3"))
DRY_RUN = _setting("DRY_RUN", "0") == "1"


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
        raise BridgeAuthError(
            "NEXUS_BRIDGE_ID / NEXUS_BRIDGE_PW가 없습니다. "
            f"{_CONFIG_PATH.name}에 설정하거나 환경변수로 지정하세요."
        )
    _, set_cookie = _request(
        "POST", "/api/v1/auth/login",
        {"employee_id": BRIDGE_ID, "password": BRIDGE_PW},
    )
    if not set_cookie or "token=" not in set_cookie:
        raise BridgeAuthError("로그인 응답에 인증 쿠키가 없습니다.")
    token = set_cookie.split("token=")[1].split(";")[0]
    logger.info("백엔드 로그인 성공 (%s)", BRIDGE_ID)
    return f"token={token}"


def _send_tcp(payload: bytes) -> int:
    """프린터 Raw TCP 포트(9100)로 직접 전송한다."""
    if not PRINTER_HOST:
        raise RuntimeError("PRINTER_MODE=TCP에는 PRINTER_HOST 환경변수가 필요합니다.")
    with socket.create_connection((PRINTER_HOST, PRINTER_PORT), timeout=5) as s:
        s.sendall(payload)
    return len(payload)


def _send_usb(payload: bytes) -> int:
    """
    Windows 프린터 큐에 RAW 데이터로 밀어넣는다 (USB 직결용).

    RAW 데이터 타입으로 보내야 드라이버가 ZPL을 그림/텍스트로 재해석하지 않고
    프린터에 원문 그대로 전달한다.
    """
    if not PRINTER_NAME:
        raise RuntimeError("PRINTER_MODE=USB에는 PRINTER_NAME 환경변수가 필요합니다.")
    try:
        import win32print  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "PRINTER_MODE=USB에는 pywin32가 필요합니다. `pip install pywin32` 후 재실행하세요."
        ) from exc

    handle = win32print.OpenPrinter(PRINTER_NAME)
    try:
        job = win32print.StartDocPrinter(handle, 1, ("Nexus LPN Label", None, "RAW"))
        try:
            win32print.StartPagePrinter(handle)
            win32print.WritePrinter(handle, payload)
            win32print.EndPagePrinter(handle)
        finally:
            win32print.EndDocPrinter(handle)
        logger.debug("USB 인쇄 작업 제출 job=%s", job)
    finally:
        win32print.ClosePrinter(handle)
    return len(payload)


def send_to_printer(zpl: str) -> int:
    """ZPL을 설정된 경로(TCP/USB)로 프린터에 전송한다. 전송 바이트 수 반환."""
    if DRY_RUN:
        logger.info("[DRY_RUN] 프린터 전송 생략 (%d chars)", len(zpl))
        return 0
    payload = zpl.encode("utf-8")
    if PRINTER_MODE == "USB":
        return _send_usb(payload)
    if PRINTER_MODE == "TCP":
        return _send_tcp(payload)
    raise RuntimeError(f"알 수 없는 PRINTER_MODE={PRINTER_MODE} (TCP 또는 USB만 지원)")


# ======================================================================
# 진단 모드 (--doctor)
# ======================================================================
# 2026-08-11 시연 실패 후 신설. 현장에서 "프린터가 어느 대역에 있는지"를 몰라
# 헤매다 제한시간을 넘겼다. 그 판단에 필요한 정보를 한 번에 모아 보여준다.

_VIRTUAL_ADAPTER_HINTS = ("VirtualBox", "VMware", "VMnet", "Hyper-V", "Loopback", "vEthernet")


def _local_ipv4_by_adapter() -> list[tuple[str, str]]:
    """(어댑터 이름, IPv4) 목록. Windows는 ipconfig, 그 외는 호스트명 기반 폴백."""
    import subprocess

    results: list[tuple[str, str]] = []
    if os.name != "nt":
        try:
            host_ips = socket.gethostbyname_ex(socket.gethostname())[2]
            return [("(local)", ip) for ip in host_ips]
        except OSError:
            return results

    try:
        raw = subprocess.run(
            ["ipconfig"], capture_output=True, timeout=10,
        ).stdout.decode("cp949", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return results

    current = "?"
    for line in raw.splitlines():
        if line and not line.startswith(" "):
            current = line.strip().rstrip(":")
        elif "IPv4" in line and ":" in line:
            ip = line.split(":", 1)[1].strip()
            if ip and not ip.startswith("127."):
                results.append((current, ip))
    return results


def _probe_9100(ip: str, timeout: float = 0.35) -> bool:
    """해당 IP의 9100 포트가 열려 있는지 빠르게 확인한다."""
    try:
        with socket.create_connection((ip, 9100), timeout=timeout):
            return True
    except OSError:
        return False


def _scan_subnet_for_printers(base_ip: str, limit: int = 254) -> list[str]:
    """base_ip와 같은 /24 대역에서 9100 포트가 열린 호스트를 찾는다 (병렬)."""
    from concurrent.futures import ThreadPoolExecutor

    prefix = base_ip.rsplit(".", 1)[0]
    targets = [f"{prefix}.{i}" for i in range(1, limit + 1) if f"{prefix}.{i}" != base_ip]
    found: list[str] = []
    with ThreadPoolExecutor(max_workers=64) as pool:
        for ip, ok in zip(targets, pool.map(_probe_9100, targets)):
            if ok:
                found.append(ip)
    return found


def _arp_candidates() -> list[str]:
    """ARP 테이블에 잡힌 IP 목록 (이미 통신한 적 있는 장치들)."""
    import re
    import subprocess

    try:
        raw = subprocess.run(
            ["arp", "-a"], capture_output=True, timeout=10,
        ).stdout.decode("cp949", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return []
    ips = re.findall(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", raw)
    return [ip for ip in dict.fromkeys(ips) if not ip.startswith(("127.", "224.", "239.", "255."))]


def doctor() -> None:
    """현재 환경을 점검하고, 프린터를 찾지 못하면 직접 스캔해 알려준다."""
    print("=" * 62)
    print("  프린트 브리지 진단")
    print("=" * 62)

    # 1. 네트워크 어댑터
    print("\n[1] 이 PC의 네트워크 주소")
    adapters = _local_ipv4_by_adapter()
    real_ips: list[str] = []
    if not adapters:
        print("    (주소를 읽지 못했습니다. `ipconfig`를 직접 확인하세요)")
    for name, ip in adapters:
        virtual = any(h.lower() in name.lower() for h in _VIRTUAL_ADAPTER_HINTS)
        tag = "  <- 가상 어댑터(무시/비활성화 권장)" if virtual else ""
        print(f"    {ip:<16} {name}{tag}")
        if not virtual:
            real_ips.append(ip)

    # 2. 백엔드 연결
    print(f"\n[2] 백엔드 연결 ({API_BASE})")
    try:
        login()
        print("    OK - 로그인 성공")
    except Exception as e:
        print(f"    실패 - {e}")
        print("    => 인터넷 연결(WiFi/핫스팟)과 계정 정보를 확인하세요.")

    # 3. 설정된 프린터
    print(f"\n[3] 설정된 프린터 (PRINTER_MODE={PRINTER_MODE})")
    if PRINTER_MODE == "USB":
        print(f"    USB 모드 / PRINTER_NAME={PRINTER_NAME or '(미설정)'}")
        try:
            import win32print  # type: ignore
            names = [p[2] for p in win32print.EnumPrinters(2)]
            print(f"    이 PC에 설치된 프린터: {', '.join(names) or '(없음)'}")
            if PRINTER_NAME and PRINTER_NAME not in names:
                print("    => PRINTER_NAME이 위 목록에 없습니다. 이름을 정확히 맞추세요.")
        except ImportError:
            print("    pywin32 미설치 - `pip install pywin32` 필요")
    else:
        if not PRINTER_HOST:
            print("    PRINTER_HOST가 비어 있습니다.")
        elif _probe_9100(PRINTER_HOST, timeout=2.0):
            print(f"    OK - {PRINTER_HOST}:9100 응답함. 인쇄 준비 완료.")
            print("=" * 62)
            return
        else:
            print(f"    실패 - {PRINTER_HOST}:9100 무응답")
            same = [ip for ip in real_ips if ip.rsplit(".", 1)[0] == PRINTER_HOST.rsplit(".", 1)[0]]
            if not same:
                print(f"    => 이 PC에는 {PRINTER_HOST.rsplit('.', 1)[0]}.x 대역 주소가 없습니다.")
                print("       (프린터와 PC가 서로 다른 대역 = 통신 불가. 아래 스캔 결과 참고)")

    # 4. 프린터 자동 탐색
    print("\n[4] 프린터 자동 탐색 (9100 포트 응답 확인)")
    found: list[str] = []

    arp_ips = _arp_candidates()
    if arp_ips:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=64) as pool:
            for ip, ok in zip(arp_ips, pool.map(_probe_9100, arp_ips)):
                if ok:
                    found.append(ip)

    for ip in real_ips:
        print(f"    {ip.rsplit('.', 1)[0]}.x 대역 스캔 중...")
        for hit in _scan_subnet_for_printers(ip):
            if hit not in found:
                found.append(hit)

    print()
    if found:
        print("    ★ 프린터로 보이는 장치를 찾았습니다:")
        for ip in found:
            print(f"        {ip}")
        print()
        print(f"    => bridge_config.json 의 PRINTER_HOST 를 위 주소로 바꾸고 다시 실행하세요.")
    else:
        print("    프린터를 찾지 못했습니다. 아래를 순서대로 확인하세요:")
        print("      1) 프린터 전원 ON, 랜 케이블 연결, RJ45 옆 LED 점등 확인")
        print("      2) 프린터 셀프테스트로 IP 확인:")
        print("         전원 OFF -> FEED 버튼 누른 채 전원 ON -> 용지 나오면 손 뗌")
        print("         출력물의 IP Address 를 확인")
        print("      3) 그 IP가 169.254.x.x 라면 프린터가 DHCP를 못 받은 상태입니다.")
        print("         PC 유선랜에 수동 IP를 주어 같은 대역으로 맞추세요:")
        print("           IP 169.254.88.100 / 서브넷 255.255.0.0 / 게이트웨이 비움")
        print("      4) VirtualBox/VMware 가상 어댑터가 있으면 잠시 '사용 안 함' 처리")
    print("=" * 62)


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
    target = f"{PRINTER_HOST or '(미설정)'}:{PRINTER_PORT}" if PRINTER_MODE == "TCP" else (PRINTER_NAME or "(미설정)")
    logger.info("프린트 브리지 시작 api=%s mode=%s printer=%s dry_run=%s config=%s",
                API_BASE, PRINTER_MODE, target, DRY_RUN,
                _CONFIG_PATH.name if _CONFIG else "(없음, 환경변수 사용)")
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
        if "--doctor" in sys.argv:
            doctor()
        else:
            main()
    except KeyboardInterrupt:
        sys.exit(0)
