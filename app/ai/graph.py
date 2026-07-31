from typing import TypedDict, Annotated, List, Optional
from langgraph.graph.message import add_messages
from langgraph.graph import StateGraph, END, START
from langchain_core.messages import HumanMessage
from app.ai.vision_agent import VisionAgent
from app.ai.policy_agent import run_policy_node
from app.ai.critic_agent import run_critic_node
from app.ai.explainer_agent import ExplainerAgent
import os
import json
from PIL import Image
from datetime import datetime

class AgentState(TypedDict, total=False):
    """Role-based LLMOps 아키텍처가 적용된 LangGraph State"""
    # LLM 대화 기록 추적용
    messages: Annotated[List, add_messages]
    
    job_id: str
    image_paths: List[str]
    book_category: str
    
    # 1. Vision Agent의 출력값
    has_defect: Optional[bool]
    defect_description: Optional[str]
    defect_coordinates: Optional[List[dict]]
    
    # 2. Policy Agent의 출력값 (RAG 기반)
    matched_rule: Optional[str]
    ubci_grade: Optional[str]
    ubci_score: Optional[int]
    
    # 3. Critic Agent의 출력값 및 루프 제어 변수
    critique: Optional[str]
    needs_hitl: Optional[bool]
    retry_count: int

    # 4. Explainer Agent의 실시간 LLMOps 종합 소견서 출력값
    explainer_summary: Optional[str]

def run_vision_node(state: AgentState):
    print("--- [Node: Vision Agent] 가동 ---")
    vision = VisionAgent()
    image_paths = state.get("image_paths", [])
    report = vision.analyze_images(image_paths)
    
    # ==== 실험 데이터 로컬 로깅 (PIL 압축 & JSON 직렬화) ====
    try:
        job_id = state.get("job_id", f"LPN-UNKNOWN-{int(datetime.now().timestamp())}")
        experiment_dir = os.path.join(os.getcwd(), "experiment_data", job_id)
        os.makedirs(experiment_dir, exist_ok=True)
        
        saved_images = []
        for idx, img_path in enumerate(image_paths):
            if not os.path.exists(img_path):
                continue
            
            with Image.open(img_path) as img:
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                
                # 비율 유지 리사이징 (최대 1280px)
                img.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
                
                # 역할별 이름 매핑 (1번 정면, 2번 후면, 그 외 훼손부위)
                label = "front" if idx == 0 else "back" if idx == 1 else f"defect_{idx-1}"
                save_name = f"img_{idx}_{label}.jpg"
                save_path = os.path.join(experiment_dir, save_name)
                
                # Quality 80 JPEG 저장으로 용량 최적화
                img.save(save_path, "JPEG", quality=80)
                saved_images.append(save_name)
        
        result_data = {
            "lpn_barcode": job_id,
            "timestamp": datetime.now().isoformat(),
            "vision_result": {
                "has_defect": not report.is_clean,
                "defect_description": ", ".join([d.defect_type for d in report.defects]) if report.defects else "MINT",
                "defect_coordinates": [{**(d.bbox.model_dump() if hasattr(d.bbox, 'model_dump') else d.bbox.dict()), "image_index": getattr(d, 'image_index', 0), "label": getattr(d, 'defect_type', '결함')} for d in report.defects] if report.defects else []
            },
            "images": saved_images
        }
        
        with open(os.path.join(experiment_dir, "vlm_result.json"), "w", encoding="utf-8") as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)
            
        print(f"--- [Node: Vision Agent] 로컬 실험 데이터 축적 완료: {experiment_dir} ---")
    except Exception as e:
        print(f"--- [Node: Vision Agent] 실험 데이터 로깅 에러 발생: {e} ---")
    
    # LLM이 읽을 수 있도록 결과를 HumanMessage로 변환하여 저장
    msg_content = f"Vision 분석 결과: 훼손 감지={not report.is_clean}, 훼손 상세={', '.join([d.defect_type for d in report.defects]) if report.defects else '없음'}"
    return {
        "messages": [HumanMessage(content=msg_content)],
        "has_defect": not report.is_clean,
        "defect_description": ", ".join([d.defect_type for d in report.defects]) if report.defects else "MINT",
        "defect_coordinates": [{**(d.bbox.model_dump() if hasattr(d.bbox, 'model_dump') else d.bbox.dict()), "image_index": getattr(d, 'image_index', 0), "label": getattr(d, 'defect_type', '결함')} for d in report.defects] if report.defects else [],
        "retry_count": 0,
        "needs_hitl": False
    }

def hitl_node(state: AgentState):
    """최대 재시도 횟수를 초과했을 때 관리자 수동 검수로 빠지는 노드"""
    print("--- [Node: HITL] 관리자 수동 검수 (Human-in-the-loop) 큐 대기 ---")
    return {"needs_hitl": True}

def run_explainer_node(state: AgentState):
    print("--- [Node: Explainer Agent] 가동 (GPT-4o-mini 실시간 소견서 생성) ---")
    explainer = ExplainerAgent()
    
    title = "도서"
    lpn = state.get("job_id", "")
    defect_desc = state.get("defect_description", "MINT 깨끗함")
    score = state.get("ubci_score", 85)
    grade = state.get("ubci_grade", "GOOD")
    critique = state.get("critique", "APPROVED")
    
    summary = explainer.generate_explanation(
        title=title,
        lpn=lpn,
        defect_description=defect_desc,
        ubci_score=score,
        grade=grade,
        critic_status="검증 완료" if "APPROVED" in str(critique).upper() else "수동 검수 요청",
        confidence=99.2
    )
    return {"explainer_summary": summary}

def critic_router(state: AgentState):
    """Critic의 결과에 따라 조건부 루프를 제어하는 라우터"""
    critique = state.get("critique", "")
    retry_count = state.get("retry_count", 0)
    
    if "APPROVED" in str(critique).upper():
        print("--- [Router] Critic 판정: APPROVED. Explainer Agent 단계로 진입 ---")
        return "explainer_agent"
    else:
        if retry_count < 1:
            print("--- [Router] Critic 판정: REJECTED. Policy Agent 1회 재시도 (재생각 기회 부여) ---")
            return "policy_agent"
        else:
            print("--- [Router] 재시도 한도 초과. HITL 프로세스로 안전하게 전환 ---")
            return "hitl_node"

def build_wms_graph():
    """WMS 멀티 에이전트 LangGraph 컴파일"""
    workflow = StateGraph(AgentState)
    
    # 노드 추가
    workflow.add_node("vision_agent", run_vision_node)
    workflow.add_node("policy_agent", run_policy_node)
    workflow.add_node("critic_agent", run_critic_node)
    workflow.add_node("explainer_agent", run_explainer_node)
    workflow.add_node("hitl_node", hitl_node)
    
    # 엣지 연결 (조건부 엣지 포함)
    workflow.add_edge(START, "vision_agent")
    workflow.add_edge("vision_agent", "policy_agent")
    workflow.add_edge("policy_agent", "critic_agent")
    
    # Critic의 결과물에 따른 분기 (APPROVED / REJECTED)
    workflow.add_conditional_edges(
        "critic_agent",
        critic_router,
        {
            "explainer_agent": "explainer_agent",
            "policy_agent": "policy_agent",
            "hitl_node": "hitl_node"
        }
    )
    workflow.add_edge("explainer_agent", END)
    workflow.add_edge("hitl_node", END)
    
    app = workflow.compile()
    return app
