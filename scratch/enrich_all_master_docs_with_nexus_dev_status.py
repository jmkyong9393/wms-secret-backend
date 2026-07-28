"""
====================================================================
[PM_정답지_백업 주요 핵심 기획서/개발문서 실시간 개발내역 종합 업데이트 스크립트]
- 최근 개발 마감된 Nexus 프로젝트 최신 개발 내역 100% 반영:
  1. 재고/출고 도메인 풀스택 구축 (FastAPI REST API + Next.js 대시보드)
  2. 3D Bin Packing 박스 최적화 & CJ대한통운 송장 자동 발급 파이프라인
  3. 50x30mm 열전사 라벨 프린트 (LpnPrintLabel.tsx)
  4. 동적 QR 웹 URL (http://.../lpn/{lpn_id}) 기반 RBAC 이원화 뷰 (WORKER 물류 뷰 vs GUEST AI UBCI 품질 보증서)
  5. [🛡️ 고객용 보증서 미리보기] Admin 1초 미리보기 기능
  6. Stage 2 손글씨/낙서 OCR 1만장 데이터셋 추출 및 YOLOv8m (batch=8, 640x640, rect=True) 훈련 스펙
- WMS_docs 이원화 자동 복사 동기화
====================================================================
"""

import os
import shutil
from pathlib import Path

pm_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\PM_정답지_백업')
wms_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\WMS_docs')
archive_dir = pm_dir / 'archive'

# 주요 핵심 업데이트 대상 파일들
target_docs = [
    'B2B_WMS_AI_Platform_기획서_ver2.0.0.0.md',
    'B2B_WMS_AI_Platform_기획서_ver2.0.0.0_Confidential.md',
    'B2B_WMS_AI_Platform_총개발문서_Master.md',
    '01_엔지니어링_산출물.md',
    '3주차_개인_개발_기록_대외비.md',
    '07_AI_모델_고도화_및_개발_명세서.md',
    'API_Schema_Specification.md',
    'UBCI_Specification_v2.0.0.0.md',
    'Bms_Platform_4대알고리즘.md',
    'FRONTEND_GUIDE.md',
    'WMS_Final_Project_Report_Internal.md'
]

# 최신 실시간 개발 내역 섹션
nexus_latest_dev_summary = """

---

## 🚀 [Nexus 프로젝트 최신 개발 현황 (2026-07-27)]

> **프로젝트 공식 명칭**: Nexus (Nexus AI Smart WMS Platform)  
> **총괄 설계자 / 소유자**: 장문경 (Lead Architect & Project Owner)

### 1. 풀스택 재고(Inventory) & 출고(Outbound) 도메인 구축 마감
* **백엔드 엔드포인트 (`FastAPI`)**:
  * `GET /api/v1/inventory/`: 통합 재고 DataGrid 조회 (LPN, 도서명, ISBN, UBCI 98점 MINT 등급, Zone A-1-3, 수량).
  * `GET /api/v1/inventory/{inventory_id}`: 단일 재고 및 QR 스티커 라벨 데이터 조회.
  * `GET /api/v1/orders/`: 출고 대기 주문 목록 조회 (`PENDING`, `PICKING`, `SHIPPED`).
  * `POST /api/v1/orders/outbound/pick`: 3D Bin Packing 알고리즘 최적 박스(`BOX-MEDIUM`, 완충재 마진 15%) 추천.
  * `POST /api/v1/orders/outbound/ship`: CJ대한통운 자동 송장번호(`CJ-2026-XXXXXX`) 발급 및 출고 확정 처리 (DB 재고 자동 차감).
* **프론트엔드 대시보드 (`Next.js / TypeScript`)**:
  * `/admin/inventory`: 통합 재고 대시보드 (검색, DataGrid, 실물 50x30mm 라벨 인쇄).
  * `/admin/inventory/[id]`: 상세 메타데이터 페이지 및 `[🛡️ 고객용 보증서 미리보기]` 버튼.
  * `/admin/outbound`: 출고 최적화 대시보드 (피킹 가이드, 3D Bin Packing 패킹 시각화, CJ대한통운 송장 즉시 발급).

### 2. 50x30mm 열전사 라벨 & 동적 QR RBAC 이원화 뷰
* **열전사 라벨 프린트 (`LpnPrintLabel.tsx`)**: `@media print` 50mm x 30mm 스티커 규격 레이아웃 및 `http://.../lpn/{lpn_id}` 동적 URL 인코딩.
* **역할 기반 이원화 랜딩 뷰 (`/lpn/[lpn_id]`)**:
  * `WORKER / ADMIN`: 창고 보관 랙(`Zone A-1-3`), 재고 수량, AI 결함 스캔 원본 로그 + `[🛡️ 고객용 보증서 미리보기]` 1초 미리보기 버튼.
  * `GUEST / BUYER`: Gold & Emerald **공식 AI UBCI 품질 보증서** (98점 MINT 뱃지, AI VERIFIED 인장, 대외비 위치 은폐).

### 3. Stage 2 손글씨/낙서 OCR 1만장 전담 모델 (`yolov8_doodle_ocr.pt`) 훈련
* **데이터셋**: AIHub 손글씨/낙서 실물 데이터 10,000장 추출 (Train: 7,000, Val: 2,000, Test: 1,000).
* **하이퍼파라미터**: `imgsz=640`, `rect=True` (종횡비 100% 보존), `mosaic=0.0`, `fliplr=0.0` (거울 반전 100% 방지), `mixup=0.0`, `batch=8` (RTX 2070 Super 8GB VRAM 3.2G 100% OOM Safe).
"""

updated_cnt = 0
for fname in target_docs:
    p = pm_dir / fname
    if p.exists():
        # 원본 백업
        shutil.copy2(p, archive_dir / f"2026-07-27_{p.stem}_pre_dev_enrich.md")
        
        with open(p, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
            
        if 'Nexus 프로젝트 최신 개발 현황' not in content:
            content += nexus_latest_dev_summary
            with open(p, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"[Enriched Dev Status] {fname}")
            updated_cnt += 1

# WMS_docs 이원화 배포 동기화
wms_synced = 0
for fname in target_docs:
    p = pm_dir / fname
    if p.exists():
        clean_name = fname.replace('_Confidential', '').replace('_Internal', '')
        shutil.copy2(p, wms_dir / clean_name)
        wms_synced += 1

print(f"[SUCCESS] Enriched {updated_cnt} core design documents with Nexus development status!")
print(f"[SUCCESS] Synced {wms_synced} core documents to WMS_docs!")
