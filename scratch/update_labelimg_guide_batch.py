import os

pm_file = r'E:\취업\KT AIVLE School\빅프로젝트\PM_정답지_백업\labelImg_Auto_Labeling_Guide.md'
wms_file = r'E:\취업\KT AIVLE School\빅프로젝트\WMS_docs\labelImg_Auto_Labeling_Guide.md'

for fpath in [pm_file, wms_file]:
    if os.path.exists(fpath):
        with open(fpath, 'r', encoding='utf-8', errors='ignore') as fp:
            content = fp.read()
        
        old_cmd = """```cmd
cd "E:\\취업\\KT AIVLE School\\빅프로젝트\\develop\\solo_develop\\wms-secret-backend"
.venv\\Scripts\\python.exe scratch/auto_labeling_with_yolo.py "사진이담긴폴더경로"
```"""

        new_cmd = """### 방법 A. 마우스 드래그 & 드롭 (가장 쉬운 방식)
사진들이 담긴 폴더를 **`run_auto_labeling.bat`** 배치 파일 위로 **마우스로 끌어서 떨어뜨리기(Drag & Drop)**만 하면 자동으로 1초 라벨링 실행!

### 방법 B. CMD 터미널 명령어 실행
```cmd
cd "E:\\취업\\KT AIVLE School\\빅프로젝트\\develop\\solo_develop\\wms-secret-backend"

# 1) 배치파일 실행 시 (경로 입력창 출력)
run_auto_labeling.bat "C:\\Users\\사용자\\Desktop\\도서사진폴더"

# 2) 파이썬 직접 실행 시
.venv\\Scripts\\python.exe scratch/auto_labeling_with_yolo.py "C:\\Users\\사용자\\Desktop\\도서사진폴더"
```"""

        content = content.replace(old_cmd, new_cmd)
        
        with open(fpath, 'w', encoding='utf-8') as fp:
            fp.write(content)
        print(f'Updated batch guide in: {os.path.basename(fpath)}')

print('Batch guide update complete!')
