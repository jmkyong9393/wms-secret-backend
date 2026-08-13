"""
검수 파이프라인 공용 상수와 이미지 유틸.

노드 두 개 이상이 함께 쓰는 것만 여기 둔다. 한 노드만 쓰는 헬퍼는 그 노드 파일에 있다
(예: BBox 크롭은 vision, 마모 부위 집계는 policy).
"""
import base64
import io
import os
import tempfile
from typing import Optional

# WBF YOLO 클래스명 -> UBCI_Specification_v2.0.0.0.md 결함 코드 매핑
# 속지(Track 2·3) 컷에서 **제외**하는 결함 유형.
#
# 모서리 마모는 물리적으로 책의 겉면(표지·뒤표지·책등)에서 발생하는 현상이다. 속지 컷에
# 마모처럼 보이는 것이 잡히더라도 그 부위는 표지·책등 컷에서 이미 검출되므로, 속지에서
# 또 세면 같은 손상을 두 번 세는 것이 된다.
#
# 그래서 속지 컷의 마모는 VLM 보고분도, YOLO 후보도 모두 버린다. 판정 주체를 옮기는 것이
# 아니라 **애초에 판정 대상에서 빼는 것**이다.
#
# 표지·뒤표지·책등(Track 1)은 영향을 받지 않는다 - 거기서는 VLM이 여러 컷을 함께 보고
# 종합 판단하며, YOLO 후보는 참고 제보로 그대로 제시된다.
TRACK1_IMAGE_COUNT = 3

INNER_PAGE_EXCLUDED_TYPES = {"DMG_EDGE_WEAR"}


def _is_inner_page(image_index) -> bool:
    """속지(Track 2·3) 컷인지. Track 1(0=앞표지,1=뒤표지,2=책등)은 제외한다."""
    try:
        return int(image_index) >= TRACK1_IMAGE_COUNT
    except (TypeError, ValueError):
        return False

YOLO_TO_UBCI_TYPE = {
    "Wornout": "DMG_EDGE_WEAR",
    "ripped": "DMG_EXT_TEAR",
    "doodle_scribble": "DMG_INT_DOODLE",
}

# UBCI 결함 코드 -> 고객이 읽는 한국어 라벨.
# [수정 이력] 원래 policy_agent 함수 내부 지역변수였는데, 보증서 문서 빌더와 프론트 BBox
# 라벨이 같은 사전을 필요로 해 모듈 스코프로 올렸다 (같은 매핑이 여러 곳에 복제되는 것을 방지).
DEFECT_TRANSLATION_MAP = {
    "DMG_INT_DOODLE": "내부 손글씨/낙서",
    "DMG_INT_STAIN": "내지 오염/이물질",
    "DMG_EXT_CRUSH": "표지 모서리 찍힘/구겨짐",
    "DMG_EXT_WET": "액체 오염/습기/휨 (WATER_DAMAGE)",
    "DMG_EXT_TEAR": "커버 찢어짐 (Tear)",
    "DMG_INT_DISCOLOR": "내지 황변/빛바램",
    "DMG_EXT_SCRATCH": "표지 긁힘/스크래치",
    "DMG_EXT_STICKER": "스티커/바코드 자국",
    "DMG_EDGE_WEAR": "모서리 마모",
    "DMG_SPINE_CRACK": "책등 갈라짐",
    "DMG_BINDING_LOOSE": "제본 벌어짐",
    "DMG_SIGNATURE": "측면 서명/이름",
    "DMG_STAMP": "도서관/장서인 도장",
}


# GPT-4o Vision 전송본의 긴 변 상한(px). S3 원본은 FHD(1920px)로 적재되지만 OpenAI는
# 512px 타일 단위로 과금하므로, 원본을 그대로 보내면 YOLO가 아닌 VLM 쪽 비용만 커진다.
# YOLO(WBF)는 로컬 풀해상도 크롭을 쓰고, VLM에는 이 상한으로 다운스케일한 사본만 보낸다.
VLM_MAX_IMAGE_EDGE = 1536


def _downscale_for_vlm(data: bytes) -> bytes:
    """이미지 바이트의 긴 변이 VLM_MAX_IMAGE_EDGE를 넘으면 비율 유지 다운스케일한 JPEG 반환.

    실패 시 원본 바이트를 그대로 반환한다(fail-open) - 다운스케일은 비용 최적화일 뿐
    판독 자체를 막을 이유가 없다. 업스케일은 절대 하지 않는다.
    """
    try:
        import io
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            if max(img.size) <= VLM_MAX_IMAGE_EDGE:
                return data
            img = img.convert("RGB")
            img.thumbnail((VLM_MAX_IMAGE_EDGE, VLM_MAX_IMAGE_EDGE), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90)
            return buf.getvalue()
    except Exception as e:
        print(f"[Vision Agent] VLM 다운스케일 실패({e}) - 원본 바이트로 전송")
        return data


def _load_image_as_base64(path_or_url: str) -> Optional[str]:
    """로컬 파일 경로 또는 HTTP(S) URL을 GPT-4o Vision에 넣을 base64 문자열로 인코딩.

    전송 직전 _downscale_for_vlm으로 긴 변 1536px 상한을 적용한다 (원본 파일은 불변).
    """
    import base64
    try:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            import urllib.request
            req = urllib.request.Request(path_or_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as response:
                return base64.b64encode(_downscale_for_vlm(response.read())).decode("utf-8")
        with open(path_or_url, "rb") as f:
            return base64.b64encode(_downscale_for_vlm(f.read())).decode("utf-8")
    except Exception as e:
        print(f"[Vision Agent] 이미지 로드 실패 ({path_or_url}): {e}")
        return None


def _ensure_local_path(path_or_url: str) -> Optional[str]:
    """WBF YOLO 추론은 로컬 파일 경로가 필요 - URL이면 임시 파일로 다운로드."""
    if not (path_or_url.startswith("http://") or path_or_url.startswith("https://")):
        return path_or_url if os.path.exists(path_or_url) else None
    try:
        import urllib.request
        import tempfile
        req = urllib.request.Request(path_or_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = response.read()
        suffix = os.path.splitext(path_or_url)[1] or ".jpg"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(data)
            return tmp.name
    except Exception as e:
        print(f"[Vision Agent] WBF용 로컬 다운로드 실패 ({path_or_url}): {e}")
        return None

