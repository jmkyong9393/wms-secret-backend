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
import logging
from functools import lru_cache
from typing import Any, Optional

import rsa
from botocore.signers import CloudFrontSigner

from app.core.config import settings

logger = logging.getLogger(__name__)

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
        logger.warning(f"[CloudFront] 개인키 로드 실패 - 서명 없이 진행합니다: {exc}")
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
def _fast_sign_key():
    """cryptography(OpenSSL) 키 객체. 순수 파이썬 rsa 서명은 1건당 30~100ms라
    URL 수백 개짜리 응답에서 수십 초가 된다 - C 구현으로 서명한다 (~0.5ms/건)."""
    raw = _raw_private_key()
    if not raw or "mock" in raw:
        return None
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        return load_pem_private_key(raw.encode("utf-8"), password=None)
    except Exception as exc:
        logger.warning(f"[CloudFront] cryptography 키 로드 실패 - rsa 폴백: {exc}")
        return None


@lru_cache(maxsize=1)
def _signer() -> Optional[CloudFrontSigner]:
    key = _load_private_key()
    if key is None:
        return None
    fast = _fast_sign_key()
    if fast is not None:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        # CloudFront 서명은 SHA-1 고정이다 (AWS 규격).
        return CloudFrontSigner(
            _key_pair_id(),
            lambda msg: fast.sign(msg, padding.PKCS1v15(), hashes.SHA1()),
        )
    return CloudFrontSigner(_key_pair_id(), lambda msg: rsa.sign(msg, key, "SHA-1"))


@lru_cache(maxsize=8192)
def _sign_cached(url: str, expire_minutes: int, _bucket: int) -> str:
    signer = _signer()
    if signer is None:
        return url
    try:
        expire_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
            minutes=expire_minutes
        )
        return signer.generate_presigned_url(url, date_less_than=expire_at)
    except Exception as exc:
        logger.warning(f"[CloudFront] 서명 실패 - 원본 URL로 진행합니다 ({url}): {exc}")
        return url


def sign_url(url: str, expire_minutes: int = DEFAULT_EXPIRE_MINUTES) -> str:
    """CloudFront URL에 서명을 붙인다. 대상이 아니거나 키가 없으면 원본을 그대로 반환.

    같은 URL은 30분 버킷 단위로 캐시해 재서명을 생략한다 - 유효기간이 60분이라
    캐시에서 꺼낸 URL도 항상 30분 이상 남는다.
    """
    if not _is_cdn_url(url) or "Signature=" in url:
        return url
    bucket = int(datetime.datetime.now(datetime.UTC).timestamp() // 1800)
    return _sign_cached(url, expire_minutes, bucket)


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
