import os
import base64
import json
import langchain

# .env 수동 로드
with open(".env", encoding="utf-8") as f:
    for line in f:
        if line.strip() and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            os.environ[k] = v.strip('"\'')

from langchain_core.messages import HumanMessage
from app.ai.supervisor import app_graph

def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

if __name__ == "__main__":
    print("=== 멀티모달(Vision) 테스트 시작 ===")
    
    # 조장님이 방금 올리신 자바 프로그래밍 책 이미지 경로
    image_path = r"C:\Users\jmkyo\.gemini\antigravity\brain\5206581e-7257-4c99-a5d3-eafe625d7fa0\media__1783795804731.jpg"
    base64_image = encode_image(image_path)
    
    # LangChain에 전달할 멀티모달 메시지 포맷
    test_message = HumanMessage(
        content=[
            {"type": "text", "text": "첨부된 책 사진의 상태를 정밀하게 판독해주세요."},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
        ]
    )
    
    initial_state = {
        "messages": [test_message],
        "revision_count": 0,
    }
    
    config = {"configurable": {"thread_id": "test_vision_real_image"}}
    
    print("그래프 실행 중 (GPT-4o Vision API 호출)...")
    result = app_graph.invoke(initial_state, config=config)
    
    print("\n=== 최종 상태 결과 ===")
    print(f"Is Mint: {result.get('is_mint')}")
    print(f"Defects: {result.get('defects')}")
    print(f"UBCI Score: {result.get('ubci_score')}")
    print(f"Reason Code: {result.get('reason_code')}")
    
    # 결과를 파일로 저장
    output_data = {
        "is_mint": result.get("is_mint"),
        "defects": result.get("defects"),
        "ubci_score": result.get("ubci_score"),
        "reason_code": result.get("reason_code"),
        "final_report": result.get("final_report"),
    }
    with open("test_result.json", "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=4)
    print("\n[+] 결과를 test_result.json 파일로 저장했습니다.")
    
    print("\n=== 대화 기록 ===")
    
    # 대화 기록을 파일로도 저장
    with open("agent_conversation_log.txt", "w", encoding="utf-8") as log_file:
        log_file.write("=== 멀티 에이전트 동작 로그 ===\n\n")
        for m in result.get("messages", []):
            if isinstance(m.content, list):
                print("[이미지 전송 완료]")
                log_file.write("[HumanMessage] 이미지 및 분석 요청 전송 완료\n")
            else:
                print(m.content)
                log_file.write(f"{m.content}\n")
    
    print("\n[+] 에이전트 티키타카 로그를 agent_conversation_log.txt 파일로 저장했습니다.")
