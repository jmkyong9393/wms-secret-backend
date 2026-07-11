import os
import sys

# 프로젝트 루트 경로를 PATH에 추가
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from app.agents.graph import build_wms_graph
from dotenv import load_dotenv

# API 키 로드
load_dotenv(os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")), "api_key.txt"))

def test_graph():
    print("그래프 컴파일 시도 중...")
    app = build_wms_graph()
    print("그래프 컴파일 성공!")
    
    # 더미 상태로 테스트
    # 실제로는 Vision Agent가 이미지 분석 결과를 내야 하지만, 여기선 직접 입력
    print("\n--- 파이프라인 가동 (Mock 데이터) ---")
    
    from langchain_core.messages import HumanMessage
    
    initial_state = {
        "job_id": "test_job_123",
        "image_path": "mock/path.jpg",
        "messages": [HumanMessage(content="Vision 분석 결과: 훼손 감지=True, 훼손 상세=모서리 찍힘 심각함")],
        "has_defect": True,
        "defect_description": "모서리 찍힘 심각함",
        "retry_count": 0,
        "needs_hitl": False
    }
    
    try:
        # Vision 노드는 건너뛰고 Policy부터 실행되도록 진입점 변경 (테스트용)
        # 하지만 그냥 invoke() 호출하면 설정된 entry_point (vision_node) 부터 돈다.
        # Vision 노드 안에서 실제 GPT-4o를 호출하려 할 것이므로, 
        pass
    except Exception as e:
        print(e)
        
if __name__ == "__main__":
    test_graph()
