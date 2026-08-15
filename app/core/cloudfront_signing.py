"""CloudFront 서명 URL 생성.

이미지 버킷은 Block Public Access가 전부 켜져 있고 CloudFront(OAC)로만 열린다.
여기에 배포 Behavior의 열람자 제한(Restrict viewer access)을 걸면, 서명이 붙은 URL만
200을 받는다. 이 모듈이 그 서명을 붙인다.

서명 쿠키가 아니라 **서명 URL**을 쓴다 - 앱 도메인(nexus-wms.p-e.kr)과 CDN 도메인
(*.cloudfront.net)의 등록 도메인이 달라 브라우저가 쿠키를 CDN으로 보내지 않는다.

키가 설정돼 있지 않으면 원본 URL을 그대로 돌려준다. 열람자 제한을 켜기 전까지는
서명 없이도 열리므로, 코드를 먼저 배포해 두고 마지막에 제한을 켤 수 있다.
"""

from __future__ import annotations

import datetime
from functools import lru_cache
from typing import Any, Optional

import rsa
from botocore.signers import CloudFrontSigner

from app.core.config import settings

# 서명 URL 기본 유효 시간. 관리자 화면 체류와 QR 보증서 열람을 함께 감당하는 값이다.
DEFAULT_EXPIRE_MINUTES = 60


def _raw_private_key() -> str:
    """설정에서 개인키를 읽는다. 환경변수로 넣을 때 개행이 \\n으로 들어오는 경우를 편다."""
    key = getattr(settings, "CLOUDFRONT_PRIVATE_KEY", "") or ""
    return key.replace("\\n", "\n").strip()


@lru_cache(maxsize=1)
def _load_private_key() -> Optional[rsa.PrivateKey]:
    """PKCS#1 PEM 개인키를 로드한다. 미설정이거나 mock이면 None."""
    raw = _raw_private_key()
    if not raw or "mock" in raw:
        return None
    try:
        return rsa.PrivateKey.load_pkcs1(raw.encode("utf-8"))
    except Exception as exc:
        print(f"[CloudFront] 개인키 로드 실패 - 서명 없이 진행합니다: {exc}")
        return None


def _key_pair_id() -> str:
    return (getattr(settings, "CLOUDFRONT_KEY_PAIR_ID", "") or "").strip()


def is_signing_enabled() -> bool:
    """서명에 필요한 키 두 가지가 모두 준비됐는지."""
    kid = _key_pair_id()
    return bool(kid) and "mock" not in kid and _load_private_key() is not None


def _cdn_domain() -> str:
    return (getattr(settings, "CLOUDFRONT_DOMAIN", "") or "").strip().rstrip("/")


def _is_cdn_url(url: str) -> bool:
    domain = _cdn_domain()
    return bool(domain) and isinstance(url, str) and url.startswith(domain)


@lru_cache(maxsize=1)
def _signer() -> Optional[CloudFrontSigner]:
    key = _load_private_key()
    if key is None:
        return None
    # CloudFront 서명은 SHA-1 고정이다 (AWS 규격).
    return CloudFrontSigner(_key_pair_id(), lambda msg: rsa.sign(msg, key, "SHA-1"))


def sign_url(url: str, expire_minutes: int = DEFAULT_EXPIRE_MINUTES) -> str:
    """CloudFront URL에 서명을 붙인다. 대상이 아니거나 키가 없으면 원본을 그대로 반환."""
    if not _is_cdn_url(url) or "Signature=" in url:
        return url
    signer = _signer()
    if signer is None:
        return url
    try:
        expire_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            minutes=expire_minutes
        )
        return signer.generate_presigned_url(url, date_less_than=expire_at)
    except Exception as exc:
        print(f"[CloudFront] 서명 실패 - 원본 URL로 진행합니다 ({url}): {exc}")
        return url


def sign_payload(obj: Any, expire_minutes: int = DEFAULT_EXPIRE_MINUTES) -> Any:
    """응답 본문을 재귀 순회하며 CloudFront URL 문자열마다 서명을 붙인다.

    엔드포인트마다 손대지 않고 한 곳에서 처리하기 위한 것이다 - 이미지 URL이 흘러나가는
    경로가 검수 상세·결함 좌표·보증서 등 여러 갈래라 개별 수정은 누락이 생긴다.
    """
    if isinstance(obj, str):
        return sign_url(obj, expire_minutes)
    if isinstance(obj, list):
        return [sign_payload(v, expire_minutes) for v in obj]
    if isinstance(obj, dict):
        return {k: sign_payload(v, expire_minutes) for k, v in obj.items()}
    return obj
