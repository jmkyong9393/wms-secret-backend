import os

pm_file = r'E:\취업\KT AIVLE School\빅프로젝트\PM_정답지_백업\LangGraph_MultiAgent_Vision_Architecture_Internal.md'
wms_file = r'E:\취업\KT AIVLE School\빅프로젝트\WMS_docs\LangGraph_MultiAgent_Vision_Architecture.md'

for fpath in [pm_file, wms_file]:
    if os.path.exists(fpath):
        with open(fpath, 'r', encoding='utf-8', errors='ignore') as fp:
            content = fp.read()
        
        # 그래프 노드 복잡도 단순화: 단일 Vision Agent 내부 asyncio.gather 병렬 로직으로 수정
        old_pattern = "LangGraph `Send API` (Fan-out / Fan-in)를 도입하여 도서 표지, 내지, 측면 등 다각도 이미지를 독립 비전 노드로 병렬 추론한 후 Supervisor에서 동기화 조인(Join)"
        new_pattern = "단일 `Vision Agent` 내부에서 Python `asyncio.gather()` 비동기 병렬 추론 파이프라인을 구동하여 도서 표지, 내지, 측면 다각도 이미지를 노드 증가 없이 고속 병렬 처리"
        
        content = content.replace(old_pattern, new_pattern)
        
        # 4.1 섹션 리팩토링
        old_section_4_1 = """### 4.1 다각도 비전 병렬 처리 (Parallel Multi-View Vision Pipeline)
- **개선 방안**: LangGraph `Send API` (Fan-out / Fan-in)를 도입하여 도서 표지, 내지, 측면 등 다각도 이미지를 독립 비전 노드로 병렬 추론한 후 Supervisor에서 동기화 조인(Join).
- **효과**: I/O 통신 병목을 완전 해소하여 전체 파이프라인 평균 처리 속도 **2.1초 이내** 보장."""

        new_section_4_1 = """### 4.1 단일 비전 에이전트 내 비동기 배치 병렬 처리 (Async Multi-Image Processing)
- **아키텍처 단순화**: 별도의 독립 노드로 그래프를 복잡하게 분지시키지 않고, 단일 `Vision Agent` 내부에서 Python `asyncio.gather()`를 활용해 표지, 내지, 측면 이미지를 비동기 병렬 추론.
- **효과**: LangGraph 그래프 토폴로지의 간결성(Simplicity)을 100% 유지하면서도 I/O 병목을 제거하여 평균 처리 속도 **2.1초 이내** 완수."""

        content = content.replace(old_section_4_1, new_section_4_1)

        with open(fpath, 'w', encoding='utf-8') as fp:
            fp.write(content)
        print(f'Simplified vision parallel arch in: {os.path.basename(fpath)}')

print('Document simplification complete!')
