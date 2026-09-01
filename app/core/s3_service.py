import base64
import logging
import os
from typing import Optional

import boto3

logger = logging.getLogger(__name__)

try:
    from app.core.config import settings
except Exception:
    settings = None


def _cfg(name: str, default: Optional[str] = None) -> Optional[str]:
    """settings(.env) -> 환경변수 -> 기본값 순으로 설정값을 읽는다. 키 리터럴 하드코딩 금지."""
    value = getattr(settings, name, None) if settings else None
    return value or os.environ.get(name) or default


def _public_base_url() -> Optional[str]:
    """
    브라우저가 실제로 열 수 있는 이미지 베이스 URL을 반환한다.

    [중요] 이 버킷은 Block Public Access 4종(BlockPublicAcls / IgnorePublicAcls /
    BlockPublicPolicy / RestrictPublicBuckets)이 전부 ON이라 S3 오브젝트 직링크
    (https://<bucket>.s3.<region>.amazonaws.com/<key>)는 항상 403을 반환한다.
    실측 결과 CloudFront 배포(OAC)만 200을 주므로, DB에 적재하는 URL은 반드시
    CloudFront 도메인 기준이어야 한다. S3 URL을 저장하면 프론트 <img>가 100% 깨진다.
    """
    domain = _cfg("CLOUDFRONT_DOMAIN")
    if not domain:
        return None
    domain = domain.strip().rstrip("/")
    if not domain.startswith("http://") and not domain.startswith("https://"):
        domain = f"https://{domain}"
    return domain


def build_public_url(s3_key: str) -> Optional[str]:
    """S3 오브젝트 키를 브라우저에서 열 수 있는 CloudFront URL로 변환한다."""
    base = _public_base_url()
    if not base:
        return None
    return f"{base}/{s3_key.lstrip('/')}"


def upload_bytes_to_s3(
    file_bytes: bytes, s3_key: str, content_type: str = "image/jpeg"
) -> Optional[str]:
    """
    바이트 배열을 S3 버킷에 업로드하고 CloudFront 공개 URL을 반환한다.
    자격증명이 없거나 업로드에 실패하면 None을 반환하여 호출부가 로컬 경로로 폴백하게 한다.
    """
    aws_access_key = _cfg("AWS_ACCESS_KEY_ID")
    aws_secret_key = _cfg("AWS_SECRET_ACCESS_KEY")

    if not aws_access_key or not aws_secret_key:
        logger.warning(
            "[S3] AWS 자격증명이 없어 업로드를 건너뜁니다 (로컬 경로로 폴백)."
        )
        return None

    aws_region = _cfg("AWS_REGION", "ap-northeast-2")
    bucket_name = _cfg("AWS_S3_BUCKET") or _cfg("S3_BUCKET_NAME")

    if not bucket_name:
        logger.warning(
            "[S3] 버킷명이 설정되지 않아 업로드를 건너뜁니다 (로컬 경로로 폴백)."
        )
        return None

    try:
        s3_client = boto3.client(
            "s3",
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            region_name=aws_region,
        )
        # ACL은 지정하지 않는다 - 버킷이 BlockPublicAcls=True라 public-read ACL은 거부된다.
        # 공개 접근은 CloudFront OAC + 버킷 정책이 담당한다.
        s3_client.put_object(
            Bucket=bucket_name,
            Key=s3_key,
            Body=file_bytes,
            ContentType=content_type,
        )
    except Exception as e:
        logger.warning(f"[S3] 업로드 실패 ({s3_key}): {e} - 로컬 경로로 폴백합니다.")
        return None

    public_url = build_public_url(s3_key)
    if not public_url:
        logger.warning(
            f"[S3] 업로드는 성공했으나 CLOUDFRONT_DOMAIN이 없어 공개 URL을 만들 수 없습니다 ({s3_key})."
        )
        return None

    logger.info(f"[S3] 업로드 완료 -> {public_url}")
    return public_url


def upload_base64_to_s3(b64_str: str, s3_key: str) -> Optional[str]:
    """Base64 문자열(data URI 접두사 허용)을 S3에 업로드하고 CloudFront URL을 반환한다."""
    if b64_str.startswith("data:image"):
        b64_str = b64_str.split(",", 1)[1]
    try:
        file_bytes = base64.b64decode(b64_str)
    except Exception as e:
        logger.warning(f"[S3] Base64 디코딩 실패 ({s3_key}): {e}")
        return None
    return upload_bytes_to_s3(file_bytes, s3_key)
