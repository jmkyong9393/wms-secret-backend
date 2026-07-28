"""
====================================================================
[PM_정답지_백업 내 50여 개 전체 문서 Nexus 프로젝트명 일괄 적용 및 WMS_docs 동기화 스크립트]
1. PM_정답지_백업 내 모든 .md 문서 원본을 archive 폴더에 2026-07-27_..._pre_nexus_all.md 로 백업
2. 모든 .md 문서에 "Nexus" (Nexus AI Smart WMS Platform) 프로젝트 공식 명칭 및 장문경 소유자 정보 반영
3. WMS_docs (팀원 배포용) 이원화 자동 동기화 마감
====================================================================
"""

import os
import shutil
from pathlib import Path

pm_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\PM_정답지_백업')
wms_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\WMS_docs')
archive_dir = pm_dir / 'archive'
archive_dir.mkdir(exist_ok=True)

# 1. PM_정답지_백업 내 모든 .md 파일 탐색
all_md_files = list(pm_dir.glob('*.md'))
print(f"[FOUND] Total {len(all_md_files)} markdown files under PM_정답지_백업")

nexus_header_badge = """
> **프로젝트 공식 명칭**: Nexus (Nexus AI Smart WMS Platform)  
> **총괄 설계자 / 소유자**: 장문경 (Lead Architect & Project Owner)
"""

updated_count = 0
backup_count = 0

for md_path in all_md_files:
    # 1. archive 폴더에 원본 백업
    bak_name = f"2026-07-27_{md_path.stem}_ver2.6.0.3_pre_nexus_all.md"
    shutil.copy2(md_path, archive_dir / bak_name)
    backup_count += 1
    
    # 2. 문서 내용 읽기
    with open(md_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    
    # 일반 단어 교체: B2B WMS -> Nexus B2B WMS, 스마트 WMS -> Nexus 스마트 WMS
    new_content = (content.replace('B2B WMS AI Platform', 'Nexus B2B WMS AI Platform')
                          .replace('B2B_WMS_AI_Platform', 'Nexus_B2B_WMS_AI_Platform')
                          .replace('스마트 WMS', 'Nexus 스마트 WMS'))
    
    # Nexus 프로젝트 공식 명칭 헤더 뱃지 삽입 (없는 경우)
    if 'Nexus (Nexus AI Smart WMS Platform)' not in new_content:
        lines = new_content.split('\n')
        # H1 제목 (# ) 바로 밑 또는 2번째 줄에 헤더 뱃지 삽입
        inserted = False
        for idx, line in enumerate(lines):
            if line.startswith('# '):
                lines.insert(idx + 1, '\n' + nexus_header_badge + '\n')
                inserted = True
                break
        if not inserted:
            lines.insert(0, nexus_header_badge + '\n')
        new_content = '\n'.join(lines)
    
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    updated_count += 1

print(f"[SUCCESS] {backup_count} files backed up to archive/")
print(f"[SUCCESS] {updated_count} markdown files updated with Project Name 'Nexus'!")

# 3. WMS_docs 동기화 (Confidential 필터링 고려)
wms_synced = 0
for md_path in pm_dir.glob('*.md'):
    fname = md_path.name
    # 대외비/Confidential/비밀 파일 제외 후 WMS_docs에 동기화
    if not ('Confidential' in fname or '대외비' in fname or 'Draft' in fname or 'Internal' in fname):
        shutil.copy2(md_path, wms_dir / fname)
        wms_synced += 1
    elif 'Internal' in fname:
        # Internal 파일은 기본 파일명으로 가공 동기화
        clean_name = fname.replace('_Internal.md', '.md')
        shutil.copy2(md_path, wms_dir / clean_name)
        wms_synced += 1

print(f"[SUCCESS] Synced {wms_synced} non-confidential master documents to WMS_docs!")
