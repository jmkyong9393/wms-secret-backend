import shutil
import os

src_base = r"E:\취업\KT AIVLE School\빅프로젝트\develop\team_develop\wms-core-backend"
dst_base = r"E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-secret-backend"

def copy_file(src_rel, dst_rel=None):
    if not dst_rel:
        dst_rel = src_rel
    src = os.path.join(src_base, src_rel)
    dst = os.path.join(dst_base, dst_rel)
    
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    print(f"Copied: {src_rel} -> {dst_rel}")

def copy_dir(src_rel, dst_rel=None):
    if not dst_rel:
        dst_rel = src_rel
    src = os.path.join(src_base, src_rel)
    dst = os.path.join(dst_base, dst_rel)
    
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    print(f"Copied DIR: {src_rel} -> {dst_rel}")

# 1. Copy docker-compose.yml
copy_file("docker-compose.yml")

# 2. Copy celery_app.py
copy_file(r"app\core\celery_app.py")

# 3. Copy services directory (includes redis_pubsub, langgraph_wrapper, etc.)
copy_dir(r"app\services")

# 4. Copy worker.py
copy_file(r"app\worker.py")

print("Migration completed successfully!")
