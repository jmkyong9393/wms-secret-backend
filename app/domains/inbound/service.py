import datetime
import uuid
import base64
import rsa
from botocore.signers import CloudFrontSigner
from app.core.config import settings

def rsa_signer(message: bytes) -> bytes:
    """
    CloudFront Signed Cookie 생성을 위한 RSA 서명 함수입니다.
    """
    try:
        if "mock" in settings.CLOUDFRONT_PRIVATE_KEY:
            # 로컬 개발 및 테스트를 위한 Mock 서명 반환
            return b"mock_signature_bytes_for_local_dev"
            
        private_key = rsa.PrivateKey.load_pkcs1(settings.CLOUDFRONT_PRIVATE_KEY.encode('utf-8'))
        return rsa.sign(message, private_key, 'SHA-1')
    except Exception as e:
        print(f"[Warning] Failed to sign with RSA key, using mock: {e}")
        return b"mock_signature_bytes_for_local_dev"

def _url_b64encode(data: bytes) -> str:
    """
    CloudFront 정책 및 서명 문자열 포맷팅 (Base64 URL Safe)
    """
    return base64.b64encode(data).replace(b'+', b'-').replace(b'=', b'_').replace(b'/', b'~').decode('utf-8')

def generate_signed_cookie(filename: str) -> dict:
    """
    S3 다이렉트 업로드를 위한 CloudFront Signed Cookie 세트를 생성합니다.
    """
    # 고유한 Object Key 생성 (예: inbound/20260716/uuid_filename.jpg)
    today_str = datetime.datetime.utcnow().strftime("%Y%m%d")
    unique_id = str(uuid.uuid4())[:8]
    object_key = f"inbound/{today_str}/{unique_id}_{filename}"
    
    url = f"{settings.CLOUDFRONT_DOMAIN}/{object_key}"
    key_pair_id = settings.CLOUDFRONT_KEY_PAIR_ID
    
    cloudfront_signer = CloudFrontSigner(key_pair_id, rsa_signer)
    
    # 업로드 유효 시간: 1시간
    expire_date = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
    
    policy = cloudfront_signer.build_policy(url, expire_date).encode('utf-8')
    signature = cloudfront_signer.signature(policy)
    
    return {
        "url": url,
        "object_key": object_key,
        "cookies": {
            "CloudFront-Policy": _url_b64encode(policy),
            "CloudFront-Signature": _url_b64encode(signature),
            "CloudFront-Key-Pair-Id": key_pair_id
        }
    }
