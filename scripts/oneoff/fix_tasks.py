filepath = 'app/worker/tasks.py'
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()
content = content.replace('datetime.now(timezone.utc)', 'now_kst()')
if 'import now_kst' not in content:
    content = content.replace('from datetime import datetime, timezone', 'from datetime import datetime, timezone\nfrom app.models.wms import now_kst')
with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)
print("Fixed tasks.py")
