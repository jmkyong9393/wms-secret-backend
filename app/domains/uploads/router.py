import logging
import os
import re
import uuid
from typing import Dict, List, Set

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status

from app.core.aws_auth_service import generate_signed_cookies
from app.core.config import settings
from app.core.security import get_current_user
from app.models.wms import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/uploads", tags=["uploads"])

# ==========================================
# 업로드 파일 검증 (시큐어코딩 "위험한 형식 파일 업로드" 대응)
# ==========================================
# 파일명·형식은 허용 목록(whitelist)으로만 통과시킨다 — 차단 목록은 새 확장자가
# 나올 때마다 뚫린다. 상세: 93_첨부파일_보안_업로드_아키텍처.
ALLOWED_UPLOAD_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
ALLOWED_UPLOAD_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic"}

# 게시판 첨부 전용 화이트리스트 — 검수 사진(이미지 전용)과 별도 관리.
# 실행 가능한 형식(.html/.svg/.js 등)은 제외, 사무용 문서만 추가 허용.
BOARD_ALLOWED_EXTENSIONS = ALLOWED_UPLOAD_EXTENSIONS | {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".hwp",
    ".txt",
}

# S3 키에 그대로 넣어도 안전한 문자만 남긴다 (한글/공백/특수문자 → '_')
_SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]")


def sanitize_upload_filename(
    file_name: str, allowed_extensions: Set[str] = ALLOWED_UPLOAD_EXTENSIONS
) -> str:
    """
    업로드 파일명을 S3 키로 쓰기 안전한 형태로 정규화한다.

    - 경로 구분자를 제거해 디렉터리 이동(`../`, `/etc/passwd`)을 차단한다.
    - 허용 문자 외에는 언더스코어로 치환한다.
    - 확장자는 소문자로 통일한다.
    - `allowed_extensions`를 지정하지 않으면 도서 검수 사진 화이트리스트(이미지 전용)를 쓴다.
    """
    # 경로 성분을 통째로 버리고 마지막 이름만 취한다 (백슬래시 경로도 함께 처리)
    base = os.path.basename(file_name.replace("\\", "/")).strip()
    base = base.lstrip(".")  # 숨김 파일(.htaccess 등) 형태 차단
    if not base:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="파일명이 비어 있습니다."
        )

    stem, ext = os.path.splitext(base)
    ext = ext.lower()
    if ext not in allowed_extensions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"허용되지 않는 파일 형식입니다. 허용 확장자: {', '.join(sorted(allowed_extensions))}",
        )

    safe_stem = _SAFE_NAME_PATTERN.sub("_", stem)[:80] or "upload"
    return f"{safe_stem}{ext}"


def validate_upload_mime(file_type: str) -> str:
    """선언된 Content-Type이 허용 목록에 있는지 확인한다."""
    normalized = (file_type or "").split(";")[0].strip().lower()
    if normalized not in ALLOWED_UPLOAD_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"허용되지 않는 Content-Type입니다. 허용 형식: {', '.join(sorted(ALLOWED_UPLOAD_MIME_TYPES))}",
        )
    return normalized


@router.get("/authorize")
def authorize_cloudfront_upload(
    response: Response,
    file_name: str,
    category: str = Query(
        "image",
        pattern="^(image|board)$",
        description="화이트리스트 선택: image=도서 검수 사진(기본), board=게시판 첨부(이미지+문서)",
    ),
    current_user: User = Depends(get_current_user),
):
    """
    S3 다이렉트 업로드를 위한 CloudFront Signed Cookie 발급

    [수정 이력 - 2026-08-06] 종전에는 인증 없이 호출할 수 있었다. 확장자를 아무리 검증해도,
    업로드 자격 자체를 누구에게나 발급하면 외부인이 우리 스토리지에 파일을 쌓을 수 있다.
    업로드는 로그인한 작업자만 수행하는 동작이므로 인증을 필수로 건다.

    [수정 이력 - 2026-08-08] `category=board`는 게시판 첨부용 확장 화이트리스트
    (`BOARD_ALLOWED_EXTENSIONS`)를 적용한다 - 도서 검수 사진 업로드(기본값)의
    화이트리스트는 그대로 이미지 전용으로 남긴다.
    """
    # 서명 쿠키는 uploads/* 전체에 대한 쓰기 권한이므로, 요청 파일명도 함께 검증해
    # 허용 형식이 아닌 업로드 시도에는 자격을 내주지 않는다.
    allowed_extensions = (
        BOARD_ALLOWED_EXTENSIONS if category == "board" else ALLOWED_UPLOAD_EXTENSIONS
    )
    sanitize_upload_filename(file_name, allowed_extensions)
    # 임시 URL (실제로는 CDN 주소 매핑)
    resource_url = "https://cdn.wms-ai.com/uploads/*"

    try:
        cookies = generate_signed_cookies(resource_url=resource_url, expire_minutes=15)
        # Set cookies on the response
        for cookie_name, cookie_value in cookies.items():
            response.set_cookie(
                key=cookie_name,
                value=cookie_value,
                httponly=True,
                secure=True,  # HTTPS 환경 필수
                samesite="none",
                domain="wms-ai.com",  # 실제 환경의 도메인
            )

        return {"message": "CloudFront Signed Cookies have been set successfully."}
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to generate signed cookies: {e!s}"
        )


