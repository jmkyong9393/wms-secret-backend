import os
import re

pm_dir = r'E:\취업\KT AIVLE School\빅프로젝트\PM_정답지_백업'
wms_dir = r'E:\취업\KT AIVLE School\빅프로젝트\WMS_docs'

modified_count = 0

for base_dir in [pm_dir, wms_dir]:
    for root, _, files in os.walk(base_dir):
        if 'archive' in root:
            continue
        for f in files:
            if f.endswith('.md'):
                fpath = os.path.join(root, f)
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as fp:
                    content = fp.read()
                
                # EXCELLENT -> GOOD 대소문자 매칭 대체
                new_content = content
                new_content = re.sub(r'\bEXCELLENT\b', 'GOOD', new_content)
                new_content = re.sub(r'\bExcellent\b', 'Good', new_content)
                new_content = re.sub(r'\bexcellent\b', 'good', new_content)
                
                if new_content != content:
                    with open(fpath, 'w', encoding='utf-8') as fp:
                        fp.write(new_content)
                    modified_count += 1
                    print(f'Replaced EXCELLENT -> GOOD in: {fpath}')

print(f'\nTotal modified markdown files: {modified_count}')
