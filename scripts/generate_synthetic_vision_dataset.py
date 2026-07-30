"""
========================================================================================
[Nexus WMS Multi-Agent Vision Pipeline]
ISBN 기반 도서 이미지 수집, 합성 결함(Augmentation) 증강 및 Multi-Agent 투입 파이프라인
========================================================================================
- 작성일: 2026-07-31
- 작성자: Antigravity AI Engine
- 비고: S3 연동 준비 완료 모드 (Local Storage / AWS S3 Dual Storage Support)
"""

import os
import sys
import io
import time
import urllib.request
import urllib.parse
import json
import random
from typing import List, Dict, Any, Optional
from PIL import Image, ImageEnhance, ImageDraw, ImageFilter

# --------------------------------------------------------------------------------------
# 1. Configuration & Storage Interface (S3 Ready)
# --------------------------------------------------------------------------------------
STORAGE_MODE = os.getenv("STORAGE_MODE", "LOCAL")  # "LOCAL" or "S3"
AWS_S3_BUCKET = os.getenv("AWS_S3_BUCKET", "nexus-wms-vision-assets")
LOCAL_OUTPUT_DIR = os.getenv("LOCAL_OUTPUT_DIR", "./synthetic_vision_data")

# Aladin & Naver API Keys (Future Credentials Binding)
ALADIN_TTB_KEY = os.getenv("ALADIN_TTB_KEY", "ttbdemo")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")


class VisionDataStorage:
    """S3 연동 및 로컬 저장소 이원화 인터페이스"""
    def __init__(self, mode: str = "LOCAL"):
        self.mode = mode
        if self.mode == "LOCAL":
            os.makedirs(LOCAL_OUTPUT_DIR, exist_ok=True)
            print(f"[Storage] Local Output Directory initialized: {os.path.abspath(LOCAL_OUTPUT_DIR)}")
        elif self.mode == "S3":
            import boto3
            self.s3_client = boto3.client("s3")
            print(f"[Storage] AWS S3 Storage Client initialized for bucket: {AWS_S3_BUCKET}")

    def save_image(self, img: Image.Image, filename: str) -> str:
        """이미지를 저장하고 반환 URL/경로 생성"""
        if self.mode == "LOCAL":
            filepath = os.path.join(LOCAL_OUTPUT_DIR, filename)
            img.save(filepath, format="JPEG", quality=92)
            return filepath
        elif self.mode == "S3":
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=92)
            buffer.seek(0)
            s3_key = f"synthetic_vision/{filename}"
            self.s3_client.upload_fileobj(
                buffer, 
                AWS_S3_BUCKET, 
                s3_key, 
                ExtraArgs={"ContentType": "image/jpeg"}
            )
            return f"https://{AWS_S3_BUCKET}.s3.amazonaws.com/{s3_key}"
        return ""