@router.get("/presigned-url")
def get_presigned_url(
    file_name: str,
    file_type: str = "image/jpeg",
    current_user: User = Depends(get_current_user),
):
    """
    S3 다이렉트 업로드를 위한 Pre-signed URL 발급

    [수정 이력 - 2026-08-06] authorize와 동일한 이유로 인증을 필수화했다.
    """
    # 검증은 mock/real 분기보다 앞에 둔다 - 로컬 개발 경로만 검증을 건너뛰면
    # "로컬에서는 되던 업로드가 운영에서 막히는" 불일치가 생긴다.
    safe_name = sanitize_upload_filename(file_name)
    safe_type = validate_upload_mime(file_type)

    if not settings.AWS_ACCESS_KEY_ID or not settings.AWS_SECRET_ACCESS_KEY:
        # S3 설정이 없을 경우 임시 Mock URL 반환 (로컬 개발용) - 실제 업로드로 착각하지 않도록 명시적으로 로그
        logger.warning(
            f"[MOCK MODE] AWS 자격증명 미설정 - {safe_name}에 대해 가짜 업로드 URL을 반환합니다 (실제 S3 업로드 아님)."
        )
        unique_id = uuid.uuid4().hex[:8]
        return {
            "upload_url": f"http://localhost:8000/mock-upload/{unique_id}_{safe_name}",
            "file_url": f"https://mock-s3.com/{unique_id}_{safe_name}",
        }

    s3_client = boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_REGION,
    )

    unique_file_name = f"{uuid.uuid4()}_{safe_name}"

    try:
        response = s3_client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": settings.AWS_S3_BUCKET,
                "Key": unique_file_name,
                # presigned URL에 서명된 ContentType과 다른 타입으로는 업로드가 거부되므로,
                # 여기서 허용 목록 값을 박아두면 클라이언트가 형식을 바꿔치기할 수 없다.
                "ContentType": safe_type,
            },
            ExpiresIn=3600,  # 1 hour
        )
    except ClientError as e:
        raise HTTPException(status_code=500, detail="Failed to generate presigned URL")

    return {
        "upload_url": response,
        "file_url": f"https://{settings.AWS_S3_BUCKET}.s3.{settings.AWS_REGION}.amazonaws.com/{unique_file_name}",
    }


# ==========================================
# 게시판 첨부 — Presigned POST + 격리(Quarantine) 파이프라인
# ==========================================
#
# [도입 배경 - 2026-08-18]
# 종전 게시판 첨부는 CloudFront Signed Cookie로 `cdn.wms-ai.com`에 PUT 하는 구조였다.
# 그 도메인은 **DNS에 존재하지 않는 플레이스홀더**여서 업로드가 100% 실패했다.
# 더 근본적으로 CloudFront 서명 쿠키는 *여러 파일을 열람*할 때 쓰는 물건이지
# 업로드용이 아니다 — 용도를 뒤집어 쓴 설계였다.
#
# 대체 구조:
#   ① presign  : 서버가 S3 Presigned POST를 발급한다. 정책(policy)에 크기 상한과
#                Content-Type을 **서버가 박아 서명**하므로 클라이언트가 고칠 수 없다.
#                업로드 지점은 격리 구역(quarantine/)이다.
#   ② verify   : 업로드 완료 후 서버가 격리본을 내려받아 실제 바이트를 검사하고
#                (file_security), 통과분만 attachments/로 옮긴다. 실패분은 즉시 삭제한다.
#
# 파일 바이트는 API 서버를 거치지 않고 브라우저→S3로 직행하므로(①) 대역폭 부담이 없고,
# 검사는 격리 구역에서만 수행되므로(②) 미검증 파일이 노출되는 창이 존재하지 않는다.

ATTACHMENT_MAX_BYTES = 5 * 1024 * 1024  # 5MB — 프론트가 아니라 S3가 강제한다
QUARANTINE_PREFIX = "quarantine/"
CLEAN_PREFIX = "attachments/"
# S3 키 길이 상한. 파일명은 sanitize에서 80자로 잘리지만, 키 전체를 한 번 더 막아
# 비정상적으로 긴 입력이 저장 경로에 들어오지 않게 한다.
ATTACHMENT_MAX_KEY_LEN = 512


