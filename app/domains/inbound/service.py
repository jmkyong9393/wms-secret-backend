import datetime
import uuid
import base64
import rsa
import httpx
from botocore.signers import CloudFrontSigner
from app.core.config import settings
from app.models.wms import now_kst


def _positive_float(value) -> float | None:
    try:
        parsed = float(value)
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None


async def lookup_book_by_isbn(isbn: str) -> dict:
    """
    알라딘 TTB Open API(ItemLookUp)로 ISBN 도서 메타데이터를 조회한다.
    입고 화면(ISBN 바코드 스캔)에서 표지/제목/저자 등 기본 정보뿐 아니라,
    OptResult=packing 옵션으로 택배 송장 산정에 필요한 실측 가로/세로/두께/무게/페이지 수까지
    한 번의 호출로 함께 가져온다 (fetch_aladin_real_packing_spec와 동일한 packing 필드 매핑).
    """
    # 알라딘 서버가 http 요청을 https로 301 리다이렉트하는데 httpx는 기본적으로 리다이렉트를
    # 따라가지 않아 조용히 실패한다 - https를 직접 호출해 리다이렉트 자체를 피한다.
    url = "https://www.aladin.co.kr/ttb/api/ItemLookUp.aspx"
    params = {
        "ttbkey": settings.ALADIN_TTB_KEY,
        "itemIdType": "ISBN13",
        "ItemId": isbn,
        "output": "js",
        "Version": "20131101",
        "Cover": "Big",
        "OptResult": "packing",
    }

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            res = await client.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"})
            res.raise_for_status()
            data = res.json()
    except Exception as e:
        print(f"[Aladin API] 도서 정보 조회 실패 (isbn={isbn}): {e}")
        return {}

    items = data.get("item") or []
    if not items:
        return {}

    item = items[0]
    sub_info = item.get("subInfo", {}) or {}
    packing = sub_info.get("packing", {}) or {}
    item_page = sub_info.get("itemPage") or item.get("itemPage")

    result = {
        "isbn": item.get("isbn13") or item.get("isbn") or isbn,
        "title": item.get("title", ""),
        "author": item.get("author", ""),
        "publisher": item.get("publisher", ""),
        "pubDate": item.get("pubDate", ""),
        "price": item.get("priceStandard") or item.get("priceSales") or 0,
        "description": item.get("description", ""),
        "imageUrl": item.get("cover", ""),
        "categoryName": item.get("categoryName", ""),
    }

    width_mm = _positive_float(packing.get("sizeWidth"))
    depth_mm = _positive_float(packing.get("sizeHeight"))
    thickness_mm = _positive_float(packing.get("sizeDepth"))
    weight_g = _positive_float(packing.get("weight"))

    if width_mm is not None:
        result["width_mm"] = width_mm
    if depth_mm is not None:
        result["depth_mm"] = depth_mm
    if thickness_mm is not None:
        result["thickness_mm"] = thickness_mm
    if weight_g is not None:
        result["weight_g"] = weight_g
    if item_page and str(item_page).isdigit() and int(item_page) > 0:
        result["page_count"] = int(item_page)

    return result

def rsa_signer(message: bytes) -> bytes:
    """
    CloudFront Signed Cookie 생성을 위한 RSA 서명 함수입니다.
    """
    try:
        if "mock" in settings.CLOUDFRONT_PRIVATE_KEY:
            # 로컬 개발 및 테스트를 위한 Mock 서명 반환 - 실제 서명으로 착각하지 않도록 명시적으로 로그
            print("[MOCK MODE] CLOUDFRONT_PRIVATE_KEY 미설정 - 가짜 서명을 반환합니다 (실제 CloudFront 서명 아님).")
            return b"mock_signature_bytes_for_local_dev"

        private_key = rsa.PrivateKey.load_pkcs1(settings.CLOUDFRONT_PRIVATE_KEY.encode('utf-8'))
        return rsa.sign(message, private_key, 'SHA-1')
    except Exception as e:
        print(f"[MOCK MODE] RSA 서명 실패, mock으로 폴백: {e}")
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
    today_str = now_kst().strftime("%Y%m%d")
    unique_id = str(uuid.uuid4())[:8]
    object_key = f"inbound/{today_str}/{unique_id}_{filename}"
    
    url = f"{settings.CLOUDFRONT_DOMAIN}/{object_key}"
    key_pair_id = settings.CLOUDFRONT_KEY_PAIR_ID
    
    cloudfront_signer = CloudFrontSigner(key_pair_id, rsa_signer)
    
    # 업로드 유효 시간: 1시간
    expire_date = now_kst() + datetime.timedelta(hours=1)
    
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