# --------------------------------------------------------------------------------------
# 2. ISBN Book Image Crawler / API Fetcher
# --------------------------------------------------------------------------------------
class BookImageFetcher:
    """ISBN 기반 정품 도서 이미지 및 메타데이터 수집기"""
    
    @staticmethod
    def fetch_book_cover_by_isbn(isbn: str) -> Dict[str, Any]:
        """알라딘 Open API 또는 네이버 책 검색 API를 통해 도서 표지 및 스펙 조회"""
        print(f"[Crawler] Fetching metadata & cover image for ISBN: {isbn}")
        
        # Aladin Open API Query URL
        aladin_url = f"http://www.aladin.co.kr/ttb/api/ItemLookUp.aspx?ttbkey={ALADIN_TTB_KEY}&itemIdType=ISBN13&ItemId={isbn}&output=js&Version=20131101&Cover=Big"
        
        try:
            req = urllib.request.Request(aladin_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if "item" in data and len(data["item"]) > 0:
                    item = data["item"][0]
                    return {
                        "title": item.get("title", "알 수 없는 도서"),
                        "author": item.get("author", "저자 미상"),
                        "publisher": item.get("publisher", "출판사 미상"),
                        "price": item.get("priceStandard", 20000),
                        "cover_url": item.get("cover", ""),
                        "category": item.get("categoryName", "일반도서")
                    }
        except Exception as e:
            print(f"[Crawler Warning] Aladin API fallback triggered: {e}")

        # Fallback Default Mock Metadata
        return {
            "title": f"합성 테스트 도서 (ISBN: {isbn})",
            "author": "알고리즘 연구팀",
            "publisher": "Nexus Press",
            "price": 25000,
            "cover_url": "https://image.aladin.co.kr/product/31805/45/cover500/k972833345_1.jpg",
            "category": "IT"
        }

    @staticmethod
    def download_image(url: str) -> Optional[Image.Image]:
        """URL에서 이미지를 다운로드하여 PIL Image로 변환"""
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                image_data = resp.read()
                return Image.open(io.BytesIO(image_data)).convert("RGB")
        except Exception as e:
            print(f"[Crawler Error] Image download failed ({url}): {e}")
            return None


# --------------------------------------------------------------------------------------
# 3. Vision Defect Synthetic Augmenter (OpenCV / PIL Defect Generator)
# --------------------------------------------------------------------------------------
class DefectAugmenter:
    """속지 오염, 모서리 찌그러짐, 형광펜, 변색 결함 합성 엔진"""
    
    @staticmethod
    def apply_corner_dent(image: Image.Image) -> Image.Image:
        """도서 모서리 찌그러짐 및 물리적 변형 합성"""
        img = image.copy()
        draw = ImageDraw.Draw(img)
        w, h = img.size
        # 우측 상단 모서리 삼각 변형
        draw.polygon([(w - 40, 0), (w, 0), (w, 40)], fill=(20, 20, 20))
        return img.filter(ImageFilter.SMOOTH)

    @staticmethod
    def apply_highlighter_stain(image: Image.Image) -> Image.Image:
        """속지 형광펜 착색 결함 합성"""
        img = image.copy()
        draw = ImageDraw.Draw(img, "RGBA")
        w, h = img.size
        # 형광 노란색 투명 레이어
        y_pos = random.randint(int(h * 0.3), int(h * 0.7))
        draw.rectangle([(30, y_pos), (w - 30, y_pos + 25)], fill=(255, 255, 0, 110))
        return img.convert("RGB")

    @staticmethod
    def apply_yellowing_stain(image: Image.Image) -> Image.Image:
        """오래된 중고 서적 습기 및 옐로잉(Yellowing) 변색 합성"""
        enhancer = ImageEnhance.Color(image)
        img = enhancer.enhance(0.7)  # 채도 약간 감소
        
        # 누런 톤 필터 레이어
        yellow_layer = Image.new("RGB", img.size, (245, 230, 200))
        return Image.blend(img, yellow_layer, alpha=0.25)


# --------------------------------------------------------------------------------------
# 4. Main Synthetic Pipeline Execution Interface (CLI Ready)
# --------------------------------------------------------------------------------------
def run_synthetic_vision_pipeline(isbn_list: List[str]):
    """
    ISBN 목록을 받아 [1. 원본 수집] -> [2. 결함 합성] -> [3. Storage 저장] -> [4. Multi-Agent 투입 준비]
    """
    print("==========================================================================================")
    print("=== STARTING SYNTHETIC VISION DATASET GENERATION PIPELINE ===")
    print("==========================================================================================")

    storage = VisionDataStorage(mode=STORAGE_MODE)
    fetcher = BookImageFetcher()

    results = []

    for isbn in isbn_list:
        print(f"\n[Processing] ISBN: {isbn}")
        meta = fetcher.fetch_book_cover_by_isbn(isbn)
        
        raw_img = fetcher.download_image(meta["cover_url"])
        if not raw_img:
            # Fallback blank canvas
            raw_img = Image.new("RGB", (500, 700), color=(240, 240, 240))

        # Generate 3 Defect Variations (MINT, GOOD, REJECT)
        img_mint = raw_img.copy()  # MINT: No defect
        img_good = DefectAugmenter.apply_highlighter_stain(raw_img)  # GOOD: Highlighter
        img_reject = DefectAugmenter.apply_corner_dent(DefectAugmenter.apply_yellowing_stain(raw_img))  # REJECT

        # Save to Storage
        url_mint = storage.save_image(img_mint, f"{isbn}_MINT.jpg")
        url_good = storage.save_image(img_good, f"{isbn}_GOOD.jpg")
        url_reject = storage.save_image(img_reject, f"{isbn}_REJECT.jpg")

        pipeline_item = {
            "isbn": isbn,
            "title": meta["title"],
            "base_price": meta["price"],
            "images": {
                "MINT": url_mint,
                "GOOD": url_good,
                "REJECT": url_reject
            }
        }
        results.append(pipeline_item)
        print(f"-> Successfully generated 3 vision variants for {meta['title']}")

    print("\n==========================================================================================")
    print(f"=== PIPELINE COMPLETED: Processed {len(results)} books. Ready for Multi-Agent Inspection ===")
    print("==========================================================================================")
    return results


if __name__ == "__main__":
    print("[NOTICE] This script is ready for future execution after S3 bucket setup.")
    print("Example Usage: run_synthetic_vision_pipeline(['9791163033455', '9788988647639'])")