def _owner_prefix(prefix: str, user: User) -> str:
    """격리·정상 구역을 소유자 단위로 분할한다.

    키에 소유자를 넣으면 인가 판정이 접두사 비교로 끝난다 — uuid 난수성은
    '추측이 어렵다'일 뿐 '권한이 없다'가 아니다.
    """
    return f"{prefix}{user.id}/"


def _assert_safe_key(key: str, expected_prefix: str) -> None:
    """오브젝트 키가 지정한 구역 안에 있는지 확인한다 (경로 이탈·제어문자 차단)."""
    if not key or len(key) > ATTACHMENT_MAX_KEY_LEN:
        raise HTTPException(status_code=400, detail="잘못된 오브젝트 키입니다.")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in key):
        raise HTTPException(status_code=400, detail="잘못된 오브젝트 키입니다.")
    # "quarantine/../attachments/x" 같은 상위 이동과 절대경로를 함께 막는다.
    if ".." in key or key.startswith("/") or "//" in key:
        raise HTTPException(status_code=400, detail="잘못된 오브젝트 키입니다.")
    if not key.startswith(expected_prefix):
        raise HTTPException(status_code=403, detail="이 파일에 대한 권한이 없습니다.")


# 게시판 첨부에 허용할 Content-Type (BOARD_ALLOWED_EXTENSIONS와 짝을 이룬다)
BOARD_ALLOWED_MIME_TYPES = ALLOWED_UPLOAD_MIME_TYPES | {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/x-hwp",
    "application/haansofthwp",
    "application/vnd.hancom.hwp",
    "text/plain",
    "application/octet-stream",  # 브라우저가 .hwp 등의 타입을 못 잡는 경우
}


def _attachment_s3_client():
    """첨부 버킷 전용 S3 클라이언트.

    endpoint_url과 서명 버전을 명시한다 — region_name만 주면 boto3가 글로벌
    엔드포인트로 URL을 만들어 서명 리전 불일치가 난다 (92번 §5-3b).
    """
    if not settings.AWS_ACCESS_KEY_ID or not settings.AWS_SECRET_ACCESS_KEY:
        raise HTTPException(
            status_code=503, detail="스토리지 자격증명이 설정되지 않았습니다."
        )
    return boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_REGION,
        endpoint_url=f"https://s3.{settings.AWS_REGION}.amazonaws.com",
        config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )


@router.post("/attachment/presign")
def presign_attachment_upload(
    file_name: str = Query(..., description="원본 파일명"),
    file_type: str = Query(
        "application/octet-stream", description="브라우저가 보고한 MIME"
    ),
    current_user: User = Depends(get_current_user),
):
    """게시판 첨부 업로드용 Presigned POST 발급 (격리 구역 대상).

    크기 상한은 프론트 검사가 아니라 S3 정책 서명으로 강제한다.
    """
    from app.core.file_security import FileSecurityError, normalize_filename

    try:
        clean_name = normalize_filename(file_name)
    except FileSecurityError as e:
        raise HTTPException(status_code=400, detail=e.reason)

    safe_name = sanitize_upload_filename(clean_name, BOARD_ALLOWED_EXTENSIONS)

    declared = (file_type or "").split(";")[0].strip().lower()
    if declared and declared not in BOARD_ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400, detail=f"허용되지 않는 Content-Type입니다: {declared}"
        )

    owner_prefix = _owner_prefix(QUARANTINE_PREFIX, current_user)
    object_key = f"{owner_prefix}{uuid.uuid4()}_{safe_name}"
    s3 = _attachment_s3_client()
    try:
        presigned = s3.generate_presigned_post(
            Bucket=settings.AWS_S3_ATTACHMENT_BUCKET,
            Key=object_key,
            Fields={"Content-Type": declared or "application/octet-stream"},
            Conditions=[
                # S3가 강제하는 제약. 위반 시 업로드 자체가 거부된다.
                ["content-length-range", 1, ATTACHMENT_MAX_BYTES],
                {"Content-Type": declared or "application/octet-stream"},
                # 자기 격리 구역 밖으로는 쓸 수 없다 — 남의 구역에 파일을 심어 두는 경로를 막는다.
                ["starts-with", "$key", owner_prefix],
            ],
            ExpiresIn=300,  # 5분 — 서명 유출 시 악용 창을 좁힌다
        )
    except ClientError:
        raise HTTPException(status_code=500, detail="업로드 자격 발급에 실패했습니다.")

    return {
        "upload_url": presigned["url"],
        "fields": presigned["fields"],
        "object_key": object_key,
        "max_bytes": ATTACHMENT_MAX_BYTES,
    }


