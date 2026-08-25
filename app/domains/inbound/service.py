import datetime
import uuid
import base64
import rsa
import httpx
from botocore.signers import CloudFrontSigner
from app.core.config import settings
from app.models.wms import now_kst
import logging

logger = logging.getLogger(__name__)


# 알라딘 조회 실패 시 Book.title에 넣는 자리표시자. 이 값이 든 행은 정본이 아니므로
# 재조회 대상으로 취급한다.
UNKNOWN_BOOK_TITLE = "미확인 도서"

# 조회 실패 사유 구분값. 호출부가 404(없는 책)와 503(조회 불가)을 나눠 응답하는 데 쓴다.
LOOKUP_NOT_FOUND = "NOT_FOUND"  # 알라딘 정상 응답, 해당 ISBN 없음
LOOKUP_UNAVAILABLE = "UNAVAILABLE"  # 타임아웃·네트워크·HTTP 오류로 조회 자체 실패

_ALADIN_TIMEOUT_SEC = 3.0
_ALADIN_ATTEMPTS = 2  # 최초 1회 + 순단 흡수용 재시도 1회


def _positive_float(value) -> float | None:
    try:
        parsed = float(value)
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None


async def lookup_book_by_isbn(isbn: str) -> dict:
    """알라딘 조회 결과만 반환하는 기존 시그니처 (실패 시 빈 dict). 실패 사유가 필요하면
    lookup_book_by_isbn_with_status()를 쓴다."""
    result, _ = await lookup_book_by_isbn_with_status(isbn)
    return result


async def lookup_book_by_isbn_with_status(isbn: str) -> tuple[dict, str | None]:
    """
    알라딘 TTB Open API(ItemLookUp)로 ISBN 도서 메타데이터를 조회한다.
    입고 화면(ISBN 바코드 스캔)에서 표지/제목/저자 등 기본 정보뿐 아니라,
    OptResult=packing 옵션으로 택배 송장 산정에 필요한 실측 가로/세로/두께/무게/페이지 수까지
    한 번의 호출로 함께 가져온다 (fetch_aladin_real_packing_spec와 동일한 packing 필드 매핑).

    반환: (메타데이터, 실패사유). 성공 시 실패사유는 None.

    타임아웃 3초는 알라딘 실측 지연(중앙값 87ms, 최대 235ms) 대비 10배 이상 여유라
    상향해도 얻는 것이 없다. 근거는 개인개발가이드 43번 문서 참조.
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

    data = None
    last_error = None
    for attempt in range(1, _ALADIN_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=_ALADIN_TIMEOUT_SEC) as client:
                res = await client.get(
                    url, params=params, headers={"User-Agent": "Mozilla/5.0"}
                )
                res.raise_for_status()
                data = res.json()
            break
        except Exception as e:
            last_error = e
            logger.warning(
                f"[Aladin API] 조회 실패 {attempt}/{_ALADIN_ATTEMPTS} (isbn={isbn}): {type(e).__name__}: {e}"
            )

    if data is None:
        return {}, LOOKUP_UNAVAILABLE

    items = data.get("item") or []
    if not items:
        logger.info(f"[Aladin API] 해당 ISBN 없음 (isbn={isbn})")
        return {}, LOOKUP_NOT_FOUND

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

    return result, None


def book_row_to_lookup_payload(book) -> dict:
    """DB의 Book 행을 /book-lookup 응답 형태(알라딘 조회 결과와 동일한 키)로 변환한다.
    프론트가 원장 응답과 알라딘 응답을 구분 없이 쓸 수 있게 키 이름을 맞춘다."""
    payload = {
        "isbn": book.isbn,
        "title": book.title,
        "author": book.author or "",
        "publisher": book.publisher or "",
        "pubDate": book.published_date or "",
        "price": book.base_price or 0,
        "description": book.description or "",
        "imageUrl": book.cover_image_url or "",
        "categoryName": book.category_type or "",
        "source": "LEDGER",  # 알라딘 응답과 구분하기 위한 출처 표기
    }
    for field in ("width_mm", "depth_mm", "thickness_mm", "weight_g", "page_count"):
        value = getattr(book, field, None)
        if value is not None:
            payload[field] = value
    return payload


def is_placeholder_book(book) -> bool:
    """조회 실패로 만들어진 자리표시자 Book 행인지 판정한다(알라딘 재조회 대상)."""
    return not book or not book.title or book.title.strip() == UNKNOWN_BOOK_TITLE


def rsa_signer(message: bytes) -> bytes:
    """
    CloudFront Signed Cookie 생성을 위한 RSA 서명 함수입니다.
    """
    try:
        if "mock" in settings.CLOUDFRONT_PRIVATE_KEY:
            # 로컬 개발 및 테스트를 위한 Mock 서명 반환 - 실제 서명으로 착각하지 않도록 명시적으로 로그
            logger.warning(
                "[MOCK MODE] CLOUDFRONT_PRIVATE_KEY 미설정 - 가짜 서명을 반환합니다 (실제 CloudFront 서명 아님)."
            )
            return b"mock_signature_bytes_for_local_dev"

        private_key = rsa.PrivateKey.load_pkcs1(
            settings.CLOUDFRONT_PRIVATE_KEY.encode("utf-8")
        )
        return rsa.sign(message, private_key, "SHA-1")
    except Exception as e:
        logger.warning(f"[MOCK MODE] RSA 서명 실패, mock으로 폴백: {e}")
        return b"mock_signature_bytes_for_local_dev"


def _url_b64encode(data: bytes) -> str:
    """
    CloudFront 정책 및 서명 문자열 포맷팅 (Base64 URL Safe)
    """
    return (
        base64.b64encode(data)
        .replace(b"+", b"-")
        .replace(b"=", b"_")
        .replace(b"/", b"~")
        .decode("utf-8")
    )


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

    policy = cloudfront_signer.build_policy(url, expire_date).encode("utf-8")
    signature = cloudfront_signer.signature(policy)

    return {
        "url": url,
        "object_key": object_key,
        "cookies": {
            "CloudFront-Policy": _url_b64encode(policy),
            "CloudFront-Signature": _url_b64encode(signature),
            "CloudFront-Key-Pair-Id": key_pair_id,
        },
    }
