"""
====================================================================
[Copy Crawled Torn Book Images to Desktop for User Inspection]
- 크롤링한 모든 실물 찢어진 책 이미지(37장+)를
  바탕화면 전용 폴더(C:\\Users\\jmkyo\\Desktop\\crawled_torn_books_review)로 복사합니다.
====================================================================
"""

import os
import shutil
from pathlib import Path

src_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\crawled_torn_books')
desktop_review_dir = Path(r'C:\Users\jmkyo\Desktop\crawled_torn_books_review')

def copy_for_user_review():
    os.makedirs(desktop_review_dir, exist_ok=True)
    
    files = list(src_dir.glob('*.*'))
    copied_count = 0

    for f in files:
        if f.suffix.lower() in ['.jpg', '.jpeg', '.png']:
            dst_p = desktop_review_dir / f.name
            shutil.copy2(f, dst_p)
            copied_count += 1

    print(f"\n=======================================================")
    print(f"[SUCCESS] Copied Crawled Images to Desktop Review Folder!")
    print(f"  - Source Dir: {src_dir}")
    print(f"  - Desktop Inspection Folder: {desktop_review_dir}")
    print(f"  - Total Copied Images for User Review: {copied_count}")
    print(f"=======================================================")

if __name__ == "__main__":
    copy_for_user_review()