@router.post("/attachment/verify")
def verify_attachment_upload(
    object_key: str = Query(..., description="presign이 발급한 격리 구역 키"),
    current_user: User = Depends(get_current_user),
):
    """격리본을 실제로 검사하고 통과분만 정상 구역으로 옮긴다.

    검사는 선언된 형식이 아니라 **실제 바이트**를 본다 (app/core/file_security.py).
    실패하면 격리본을 즉시 삭제하므로 미검증 파일이 스토리지에 남지 않는다.
    """
    from app.core.file_security import FileSecurityError, scan_attachment

    # 자기 격리 구역의 키만 승격시킬 수 있다. uuid를 알아냈더라도 소유자가 다르면 403이다.
    _assert_safe_key(object_key, _owner_prefix(QUARANTINE_PREFIX, current_user))

    s3 = _attachment_s3_client()
    bucket = settings.AWS_S3_ATTACHMENT_BUCKET

    try:
        head = s3.head_object(Bucket=bucket, Key=object_key)
    except ClientError:
        raise HTTPException(status_code=404, detail="업로드된 파일을 찾을 수 없습니다.")

    size = head.get("ContentLength", 0)
    if size > ATTACHMENT_MAX_BYTES:
        s3.delete_object(Bucket=bucket, Key=object_key)
        raise HTTPException(
            status_code=400, detail="파일 크기가 허용치를 초과했습니다."
        )

    body = s3.get_object(Bucket=bucket, Key=object_key)["Body"].read()
    ext = os.path.splitext(object_key)[1].lower()

    try:
        report = scan_attachment(body, ext, declared_size=size)
    except FileSecurityError as e:
        # 검사 실패분은 격리 구역에서 즉시 제거한다 — 재시도·열람 경로를 남기지 않는다
        s3.delete_object(Bucket=bucket, Key=object_key)
        raise HTTPException(
            status_code=400, detail=e.reason, headers={"X-Scan-Code": e.code}
        )

    clean_key = object_key.replace(QUARANTINE_PREFIX, CLEAN_PREFIX, 1)
    s3.copy_object(
        Bucket=bucket,
        Key=clean_key,
        CopySource={"Bucket": bucket, "Key": object_key},
        MetadataDirective="REPLACE",
        ContentType=head.get("ContentType", "application/octet-stream"),
        # 저장형 XSS 차단: 브라우저가 렌더링하지 않고 반드시 내려받게 한다
        ContentDisposition="attachment",
    )
    s3.delete_object(Bucket=bucket, Key=object_key)

    return {"status": "CLEAN", "object_key": clean_key, "scan": report}


# 열람 URL 만료. 짧을수록 유출 시 악용 창이 좁지만, 게시글을 오래 열어 두면 이미지가 깨진다.
# 프론트는 이보다 짧은 주기로 캐시를 무효화한다(8분).
ATTACHMENT_DOWNLOAD_TTL = 600
ATTACHMENT_DOWNLOAD_MAX_KEYS = 20


@router.post("/attachment/download-urls")
def issue_attachment_download_urls(
    object_keys: List[str] = Body(
        ..., embed=True, description="attachments/ 하위 키 목록"
    ),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Dict[str, str]]:
    """첨부 열람용 Presigned GET URL을 **일괄** 발급한다.

    전용 버킷은 퍼블릭 접근이 차단돼 있어 객체 주소를 그대로 붙여서는 열 수 없다.
    발급은 서명 계산뿐이라 S3 왕복이 없다 — 그래서 게시글 첨부 5개를 한 번의 요청으로
    처리한다(키마다 따로 부르면 왕복만 5배가 된다).
    """
    if not object_keys:
        return {"urls": {}}
    if len(object_keys) > ATTACHMENT_DOWNLOAD_MAX_KEYS:
        raise HTTPException(
            status_code=400, detail="한 번에 요청할 수 있는 첨부 수를 초과했습니다."
        )

    s3 = _attachment_s3_client()
    urls: Dict[str, str] = {}
    for key in object_keys:
        # 검사를 통과한 정상 구역만 열어 준다. 격리 구역 키를 넘겨 미검증 파일을 열람하는
        # 경로를 만들지 않는다. 형식이 어긋난 키는 오류 대신 조용히 건너뛴다 —
        # 거부 사유를 응답으로 되돌려주면 키 존재 여부를 캐는 수단이 된다.
        try:
            _assert_safe_key(key, CLEAN_PREFIX)
        except HTTPException:
            continue
        urls[key] = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.AWS_S3_ATTACHMENT_BUCKET, "Key": key},
            ExpiresIn=ATTACHMENT_DOWNLOAD_TTL,
        )
    return {"urls": urls}
