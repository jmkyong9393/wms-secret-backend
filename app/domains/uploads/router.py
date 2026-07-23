from fastapi import APIRouter, Depends, HTTPException, status, Response
import boto3
from botocore.exceptions import ClientError
from app.core.config import settings
import uuid
from app.core.aws_auth_service import generate_signed_cookies

router = APIRouter(prefix="/uploads", tags=["uploads"])

@router.get("/authorize")
def authorize_cloudfront_upload(response: Response, file_name: str):
    """
    S3 다이렉트 업로드를 위한 CloudFront Signed Cookie 발급
    """
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
def get_presigned_url(file_name: str, file_type: str = "image/jpeg"):
    """
    S3 다이렉트 업로드를 위한 Pre-signed URL 발급
    """
    if not settings.AWS_ACCESS_KEY_ID or not settings.AWS_SECRET_ACCESS_KEY:
        # S3 설정이 없을 경우 임시 Mock URL 반환 (로컬 개발용)
        unique_id = uuid.uuid4().hex[:8]
        return {
            "upload_url": f"http://localhost:8000/mock-upload/{unique_id}_{file_name}",
            "file_url": f"https://mock-s3.com/{unique_id}_{file_name}"
        }

    s3_client = boto3.client(
        's3',
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_REGION
    )

    unique_file_name = f"{uuid.uuid4()}_{file_name}"

    try:
        response = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': settings.AWS_BUCKET_NAME,
                'Key': unique_file_name,
                'ContentType': file_type
            },
            ExpiresIn=3600 # 1 hour
        )
    except ClientError as e:
        raise HTTPException(status_code=500, detail="Failed to generate presigned URL")

    return {
        "upload_url": response,
        "file_url": f"https://{settings.AWS_BUCKET_NAME}.s3.{settings.AWS_REGION}.amazonaws.com/{unique_file_name}"
    }
