"""
Rate Limiter 설정.

[수정 이력 - 2026-08-06]
종전에는 slowapi 기본 제공 `get_remote_address`를 그대로 key_func으로 썼다. 그런데 이 API는
Next.js의 rewrites 프록시 뒤에 있어서, 컨테이너가 보는 peer IP가 **접속자와 무관하게 항상
도커 게이트웨이(172.20.0.1) 하나**로 수렴한다. 그 결과 "5회/분" 제한이 접속자별이 아니라
**서비스 전체 합산**으로 동작해, 한 사람이 오타를 몇 번 내면 나머지 전원이 로그인하지 못했다
(cloudflared 터널 시연 환경에서 실측 확인).

여기서는 요청이 신뢰 대역(도커 브리지/루프백)에서 들어온 경우에 한해 X-Forwarded-For의
원 클라이언트 IP를 키로 채택한다. 신뢰 대역 밖에서 온 XFF는 헤더 위조로 리밋을 우회하는
수단이 되므로 무시하고 peer IP를 그대로 쓴다.
"""
import ipaddress
from typing import List

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from app.core.config import settings


def _parse_trusted_networks() -> List[ipaddress._BaseNetwork]:
    nets = []
    for raw in (settings.TRUSTED_PROXY_CIDRS or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            nets.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            # 설정 오타 하나로 서버가 뜨지 못하게 만들지 않는다 - 해당 항목만 버린다.
            print(f"[Limiter] TRUSTED_PROXY_CIDRS 항목 파싱 실패, 무시함: {raw!r}")
    return nets


TRUSTED_NETWORKS = _parse_trusted_networks()


def _is_trusted_proxy(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr in net for net in TRUSTED_NETWORKS)


def client_ip_key(request: Request) -> str:
    """리밋 버킷 키로 쓸 클라이언트 IP를 판별한다."""
    peer = get_remote_address(request)
    if not peer or not _is_trusted_proxy(peer):
        return peer or "unknown"

    # XFF는 "원 클라이언트, 중간 프록시1, ..." 순서이므로 맨 앞이 실제 접속자다.
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return peer

    candidate = forwarded.split(",")[0].strip()
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        # 값이 IP 형태가 아니면 신뢰할 수 없다 - peer로 폴백한다.
        return peer
    return candidate


# 클라이언트 IP 기반으로 Rate Limiting을 수행하는 Limiter 인스턴스
limiter = Limiter(key_func=client_ip_key)
