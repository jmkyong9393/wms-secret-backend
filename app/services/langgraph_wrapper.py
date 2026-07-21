from typing import Any, Dict

from langchain_core.messages import HumanMessage

# LangGraph Supervisor 파이프라인 호출 담당 Wrapper class
class LangGraphInspectionWrapper:
    # LangGraph Supervisor에 전달한 초기 WMSInspectionState 생성
    def build_initial_inspection_state(self, order_id: str, image_url: str) -> Dict[str, Any]:
        # 실제 값들은 각 Agent가 실행되면서 채워짐.

        return {
            "messages": [
                HumanMessage(
                    content=(
                        "다음 반품 도서 이미지를 AI 검수해주세요.\n"
                        f"order_id: {order_id}\n"
                        f"image_url: {image_url}"
                    )
                )
            ],

            #Vision Agent가 채울 값
            "is_mint": None,
            "defects": None,

            #Policy Agent가 채울 값
            "ubci_score": None,

            # Critic Agent가 채울 값
            "reason_code": None,
            "repair_directive": None,
            "revision_count": 0,

            # Human-In-The-Loop에서 사용할 값
            "human_feedback": None,

            # 최종 Report Agent가 채울 값
            "final_report" : None,
        }

    # LangGraph 최종 state를 기반으로 Worker가 사용할 decision 값으로 변환하는 함수
    def convert_state_to_decision(self, final_state: Dict[str, Any]) -> str:
        is_mint = final_state.get("is_mint")
        reason_code = final_state.get("reason_code")
        ubci_score = final_state.get("ubci_score")

        if is_mint is True:
            return "APPROVE"
        
        if reason_code == "OK" and ubci_score is not None and ubci_score>=70: # 문서상 B급 이상으로 되어있음. 수치 수정 필요
            return "APPROVE"
        return "REJECT"


    # LangGraph 최종 WMSInspectionState를 dict 형태로 변환
    def convert_final_state_to_worker_result(self, final_state: Dict[str,Any]) -> Dict[str,Any]:
        decision = self.convert_state_to_decision(final_state)

        return {
            "decision": decision,
            "ubci_score": final_state.get("ubci_score"),
            "final_report": final_state.get("final_report"),
            "agent_logs": {
                "is_mint": final_state.get("is_mint"),
                "defects": final_state.get("defects"),
                "reason_code": final_state.get("reason_code"),
                "repair_directive": final_state.get("repair_directive"),
                "revision_count": final_state.get("revision_count"),
                "human_feedback": final_state.get("human_feedback"),
            },
        }

    # Supervisor 실행하고, 최종 결과 dict로 반환. 뼈대만 작성
    def run_inspection(self, order_id: str, image_url: str)-> Dict[str,Any]:
        from app.ai.supervisor import app_graph, build_supervisor_graph

        graph = app_graph or build_supervisor_graph()

        initial_state = self.build_initial_inspection_state(
            order_id = order_id,
            image_url = image_url,
        )
        config = {
            "configurable" : {
                "thread_id": f"inspection-{order_id}"
            }
        }

        final_state = graph.invoke(
            initial_state,
            config=config,
        )

        return self.convert_final_state_to_worker_result(final_state)

