from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

# LLMOps 설계: 비판적이고 깐깐한 평가를 위해 temperature=0.0
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.0)

def run_critic_node(state):
    print("--- [Node: Critic Agent] 가동 ---")
    
    grade = state.get("ubci_grade", "알수없음")
    score = state.get("ubci_score", 0)
    rule = state.get("matched_rule", "알수없음")
    retry_count = state.get("retry_count", 0)
    
    # LLMOps 역할 프롬프트 패턴 적용
    sys_msg = """
    # 역할 : 너는 WMS Critic.
    # 지침 : 이전 대화 기록에 있는 'Vision 분석 결과' 내용과 
    방금 Policy Agent가 내린 등급 판정(등급, 점수, 규정 근거)이 논리적으로 완벽하게 일치하는지 평가해.
    
    기준:
    1. 판정이 합당하다면 반드시 'APPROVED' 라는 단어를 응답에 포함할 것.
    2. 훼손이 있는데 MINT를 주었거나, 규정 해석에 모순이 있다면 'REJECTED' 라는 단어를 포함하고, 
       Policy Agent가 다시 생각할 수 있도록 어떤 부분이 틀렸는지 논리적인 피드백을 제공할 것.
    """
    
    human_msg = f"# Policy Agent의 이번 턴 판정 결과:\n- 등급: {grade}\n- 점수: {score}\n- 근거: {rule}"
    
    # 히스토리에 시스템 지침 및 검토 대상 메시지 결합
    history_messages = state.get("messages", [])
    invoke_messages = history_messages + [SystemMessage(content=sys_msg), HumanMessage(content=human_msg)]
    
    response = llm.invoke(invoke_messages)
    
    # REJECTED를 반환했다면 재시도 카운트 1 증가
    new_retry_count = retry_count
    if "REJECTED" in response.content.upper():
        new_retry_count += 1
        
    return {
        "messages": [response],
        "critique": response.content,
        "retry_count": new_retry_count
    }
