"""업로드 라우터 - 인증·HTTP 응답 조작(Signed Cookie 세팅)과 배선 전용.

화이트리스트·presign 발급·격리 검사 로직은 service.py (2026-09-01 계층 정리).
"""

from typing import Dict, List

from fastapi import APIRouter, Body, Depends, Query, Response

from app.core.security import get_current_user
from app.domains.uploads import service
from app.models.wms import User

router = APIRouter(prefix="/uploads", tags=["uploads"])


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
    cookies = service.authorize_cloudfront_upload(file_name, category)
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
    return service.get_presigned_url(file_name, file_type)


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
    return service.presign_attachment_upload(current_user, file_name, file_type)


@router.post("/attachment/verify")
def verify_attachment_upload(
    object_key: str = Query(..., description="presign이 발급한 격리 구역 키"),
    current_user: User = Depends(get_current_user),
):
    """격리본을 실제로 검사하고 통과분만 정상 구역으로 옮긴다.

    검사는 선언된 형식이 아니라 **실제 바이트**를 본다 (app/core/file_security.py).
    실패하면 격리본을 즉시 삭제하므로 미검증 파일이 스토리지에 남지 않는다.
    """
    return service.verify_attachment_upload(current_user, object_key)


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
    return service.issue_attachment_download_urls(object_keys)
