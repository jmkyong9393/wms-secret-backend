"""
====================================================================
[Nexus 프로젝트 ver 2.6.0.3 마스터 문서 종합 동기화 & 백업 스크립트]
1. 개인개발가이드 내 4대 마스터 문서 ver 2.6.0.3 업데이트:
   - Nexus 프로젝트 풀스택 아키텍처 (재고/출고 도메인, 3D Bin Packing, CJ대한통운 송장 자동 발급) 명세
   - 50x30mm 열전사 라벨 프린트 (LpnPrintLabel.tsx) & 동적 QR Web URL (/lpn/{lpn_id})
   - 역할 기반 이원화 뷰 (WORKER 물류 뷰 vs GUEST AI UBCI 품질 보증서) & Admin [🛡️ 고객용 보증서 미리보기] 기능 명세
2. archive 폴더에 2026-07-27_..._ver2.6.0.2.md 백업 보존
3. WMS_docs (팀원 배포용) 이원화 자동 복사 동기화
====================================================================
"""

import os
import shutil
from pathlib import Path

pm_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\개인개발가이드')
wms_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\WMS_docs')
archive_dir = pm_dir / 'archive'
archive_dir.mkdir(exist_ok=True)

# 1. 원본 파일 백업 (ver2.6.0.2)
doc_files = [
    'YOLO_Model_History_Internal.md',
    'YOLOv8_Recall_Optimization_Report.md',
    'YOLOv8_모델_학습결과_분석보고서.md',
    'LangGraph_MultiAgent_Vision_Architecture_Internal.md'
]

for doc_name in doc_files:
    src_p = pm_dir / doc_name
    if src_p.exists():
        stem = src_p.stem.replace('_Internal', '')
        bak_name = f"2026-07-27_{stem}_ver2.6.0.2.md"
        shutil.copy2(src_p, archive_dir / bak_name)
        print(f"[Backup] {doc_name} -> archive/{bak_name}")

# 2. ver 2.6.0.3 갱신 세그먼트
v2603_nexus_section = """
---

## 11. [v2.6.0.3] Nexus 풀스택 재고/출고 도메인 및 50x30mm 라벨/보증서 명세 (2026-07-27)

> **프로젝트 공식 명칭**: Nexus (Nexus AI Smart WMS Platform)  
> **총괄 설계자 / 소유자**: 장문경 (Lead Architect & Project Owner)

### 11.1 백엔드(FastAPI) & 프론트엔드(Next.js) 재고/출고 도메인 연동
* **재고(Inventory) 도메인**:
  * `GET /api/v1/inventory/`: 통합 재고 DataGrid 조회 (LPN, 도서명, ISBN, UBCI 98점, MINT 등급, Zone A-1-3, 수량).
  * `GET /api/v1/inventory/{inventory_id}`: 단일 재고 및 QR 라벨 데이터 반환.
  * `/admin/inventory/[id]`: 상세 메타데이터 페이지 및 `[🛡️ 고객용 보증서 미리보기]` 버튼 탑재.
* **출고(Outbound) 도메인**:
  * `GET /api/v1/orders/`: 출고 대기 주문 목록 조회 (`PENDING`, `PICKING`, `SHIPPED`).
  * `POST /api/v1/orders/outbound/pick`: 3D Bin Packing 알고리즘 최적 박스(`BOX-MEDIUM`, 완충재 15% 마진) 추천.
  * `POST /api/v1/orders/outbound/ship`: CJ대한통운 자동 송장번호(`CJ-2026-XXXXXX`) 발급 및 출고 확정 처리 ➔ 재고 자동 차감.

### 11.2 50x30mm 열전사 라벨 & 동적 QR RBAC 이원화 뷰
* **인쇄 컴포넌트 (`LpnPrintLabel.tsx`)**: `@media print` 기반 50mm x 30mm 스티커 규격 레이아웃 및 `http://.../lpn/{lpn_id}` 동적 URL 인코딩.
* **역할 기반 이원화 뷰 (`/lpn/[lpn_id]`)**:
  * `WORKER / ADMIN`: 창고 보관 랙(`Zone A-1-3`), 재고 수량, AI 결함 스캔 원본 로그 + `[🛡️ 고객용 보증서 미리보기]` 1초 버튼.
  * `GUEST / BUYER`: Gold & Emerald **공식 AI UBCI 품질 보증서** (98점 MINT 뱃지, AI VERIFIED 인장, 대외비 위치 은폐).
"""

# 문서 갱신 함수
def update_doc_content_v2603(filepath, new_section, version_tag="v2.6.0.3"):
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    
    content = content.replace('ver 2.6.0.2', 'ver 2.6.0.3').replace('[v2.6.0.2]', '[v2.6.0.3]')
    
    if version_tag not in content:
        content += f"\n{new_section}\n"
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"[Updated Nexus Project] {filepath.name} with {version_tag}")

# 개인개발가이드 문서 갱신
update_doc_content_v2603(pm_dir / 'YOLO_Model_History_Internal.md', v2603_nexus_section)
update_doc_content_v2603(pm_dir / 'YOLOv8_Recall_Optimization_Report.md', v2603_nexus_section)
update_doc_content_v2603(pm_dir / 'YOLOv8_모델_학습결과_분석보고서.md', v2603_nexus_section)
update_doc_content_v2603(pm_dir / 'LangGraph_MultiAgent_Vision_Architecture_Internal.md', v2603_nexus_section)

# WMS_docs 이원화 배포 동기화
shutil.copy2(pm_dir / 'YOLO_Model_History_Internal.md', wms_dir / 'YOLO_Model_History_Internal.md')
shutil.copy2(pm_dir / 'YOLO_Model_History_Internal.md', wms_dir / 'YOLO_Model_History.md')
shutil.copy2(pm_dir / 'YOLOv8_Recall_Optimization_Report.md', wms_dir / 'YOLOv8_Recall_Optimization_Report.md')
shutil.copy2(pm_dir / 'YOLOv8_모델_학습결과_분석보고서.md', wms_dir / 'YOLOv8_모델_학습결과_분석보고서.md')
shutil.copy2(pm_dir / 'LangGraph_MultiAgent_Vision_Architecture_Internal.md', wms_dir / 'LangGraph_MultiAgent_Vision_Architecture.md')

print("[SUCCESS] Nexus Project ver 2.6.0.3 master docs fully updated and synced to WMS_docs!")
