"""
사번 단위 로그인 실패 스로틀 (Redis).

[도입 배경]
IP 기준 리밋만으로는 브루트포스를 제대로 막지도, 정상 사용자를 보호하지도 못했다.
- 프록시 뒤라 IP가 한 점으로 수렴하거나(limiter.py 참고), 시연장 공유 WiFi(NAT)처럼
  여러 사람이 같은 공인 IP를 쓰면 서로의 오타에 서로가 잠긴다.
- 반대로 공격자는 IP만 바꾸면 같은 계정을 계속 두드릴 수 있다.

그래서 방어 축을 "IP"가 아니라 **"어느 계정을 두드리고 있는가"** 로 옮긴다.

설계 원칙:
1. **실패했을 때만 카운트한다.** 종전 slowapi 데코레이터는 성공/실패를 구분하지 않아,
   정상적으로 로그인하는 사람도 남의 실패 시도와 같은 예산을 소모했다.
2. **성공하면 즉시 리셋한다.** 비밀번호를 기억해낸 사용자를 계속 벌주지 않는다.
3. **계정을 잠그지 않는다.** 남의 사번으로 일부러 실패시켜 잠그는 DoS를 막기 위해,
   영구 잠금이 아니라 짧은 TTL(기본 5분) 스로틀로만 제한한다.
4. **Redis 장애 시 fail-open.** 부가 방어 장치 때문에 로그인 자체가 불가능해지면 안 된다.
   (동일 원칙: Critic Stage B, Vision 검증 함수)
"""

from typing import Optional, Tuple

import redis

from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

_KEY_PREFIX = "auth:fail:"


def _get_client() -> Optional[redis.Redis]:
    try:
        return redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    except Exception as e:
        logger.warning(
            f"[LoginThrottle] Redis 연결 실패({e}) - 스로틀 비활성 상태로 진행(fail-open)"
        )
        return None


def _key(employee_id: str) -> str:
    return f"{_KEY_PREFIX}{employee_id}"


def get_throttle_state(employee_id: str) -> Tuple[bool, int, int]:
    """
    현재 스로틀 상태를 조회한다.

    Returns:
        (차단여부, 남은 시도 횟수, 해제까지 남은 초)
    """
    client = _get_client()
    if client is None:
        return (False, settings.LOGIN_FAIL_MAX_ATTEMPTS, 0)

    try:
        key = _key(employee_id)
        raw = client.get(key)
        fails = int(raw) if raw else 0
        if fails < settings.LOGIN_FAIL_MAX_ATTEMPTS:
            return (False, settings.LOGIN_FAIL_MAX_ATTEMPTS - fails, 0)

        ttl = client.ttl(key)
        # TTL이 없는(-1) 비정상 키는 창 길이로 되살려 영구 차단이 남지 않게 한다.
        if ttl is None or ttl < 0:
            client.expire(key, settings.LOGIN_FAIL_WINDOW_SECONDS)
            ttl = settings.LOGIN_FAIL_WINDOW_SECONDS
        return (True, 0, int(ttl))
    except Exception as e:
        logger.warning(f"[LoginThrottle] 상태 조회 실패({e}) - fail-open")
        return (False, settings.LOGIN_FAIL_MAX_ATTEMPTS, 0)


def register_failure(employee_id: str) -> int:
    """로그인 실패 1건을 기록하고 남은 시도 횟수를 반환한다."""
    client = _get_client()
    if client is None:
        return settings.LOGIN_FAIL_MAX_ATTEMPTS

    try:
        key = _key(employee_id)
        fails = client.incr(key)
        # 첫 실패에만 TTL을 건다 - 실패할 때마다 갱신하면 창이 무한히 밀려 사실상 영구 차단이 된다.
        if fails == 1:
            client.expire(key, settings.LOGIN_FAIL_WINDOW_SECONDS)
        return max(0, settings.LOGIN_FAIL_MAX_ATTEMPTS - int(fails))
    except Exception as e:
        logger.warning(f"[LoginThrottle] 실패 기록 실패({e}) - fail-open")
        return settings.LOGIN_FAIL_MAX_ATTEMPTS


def clear(employee_id: str) -> None:
    """로그인 성공 시 실패 카운터를 지운다."""
    client = _get_client()
    if client is None:
        return
    try:
        client.delete(_key(employee_id))
    except Exception as e:
        logger.warning(f"[LoginThrottle] 카운터 초기화 실패({e}) - 무시")
