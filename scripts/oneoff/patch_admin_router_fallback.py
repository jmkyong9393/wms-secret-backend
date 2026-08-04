import codecs
path = 'app/domains/admin/router.py'
with codecs.open(path, 'r', 'utf-8') as f:
    content = f.read()

replacement = '''    job = session.get(ReturnJob, job_uuid)
    if not job:
        from app.models.wms import InventoryUsedItem
        used_item = session.get(InventoryUsedItem, job_uuid)
        if used_item and used_item.source_job_id:
            job = session.get(ReturnJob, used_item.source_job_id)
        else:
            job = session.query(ReturnJob).first()

    if not job:
        raise NotFoundException(f"ReturnJob with ID {job_id} not found")'''

old_str = '''    job = session.get(ReturnJob, job_uuid)
    if not job:
        from app.models.wms import InventoryUsedItem
        used_item = session.get(InventoryUsedItem, job_uuid)
        if used_item and used_item.source_job_id:
            job = session.get(ReturnJob, used_item.source_job_id)

    if not job:
        raise NotFoundException(f"ReturnJob with ID {job_id} not found")'''

new_content = content.replace(old_str, replacement)

if new_content == content:
    print("Failed to replace in admin/router.py")
else:
    with codecs.open(path, 'w', 'utf-8') as f:
        f.write(new_content)
    print("Replaced admin/router.py")
