import os
import json
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage

# LLMOps 설계: gpt-4o-mini, 낮은 temperature로 정해진 규정 준수
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.1)

def run_policy_node(state):
    print("--- [Node: Policy Agent] 가동 ---")
    
    # State에서 텍스트 히스토리 가져오기
    # Vision 결과 혹은 Critic의 반려 의견이 담겨 있음
    history_messages = state.get("messages", [])
    
    # RAG DB 조회 (소한민 파트)
    # 런타임 환경에서만 로드하여 성능 보호 및 임포트 에러 방지
    try:
        from app.core.rag_builder import OPENAI_EMBEDDING_MODEL
        from langchain_community.embeddings import OpenAIEmbeddings
        from langchain_community.vectorstores import Chroma
        
        db_path = os.path.join(os.getcwd(), "chroma_db")
        if os.path.exists(db_path):
            embeddings = OpenAIEmbeddings(model=OPENAI_EMBEDDING_MODEL)
            db = Chroma(persist_directory=db_path, embedding_function=embeddings)
            # 가장 최근 메시지(Vision 훼손 내용 혹은 Critic 반려 피드백) 기반으로 연관 문서 검색
            docs = db.similarity_search(history_messages[-1].content, k=2)
            rag_context = "\n".join([doc.page_content for doc in docs])
        else:
            rag_context = "RAG DB 파일이 없습니다. (DB 경로 없음)"
    except Exception as e:
        print(f"RAG DB 조회 실패 (Mock 진행): {e}")
        rag_context = "[MOCK 규정] 도서 모서리 훼손: NORMAL(60점), 양호: MINT(100점), 파손 심각: REJECT(0점)"

    # LLMOps 역할 프롬프트 패턴 적용
    sys_msg = f"""
    # 역할 : 너는 WMS Policy Agent.
    # 지침 : 이전 메시지의 대화 기록(Vision 분석 결과 또는 Critic의 피드백)과 
    아래 [RAG Context]에 제공된 공식 WMS 반품 규정을 대조하여, 
    정확한 UBCI 등급(MINT, GOOD, NORMAL, REJECT)과 점수를 계산하고 적용된 규정을 요약해.
    
    [RAG Context]
    {rag_context}
    
    반드시 아래의 순수 JSON 문자열 형식으로만 응답할 것 (백틱 없이):
    {{"ubci_grade": "등급", "ubci_score": 100, "matched_rule": "규정 근거 설명"}}
    """
    
    # 컨텍스트 조립 후 LLM 호출
    invoke_messages = history_messages + [SystemMessage(content=sys_msg)]
    response = llm.invoke(invoke_messages)
    
    # JSON 파싱 방어 코드
    try:
        # LLM이 markdown 백틱을 넣을 가능성에 대비
        result_str = response.content.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(result_str)
        grade = parsed.get("ubci_grade", "NORMAL")
        score = parsed.get("ubci_score", 60)
        rule = parsed.get("matched_rule", "규정 파싱 실패")
    except Exception as e:
        print(f"Policy JSON 파싱 오류: {e}")
        grade = "NORMAL"
        score = 60
        rule = f"규정 파싱 실패 (Raw: {response.content})"
        
    return {
        "messages": [response],
        "ubci_grade": grade,
        "ubci_score": score,
        "matched_rule": rule
    }
