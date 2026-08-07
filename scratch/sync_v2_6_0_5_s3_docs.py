"""
====================================================================
[Nexus 프로젝트 ver 2.6.0.5 AWS S3 다이렉트 업로드 명세 수록 스크립트]
1. 개인개발가이드 내 4주차 개발 기록 및 마스터 산출물 ver 2.6.0.5 업데이트:
   - AWS S3 / CloudFront Pre-signed URL 다이렉트 업로드 (POST /api/v1/inbound/upload-presigned-url)
   - 모바일 카메라 촬영 이미지 엣지 단 Canvas 리사이징 ➔ S3 버킷 다이렉트 업로드 파이프라인
2. WMS_docs 이원화 배포 자동 복사 동기화
====================================================================
"""

import os
import shutil
from pathlib import Path

pm_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\개인개발가이드')
wms_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\WMS_docs')
archive_dir = pm_dir / 'archive'

doc_files = [
    '4주차_개인_개발_기록_대외비.md',
    '01_엔지니어링_산출물.md',
    'B2B_WMS_AI_Platform_기획서_ver2.0.0.0.md',
    'B2B_WMS_AI_Platform_총개발문서_Master.md'
]

# 백업
for fname in doc_files:
    p = pm_dir / fname
    if p.exists():
        shutil.copy2(p, archive_dir / f"2026-07-27_{p.stem}_pre_v2605.md")

s3_dev_section = """
* **AWS S3 / CloudFront Pre-signed URL 다이렉트 업로드 파이프라인 (`app/domains/inbound/`)**:
  - `POST /api/v1/inbound/upload-presigned-url`: 모바일 웹/카메라에서 촬영된 도서 이미지를 백엔드 서버 부하 없이 AWS S3 버킷(`nexus-wms-inspection-images`)으로 직접 업로드하기 위한 S3 Pre-signed URL(PUT) 발급 API 연동 마감.
  - 업로드된 이미지는 CloudFront CDN URL(`https://cdn.nexus-wms.com/inbound/YYYYMMDD/uuid_filename.jpg`)로 가공되어 Vision AI 에이전트로 실시간 전달됨.
"""

for fname in doc_files:
    p = pm_dir / fname
    if p.exists():
        with open(p, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        content = content.replace('ver 2.6.0.4', 'ver 2.6.0.5').replace('[v2.6.0.4]', '[v2.6.0.5]')
        if 'AWS S3 / CloudFront Pre-signed URL' not in content:
            content += f"\n{s3_dev_section}\n"
        with open(p, 'w', encoding='utf-8') as f:
            f.write(content)

# WMS_docs 동기화
for fname in doc_files:
    p = pm_dir / fname
    if p.exists():
        clean_name = fname.replace('_Confidential', '').replace('_Internal', '')
        shutil.copy2(p, wms_dir / clean_name)

print("[SUCCESS] AWS S3 Pre-signed URL specifications updated to ver 2.6.0.5 and synced to WMS_docs!")
