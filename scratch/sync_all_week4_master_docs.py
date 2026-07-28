"""
====================================================================
[Week 4 개발 내역 01_엔지니어링_산출물 및 주요 마스터 문서 전면 동기화 스크립트]
- 01_엔지니어링_산출물.md
- B2B_WMS_AI_Platform_기획서_ver2.0.0.0.md
- B2B_WMS_AI_Platform_워크플로우_ver2.0.0.0.md
- B2B_WMS_AI_Platform_총개발문서_Master.md
- WMS_docs 이원화 배포 자동 복사 동기화
====================================================================
"""

import os
import shutil
from pathlib import Path

pm_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\PM_정답지_백업')
wms_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\WMS_docs')
archive_dir = pm_dir / 'archive'

# 업데이트 대상 파일
master_docs = [
    '01_엔지니어링_산출물.md',
    'B2B_WMS_AI_Platform_기획서_ver2.0.0.0.md',
    'B2B_WMS_AI_Platform_워크플로우_ver2.0.0.0.md',
    'B2B_WMS_AI_Platform_총개발문서_Master.md'
]

# 원본 백업
for fname in master_docs:
    p = pm_dir / fname
    if p.exists():
        shutil.copy2(p, archive_dir / f"2026-07-27_{p.stem}_pre_w4_final.md")

week4_full_update_block = """

---

## 📌 [Nexus 프로젝트 4주차 개발 완공 및 마스터 명세 (2026-07-27)]

> **프로젝트 공식 명칭**: Nexus (Nexus AI Smart WMS Platform)  
> **총괄 설계자 / 소유자**: 장문경 (Lead Architect & Project Owner)

### 1. 풀스택 재고 & 출고 도메인 구축 마감
* **FastAPI 백엔드 라우터 연동 (`app/domains/`)**:
  * `GET /api/v1/inventory/`: 통합 재고 DataGrid 조회 (LPN, 도서명, ISBN, UBCI 98점 MINT 등급, Zone A-1-3, 수량).
  * `GET /api/v1/inventory/{inventory_id}`: 단일 재고 및 QR 스티커 라벨 데이터 반환.
  * `GET /api/v1/orders/`: 출고 대기 주문 목록 조회 (`PENDING`, `PICKING`, `SHIPPED`).
  * `POST /api/v1/orders/outbound/pick`: 3D Bin Packing 알고리즘 최적 박스(`BOX-MEDIUM`, 완충재 마진 15%) 추천.
  * `POST /api/v1/orders/outbound/ship`: CJ대한통운 자동 송장번호(`CJ-2026-XXXXXX`) 발급 및 출고 확정 처리 ➔ DB 재고 자동 차감.
  * `POST /api/v1/orders/outbound/short-pick`: 피킹 숏픽(Short Pick) 실손 예외 처리 및 차순위 FIFO LPN(`LPN-260727-F0001`) 자동 대체 배정.

### 2. 50x30mm 열전사 라벨 & 동적 QR RBAC 이원화 뷰
* **열전사 라벨 인쇄 컴포넌트 (`LpnPrintLabel.tsx`)**: `@media print` 기반 가로 50mm x 세로 30mm 스티커 인쇄 레이아웃 및 `http://.../lpn/{lpn_id}` 동적 URL 인코딩.
* **역할 기반 이원화 랜딩 뷰 (`/lpn/[lpn_id]`)**:
  * `WORKER / ADMIN`: 창고 보관 랙(`Zone A-1-3`), 재고 수량, AI 결함 스캔 원본 로그 + `[🛡️ 고객용 보증서 미리보기]` 1초 미리보기 버튼.
  * `GUEST / BUYER`: Gold & Emerald **공식 AI UBCI 품질 보증서** (98점 MINT 뱃지, AI VERIFIED 인장, 대외비 창고 위치 100% 은폐).

### 3. 4대 벤치마킹 엔터프라이즈 모듈 완공
* **엑셀 CSV Export 모듈 (`src/lib/exportCsv.ts`)**: 재고 및 출고 대시보드 **`[📊 Excel 다운로드]`** 클릭 시 UTF-8 BOM CSV 파일 다운로드.
* **1초/건 가상 출고 주문 무한 생성 데몬 (`scratch/seed_mock_orders.py`)**: 시연 및 시연 영상 촬영용 무한 가상 주문 적재 데몬.
* **실시간 FDS 및 악성 재고 토스트 알림 배너**: 대시보드 상단 실시간 경고 팝업 출력.

### 4. Stage 2 손글씨/낙서 OCR 1만장 전담 모델 (`yolov8_doodle_ocr.pt`)
* **데이터셋 및 하이퍼파라미터**: AIHub 실물 10,000장 extraction (Train: 7,000, Val: 2,000, Test: 1,000), `imgsz=640`, `rect=True`, `mosaic=0.0`, `fliplr=0.0`, `batch=8` (RTX 2070 Super 8GB VRAM 3.2G 100% OOM Safe).
"""

for fname in master_docs:
    p = pm_dir / fname
    if p.exists():
        with open(p, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        if 'Nexus 프로젝트 4주차 개발 완공 및 마스터 명세' not in content:
            content += week4_full_update_block
            with open(p, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"[Updated Master Doc] {fname}")

# WMS_docs 동기화
for fname in master_docs:
    p = pm_dir / fname
    if p.exists():
        shutil.copy2(p, wms_dir / fname)

print("[SUCCESS] All 4 master documents and WMS_docs updated with Week 4 development details!")
