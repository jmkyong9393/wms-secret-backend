import codecs

path = 'app/domains/inventory/router.py'
with codecs.open(path, 'r', 'utf-8') as f:
    content = f.read()

replacement = '''    if not item:
        try:
            parsed_id = UUID(item_id)
            job = db.query(ReturnJob).filter(ReturnJob.id == parsed_id).first()
        except Exception:
            job = db.query(ReturnJob).filter(ReturnJob.lpn_barcode == item_id).first()
            
        if not job:
            job = db.query(ReturnJob).filter(ReturnJob.lpn_barcode == item_id).first()

        if job:
            book = db.query(Book).filter(Book.id == job.book_id).first()
            return {
                "id": str(job.id),
                "lpn_barcode": job.agent_logs.get("lpn_barcode") if job.agent_logs else (job.lpn_barcode or "LPN-PENDING"),
                "book": {
                    "title": book.title if book else "알 수 없는 도서",
                    "author": book.author if book else "-",
                    "publisher": book.publisher if book else "-",
                    "isbn": book.isbn if book else "-",
                    "base_price": book.base_price if book else 0.0,
                    "cover_image_url": book.cover_image_url if book else "",
                },
                "grade": job.agent_logs.get("suggested_grade") if job.agent_logs and job.agent_logs.get("suggested_grade") else "NORMAL",
                "ubci_score": job.ubci_score or 75,
                "zone": "Zone Z (임시적재)",
                "quantity": 1,
                "worker_id": "HITL 대기",
                "date": to_kst_str(job.created_at),
                "image_urls": job.image_urls or [],
                "agent_logs": job.agent_logs or {}
            }
        
        item = db.query(InventoryUsedItem).first()'''

content = content.replace('    if not item:\n        item = db.query(InventoryUsedItem).first()', replacement)

with codecs.open(path, 'w', 'utf-8') as f:
    f.write(content)
