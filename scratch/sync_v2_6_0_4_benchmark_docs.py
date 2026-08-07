"""
====================================================================
[Nexus 프로젝트 ver 2.6.0.4 4대 벤치마킹 기능 완공 문서 스크립트]
1. 개인개발가이드 내 4대 마스터 문서 ver 2.6.0.4 업데이트:
   - 엑셀 CSV 다운로드 기능 (exportToCSV)
   - 1초/건 가상 주문 무한 생성 데몬 (seed_mock_orders.py)
   - 실시간 FDS / 악성 재고 경고 토스트 알람 배너
   - 피킹 숏픽(Short Pick) 실손 예외 처리 및 차순위 FIFO LPN 자동 재할당 API
2. WMS_docs 이원화 자동 복사 동기화
====================================================================
"""

import os
import shutil
from pathlib import Path

pm_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\개인개발가이드')
wms_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\WMS_docs')
archive_dir = pm_dir / 'archive'

doc_files = [
    'YOLO_Model_History_Internal.md',
    'YOLOv8_Recall_Optimization_Report.md',
    'YOLOv8_모델_학습결과_분석보고서.md',
    'LangGraph_MultiAgent_Vision_Architecture_Internal.md'
]

# 백업
for doc_name in doc_files:
    src_p = pm_dir / doc_name
    if src_p.exists():
        stem = src_p.stem.replace('_Internal', '')
        bak_name = f"2026-07-27_{stem}_ver2.6.0.3_pre_v2604.md"
        shutil.copy2(src_p, archive_dir / bak_name)

v2604_section = """
---

## 12. [v2.6.0.4] 4대 벤치마킹 엔터프라이즈 코어 기능 구축 마감 (2026-07-27)

> **프로젝트 공식 명칭**: Nexus (Nexus AI Smart WMS Platform)  
> **총괄 설계자 / 소유자**: 장문경 (Lead Architect & Project Owner)

### 12.1 구축 완료된 4대 벤치마킹 코어 기능 명세
1. **엑셀 CSV 데이터 Export 모듈 (`src/lib/exportCsv.ts`)**:
   - `/admin/inventory` 및 `/admin/outbound` 대시보드 내 **`[📊 Excel 다운로드]`** 버튼 클릭 시, UTF-8 BOM 인코딩 엑셀 CSV 파일 즉시 다운로드.
2. **시연용 1초/건 가상 출고 주문 무한 생성 데몬 (`scratch/seed_mock_orders.py`)**:
   - 시연 및 영상 촬영 시 B2B/B2C 가상 주문을 무한 생성하여 대시보드에 실시간 주문이 축적되는 액티브 시뮬레이션 데몬.
3. **실시간 FDS 및 악성 재고 토스트 알림 배너**:
   - 120일 이상 장기 체류 악성 재고 또는 파손 도서 감지 시 대시보드 상단 실시간 경고 토스트 팝업 출력.
4. **피킹 숏픽(Short Pick) 예외 처리 및 차순위 FIFO LPN 자동 대체 배정 API**:
   - `POST /api/v1/orders/outbound/short-pick`: 피킹 시 실물 파손/실종 발생 시 차순위 FIFO LPN(`LPN-260727-F0001`) 및 대체 보관 랙(`Zone A-2-1`) 자동 재할당.
"""

for fname in doc_files:
    p = pm_dir / fname
    if p.exists():
        with open(p, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        content = content.replace('ver 2.6.0.3', 'ver 2.6.0.4').replace('[v2.6.0.3]', '[v2.6.0.4]')
        if 'v2.6.0.4' not in content:
            content += v2604_section
        with open(p, 'w', encoding='utf-8') as f:
            f.write(content)

# WMS_docs 동기화
shutil.copy2(pm_dir / 'YOLO_Model_History_Internal.md', wms_dir / 'YOLO_Model_History_Internal.md')
shutil.copy2(pm_dir / 'YOLO_Model_History_Internal.md', wms_dir / 'YOLO_Model_History.md')
shutil.copy2(pm_dir / 'YOLOv8_Recall_Optimization_Report.md', wms_dir / 'YOLOv8_Recall_Optimization_Report.md')
shutil.copy2(pm_dir / 'YOLOv8_모델_학습결과_분석보고서.md', wms_dir / 'YOLOv8_모델_학습결과_분석보고서.md')
shutil.copy2(pm_dir / 'LangGraph_MultiAgent_Vision_Architecture_Internal.md', wms_dir / 'LangGraph_MultiAgent_Vision_Architecture.md')

print("[SUCCESS] All 4 master documents updated to ver 2.6.0.4 and synced to WMS_docs!")
