import os

pm_file = r'E:\취업\KT AIVLE School\빅프로젝트\PM_정답지_백업\LangGraph_MultiAgent_Vision_Architecture_Internal.md'
wms_file = r'E:\취업\KT AIVLE School\빅프로젝트\WMS_docs\LangGraph_MultiAgent_Vision_Architecture.md'

for fpath in [pm_file, wms_file]:
    if os.path.exists(fpath):
        with open(fpath, 'r', encoding='utf-8', errors='ignore') as fp:
            content = fp.read()
        
        # 작성자명 정밀 수정
        content = content.replace(
            "**작성자**: Senior AI Technical Architect (Antigravity)",
            "**작성자**: 장문경 (Lead Architect & Project Owner)"
        )
        
        with open(fpath, 'w', encoding='utf-8') as fp:
            fp.write(content)
        print(f'Updated author to 장문경 in: {os.path.basename(fpath)}')

print('Author update complete!')
