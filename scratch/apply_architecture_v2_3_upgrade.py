import os
import shutil

pm_dir = r'E:\취업\KT AIVLE School\빅프로젝트\PM_정답지_백업'
wms_dir = r'E:\취업\KT AIVLE School\빅프로젝트\WMS_docs'

pm_archive = os.path.join(pm_dir, 'archive')
wms_archive = os.path.join(wms_dir, 'archive')

os.makedirs(pm_archive, exist_ok=True)
os.makedirs(wms_archive, exist_ok=True)

# 1. 백업 (Archive) - 절대 이동하지 않고 복사!
pm_src = os.path.join(pm_dir, 'LangGraph_MultiAgent_Vision_Architecture_Internal.md')
wms_src = os.path.join(wms_dir, 'LangGraph_MultiAgent_Vision_Architecture.md')

if os.path.exists(pm_src):
    shutil.copy2(pm_src, os.path.join(pm_archive, 'LangGraph_MultiAgent_Vision_Architecture_Internal_ver2.2.0.0.md'))
    print('Archived PM master doc to ver2.2.0.0')

if os.path.exists(wms_src):
    shutil.copy2(wms_src, os.path.join(wms_archive, 'LangGraph_MultiAgent_Vision_Architecture_ver2.2.0.0.md'))
    print('Archived WMS public doc to ver2.2.0.0')
