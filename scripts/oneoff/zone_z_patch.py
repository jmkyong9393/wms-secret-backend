import os
import re

service_file = 'app/domains/inventory/service.py'
with open(service_file, 'r', encoding='utf-8') as f:
    s_content = f.read()

# Fix assign_rack_location_after_inspection to use final_status instead of hardcoded "IN_STOCK"
# and override rec_zone to "Z" if final_status == "HITL_PENDING"
old_algo = '''    # 등급/카테고리/판형 3차원 알고리즘으로 Zone A/B/C/D/E 랙위치 자동 결정
    rec_zone, rec_rack, rec_shelf = recommend_optimal_warehouse_zone(
        grade=final_grade,
        category=book.category_type if book else "IT/컴퓨터",
        base_price=book.base_price if book else 20000.0,
        standard_size=book.standard_size if book else None
    )
    location = get_or_create_location(db, zone=rec_zone, rack=rec_rack, shelf=rec_shelf)'''

new_algo = '''    # 등급/카테고리/판형 3차원 알고리즘으로 Zone A/B/C/D/E 랙위치 자동 결정
    rec_zone, rec_rack, rec_shelf = recommend_optimal_warehouse_zone(
        grade=final_grade,
        category=book.category_type if book else "IT/컴퓨터",
        base_price=book.base_price if book else 20000.0,
        standard_size=book.standard_size if book else None
    )
    
    if final_status == "HITL_PENDING":
        rec_zone = "Z"
        rec_rack = "1"
        rec_shelf = "1"
        
    location = get_or_create_location(db, zone=rec_zone, rack=rec_rack, shelf=rec_shelf)'''

s_content = s_content.replace(old_algo, new_algo)

s_content = re.sub(r'item_status="IN_STOCK",', 'item_status=final_status,', s_content)
s_content = re.sub(r'item\.item_status = "IN_STOCK"', 'item.item_status = final_status', s_content)

with open(service_file, 'w', encoding='utf-8') as f:
    f.write(s_content)


router_file = 'app/domains/inbound/router.py'
with open(router_file, 'r', encoding='utf-8') as f:
    r_content = f.read()

old_router = '''                    # 2. InventoryUsedItem DB 생성 및 창고(Zone) 배치
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

new_router = '''                    # 2. InventoryUsedItem DB 생성 및 창고(Zone) 배치
                    # HITL_REQUIRED는 Enum 제약 방어를 위해 NORMAL 등급으로 위장하되, 상태를 HITL_PENDING으로 전달하여 Zone Z로 자동 배정
                    temp_grade = "NORMAL" if grade == "HITL_REQUIRED" else grade
                    temp_status = "HITL_PENDING" if grade == "HITL_REQUIRED" else ("REJECTED" if grade == "REJECT" else "IN_STOCK")
                    item = assign_rack_location_after_inspection(
                        db=session, 
                        lpn_barcode=lpn_code, 
                        final_grade=temp_grade,
                        final_status=temp_status,
                        book_id=book_obj.id if book_obj else None,
                        ubci_score=ubci_score,
                        source_job_id=str(return_job_db.id)
                    )
                    session.commit()'''

r_content = r_content.replace(old_router, new_router)

with open(router_file, 'w', encoding='utf-8') as f:
    f.write(r_content)
