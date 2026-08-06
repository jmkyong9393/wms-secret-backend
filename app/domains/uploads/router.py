from fastapi import APIRouter, Depends, HTTPException, status, Response
import boto3
from botocore.exceptions import ClientError
from app.core.config import settings
import os
import re
import uuid
from app.core.aws_auth_service import generate_signed_cookies
from app.core.security import get_current_user
from app.models.wms import User

router = APIRouter(prefix="/uploads", tags=["uploads"])

# ==========================================
# 업로드 파일 검증 (시큐어코딩 "위험한 형식 파일 업로드" 대응)
# ==========================================
#
# [도입 배경 - 2026-08-06]
# 종전에는 클라이언트가 보낸 file_name / file_type을 **아무 검증 없이** 그대로 S3 키와
# Content-Type에 넣어 presigned URL을 발급했다. 그 결과
#   1) `.html`, `.svg`, `.js` 같은 실행 가능한 형식을 올려 CDN 도메인에서 스크립트를
#      실행시키는 저장형 XSS 경로가 열려 있었고,
#   2) file_name에 `../`나 절대경로를 넣어 의도하지 않은 키 위치에 쓰는 경로 조작이 가능했다.
#
# 이 서비스가 업로드하는 것은 **도서 검수 사진뿐**이므로, 허용 목록(whitelist) 방식으로
# 이미지 포맷만 통과시킨다. 차단 목록(blacklist)은 새 확장자가 나올 때마다 뚫리므로 쓰지 않는다.
ALLOWED_UPLOAD_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
ALLOWED_UPLOAD_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic"}

# S3 키에 그대로 넣어도 안전한 문자만 남긴다 (한글/공백/특수문자 → '_')
_SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]")


def sanitize_upload_filename(file_name: str) -> str:
    """
    업로드 파일명을 S3 키로 쓰기 안전한 형태로 정규화한다.

    - 경로 구분자를 제거해 디렉터리 이동(`../`, `/etc/passwd`)을 차단한다.
    - 허용 문자 외에는 언더스코어로 치환한다.
    - 확장자는 소문자로 통일한다.
    """
    # 경로 성분을 통째로 버리고 마지막 이름만 취한다 (백슬래시 경로도 함께 처리)
    base = os.path.basename(file_name.replace("\\", "/")).strip()
    base = base.lstrip(".")  # 숨김 파일(.htaccess 등) 형태 차단
    if not base:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="파일명이 비어 있습니다.")

    stem, ext = os.path.splitext(base)
    ext = ext.lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"허용되지 않는 파일 형식입니다. 허용 확장자: {', '.join(sorted(ALLOWED_UPLOAD_EXTENSIONS))}",
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
    current_user: User = Depends(get_current_user),
):
    """
    S3 다이렉트 업로드를 위한 CloudFront Signed Cookie 발급

    [수정 이력 - 2026-08-06] 종전에는 인증 없이 호출할 수 있었다. 확장자를 아무리 검증해도,
    업로드 자격 자체를 누구에게나 발급하면 외부인이 우리 스토리지에 파일을 쌓을 수 있다.
    업로드는 로그인한 작업자만 수행하는 동작이므로 인증을 필수로 건다.
    """
    # 서명 쿠키는 uploads/* 전체에 대한 쓰기 권한이므로, 요청 파일명도 함께 검증해
    # 허용 형식이 아닌 업로드 시도에는 자격을 내주지 않는다.
    sanitize_upload_filename(file_name)
    # 임시 URL (실제로는 CDN 주소 매핑)
    resource_url = f"https://cdn.wms-ai.com/uploads/*"
    
    try:
        cookies = generate_signed_cookies(resource_url=resource_url, expire_minutes=15)
        # Set cookies on the response
        for cookie_name, cookie_value in cookies.items():
            response.set_cookie(
                key=cookie_name,
                value=cookie_value,
                httponly=True,
                secure=True, # HTTPS 환경 필수
                samesite="none",
                domain="wms-ai.com" # 실제 환경의 도메인
            )
        
        return {"message": "CloudFront Signed Cookies have been set successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate signed cookies: {str(e)}")

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
        print(f"[MOCK MODE] AWS 자격증명 미설정 - {safe_name}에 대해 가짜 업로드 URL을 반환합니다 (실제 S3 업로드 아님).")
        unique_id = uuid.uuid4().hex[:8]
        return {
            "upload_url": f"http://localhost:8000/mock-upload/{unique_id}_{safe_name}",
            "file_url": f"https://mock-s3.com/{unique_id}_{safe_name}"
        }

    s3_client = boto3.client(
        's3',
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_REGION
    )

    unique_file_name = f"{uuid.uuid4()}_{safe_name}"

    try:
        response = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': settings.AWS_S3_BUCKET,
                'Key': unique_file_name,
                # presigned URL에 서명된 ContentType과 다른 타입으로는 업로드가 거부되므로,
                # 여기서 허용 목록 값을 박아두면 클라이언트가 형식을 바꿔치기할 수 없다.
                'ContentType': safe_type
            },
            ExpiresIn=3600 # 1 hour
        )
    except ClientError as e:
        raise HTTPException(status_code=500, detail="Failed to generate presigned URL")

    return {
        "upload_url": response,
        "file_url": f"https://{settings.AWS_S3_BUCKET}.s3.{settings.AWS_REGION}.amazonaws.com/{unique_file_name}"
    }
