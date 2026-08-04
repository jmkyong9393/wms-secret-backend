import os
import re

file_path = 'app/domains/inbound/router.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Fix now_kst() imports and usages
if 'from app.models.wms import now_kst' not in content:
    content = content.replace('from app.models.wms import ReturnJob', 'from app.models.wms import ReturnJob, now_kst')

content = re.sub(r'datetime\.utcnow\(\)', 'now_kst()', content)
content = re.sub(r'datetime\.datetime\.utcnow\(\)', 'now_kst()', content)
content = re.sub(r'datetime\.datetime\.now\(.*?\)', 'now_kst()', content)

# 2. Fix the DB saving block in real_ai_worker
# I will use a regex to match the exact block and replace it since the exact whitespace might differ.

old_block_pattern = r'# \[실제 DB 저장 연동\].*?assign_rack_location_after_inspection\(session, lpn_code, grade\)'
new_block = '''# [실제 DB 저장 연동] AI 검수 최종 판정 결과를 InventoryUsedItem 과 ReturnJob DB 테이블에 남기기
        if lpn_code:
            try:
                from app.db.session import engine
                from app.domains.inventory.service import assign_rack_location_after_inspection
                from app.models.wms import ReturnJob, JobStatusEnum
                isbn = book_meta.get("isbn")
                
                with Session(engine) as session:
                    book_obj = None
                    if isbn:
                        book_obj = session.exec(select(Book).where(Book.isbn == isbn)).first()
                    
                    # 1. ReturnJob DB 생성 (관리자 HITL 대기보드 노출용)
                    return_job_db = ReturnJob(
                        book_id=book_obj.id if book_obj else None,
                        image_urls=image_paths,
                        status=JobStatusEnum.HITL_REQUIRED.value if grade == "HITL_REQUIRED" else JobStatusEnum.APPROVED.value,
                        ubci_score=ubci_score,
                        agent_logs={
                            "defect_coordinates": defect_coordinates,
                            "defect_description": defect_description,
                            "lpn_barcode": lpn_code,
                            "final_grade": grade
                        }
                    )
                    session.add(return_job_db)
                    session.commit()
                    session.refresh(return_job_db)

                    # 2. InventoryUsedItem DB 생성 및 창고(Zone) 배치
                    # HITL_REQUIRED 등급은 DB의 NOT NULL 제약조건을 만족하기 위해 임시로 NORMAL 구역에 배정
                    temp_grade = "NORMAL" if grade == "HITL_REQUIRED" else grade
                    item = assign_rack_location_after_inspection(
                        db=session, 
                        lpn_barcode=lpn_code, 
                        final_grade=temp_grade, 
                        book_id=book_obj.id if book_obj else None,
                        ubci_score=ubci_score,
                        source_job_id=str(return_job_db.id)
                    )
                    
                    # 3. 상태(item_status) 최종 업데이트
                    if grade == "HITL_REQUIRED":
                        item.item_status = "HITL_PENDING"
                    elif grade == "REJECT":
                        item.item_status = "REJECTED"
                    else:
                        item.item_status = "IN_STOCK"
                        
                    session.add(item)
                    session.commit()'''

content = re.sub(old_block_pattern, new_block, content, flags=re.DOTALL)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Success!" if new_block in content else "Failed to find the target block.")
