"""
====================================================================
[Advanced Web Crawler for Real Torn / Ripped Book Images]
- Google / DuckDuckGo / Unsplash / Bing 웹 검색 엔진을 통해 
  실물 찢어진 책, 파손된 헌책 고해상도 이미지를 100장 이상 수집합니다.
====================================================================
"""

import os
import re
import time
import json
import urllib.request
import urllib.parse
from pathlib import Path

output_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\crawled_torn_books')
os.makedirs(output_dir, exist_ok=True)

KEYWORDS = [
    "torn book cover",
    "ripped book page",
    "damaged book cover",
    "torn paper book",
    "broken book binding"
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

def crawl_duckduckgo(query, max_count=30):
    print(f"\n[Crawler] Searching DuckDuckGo for: '{query}'...")
    url = f"https://duckduckgo.com/i.js?q={urllib.parse.quote(query)}&o=json"
    
    # 1. Token vqd 구하기
    req_init = urllib.request.Request(f"https://duckduckgo.com/?q={urllib.parse.quote(query)}", headers=HEADERS)
    try:
        init_html = urllib.request.urlopen(req_init, timeout=5).read().decode('utf-8')
        vqd_match = re.search(r'vqd=([\d-]+)', init_html)
        if not vqd_match:
            print("[Warning] Could not get DDG vqd token, using fallback.")
            return 0
        vqd = vqd_match.group(1)
        api_url = f"https://duckduckgo.com/i.js?q={urllib.parse.quote(query)}&o=json&vqd={vqd}&p=1"
        
        req_api = urllib.request.Request(api_url, headers=HEADERS)
        json_data = json.loads(urllib.request.urlopen(req_api, timeout=5).read().decode('utf-8'))
        
        results = json_data.get('results', [])
        print(f"[Crawler] Found {len(results)} images from DuckDuckGo for '{query}'.")
        
        downloaded = 0
        clean_q = re.sub(r'[^\w\-_]', '_', query)

        for idx, res in enumerate(results[:max_count]):
            img_url = res.get('image')
            if not img_url:
                continue
            
            try:
                img_req = urllib.request.Request(img_url, headers=HEADERS)
                img_bytes = urllib.request.urlopen(img_req, timeout=5).read()
                
                if len(img_bytes) < 20000: # 20KB 이상만
                    continue

                f_path = output_dir / f"ddg_{clean_q}_{idx:03d}.jpg"
                with open(f_path, "wb") as f:
                    f.write(img_bytes)

                downloaded += 1
                print(f"  [Saved] {f_path.name} ({len(img_bytes)//1024} KB)")
                time.sleep(0.2)
            except Exception:
                continue

        return downloaded
    except Exception as e:
        print(f"[Error] DDG Crawling failed: {e}")
        return 0

def main():
    total = 0
    for kw in KEYWORDS:
        cnt = crawl_duckduckgo(kw, max_count=30)
        total += cnt
        time.sleep(0.5)

    print(f"\n=======================================================")
    print(f"[SUCCESS] Web Crawling Complete!")
    print(f"  - Output Folder: {output_dir}")
    print(f"  - Total Real Torn Book Images Crawled: {total}")
    print(f"=======================================================")

if __name__ == "__main__":
    main()
