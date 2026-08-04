import re
import codecs

path = 'app/domains/admin/router.py'
with codecs.open(path, 'r', 'utf-8') as f:
    content = f.read()

pattern = re.compile(r'@router\.post\("/\{job_id\}/re-inspect"\)\ndef trigger_ai_reinspection.*?return \{\n        "status": "success",\n        "job_id": job_id,.*?\n    \}', re.DOTALL)

replacement = '''@router.post("/{job_id}/re-inspect")
def trigger_ai_reinspection(job_id: str, session: Session = Depends(get_db)):
    """
    [Master AI Re-inspection Engine]
    Actual LangGraph execution for the re-inspection triggered by HITL.
    """
    try:
        job_uuid = UUID(job_id)
    except ValueError:
        raise BadRequestException(f"Invalid job_id UUID: {job_id}")

    job = session.get(ReturnJob, job_uuid)
    if not job:
        raise NotFoundException(f"ReturnJob with ID {job_id} not found")

    image_urls = job.image_urls or []
    if not image_urls:
        raise BadRequestException("No images found for this job.")

    from app.models.wms import Book
    book_obj = session.get(Book, job.book_id) if job.book_id else None
    book_category = "NORMAL"
    if book_obj and book_obj.title:
        title = book_obj.title
        if any(k in title for k in ["수험서", "문제집", "기출", "자격검정", "실전문제", "학습", "교재", "AIVLE", "SQL"]):
            book_category = "WORKBOOK"

    try:
        from app.ai.graph import build_wms_graph
        app = build_wms_graph()
        
        initial_state = {
            "job_id": job_id,
            "image_paths": image_urls,
            "book_category": book_category,
            "needs_hitl": False
        }
        
        final_state = app.invoke(initial_state)
        
        grade = "MINT" if final_state.get("is_mint", False) else "NORMAL"
        ubci_score = final_state.get("ubci_score", 75)
        
        from app.ai.explainer_agent import ExplainerAgent
        explainer = ExplainerAgent()
        explainer_summary = explainer.generate_explanation(
            title=book_obj.title if book_obj else "알 수 없는 도서",
            lpn=job.agent_logs.get("lpn_barcode") if job.agent_logs else "LPN-PENDING",
            defect_description=str(final_state.get("defects", [])),
            ubci_score=ubci_score,
            grade=grade,
            critic_status=final_state.get("reason_code", "OK")
        )

        job.status = JobStatusEnum.INSPECTED
        job.ubci_score = ubci_score
        job.retry_count += 1
        
        if not job.agent_logs:
            job.agent_logs = {}
            
        job.agent_logs["vision_text"] = f"👁️ [Vision Agent] VLM 추론 완료. 결함: {len(final_state.get('defects', []))}개 발견"
        job.agent_logs["policy_text"] = f"📜 [Policy Agent] 감가상각 룰 적용 ➔ UBCI {ubci_score}점 확정"
        job.agent_logs["critic_text"] = f"🛡️ [Critic Agent] 교차 검증: {final_state.get('reason_code', 'OK')}"
        job.agent_logs["explainer_summary"] = explainer_summary
        job.agent_logs["suggested_grade"] = grade
        job.agent_logs["suggested_decision"] = "APPROVE" if grade in ["MINT", "GOOD"] else "REJECT"
        job.agent_logs["defect_coordinates"] = final_state.get("defects", [])
        
        session.add(job)
        session.commit()
        
        return {
            "status": "success",
            "job_id": job_id,
            "final_grade": grade,
            "ubci_score": ubci_score,
            "agent_logs": job.agent_logs
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"AI Graph Execution Failed: {str(e)}")'''

new_content = re.sub(pattern, replacement, content)
if new_content == content:
    print("Regex failed to match! Dumping a portion of the file for debug.")
    idx = content.find('@router.post("/{job_id}/re-inspect")')
    print(content[idx:idx+500])
else:
    with codecs.open(path, 'w', 'utf-8') as f:
        f.write(new_content)
    print("Replaced successfully")
