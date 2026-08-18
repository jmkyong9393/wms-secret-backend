"""
====================================================================
[Wikimedia Commons & Public API Real Torn Book Image Crawler]
- Wikimedia Commons API 및 공개 이미지 검색 엔지니어링을 통해
  실물 찢어진 책, 파손된 헌책 고해상도 이미지를 안전하게 자동 수집합니다.
====================================================================
"""

import os
import json
import time
import urllib.request
import urllib.parse
from pathlib import Path

output_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\crawled_torn_books')
os.makedirs(output_dir, exist_ok=True)

HEADERS = {
    "User-Agent": "WMSDefectCrawler/1.0 (contact: test@example.com) Python-urllib"
}

KEYWORDS = [
    "damaged book",
    "torn page",
    "torn book",
    "broken book",
    "old torn book"
]

def crawl_wikimedia(query, max_count=20):
    print(f"[Wikimedia Crawler] Querying for: '{query}'...")
    url = f"https://commons.wikimedia.org/w/api.php?action=query&generator=search&gtepsize={max_count}&prop=imageinfo&iiprop=url|size&format=json&gsearch={urllib.parse.quote(query)}"
    
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        resp = urllib.request.urlopen(req, timeout=10).read().decode('utf-8')
        data = json.loads(resp)
        pages = data.get('query', {}).get('pages', {})
        
        downloaded = 0
        clean_q = query.replace(" ", "_")

        for p_id, page in pages.items():
            imginfo = page.get('imageinfo', [])
            if not imginfo:
                continue
            
            img_url = imginfo[0].get('url')
            if not img_url or not img_url.lower().endswith(('.jpg', '.jpeg', '.png')):
                continue

            try:
                img_req = urllib.request.Request(img_url, headers=HEADERS)
                img_bytes = urllib.request.urlopen(img_req, timeout=8).read()
                
                if len(img_bytes) < 30000: # 30KB 미만 썸네일 제외
                    continue

                f_name = f"wiki_{clean_q}_{downloaded:03d}.jpg"
                f_path = output_dir / f_name
                with open(f_path, "wb") as f:
                    f.write(img_bytes)

                downloaded += 1
                print(f"  [Saved] {f_name} ({len(img_bytes)//1024} KB)")
                time.sleep(0.3)
                
                if downloaded >= max_count:
                    break
            except Exception as e:
                continue

        return downloaded
    except Exception as e:
        print(f"[Error] Wikimedia API query failed: {e}")
        return 0

def main():
    total = 0
    for kw in KEYWORDS:
        cnt = crawl_wikimedia(kw, max_count=25)
        total += cnt
        time.sleep(0.5)

    # 전체 수집된 파일 수 검증
    all_files = list(output_dir.glob('*.*'))
    print(f"\n=======================================================")
    print(f"[SUCCESS] Real Torn Book Images Crawled!")
    print(f"  - Output Directory: {output_dir}")
    print(f"  - Total Real High-Res Images Collected: {len(all_files)}")
    print(f"=======================================================")

if __name__ == "__main__":
    main()
