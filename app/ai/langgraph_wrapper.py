from typing import Any, Dict, List

from langchain_core.messages import HumanMessage

# LangGraph Supervisor 파이프라인 호출 담당 Wrapper class
class LangGraphInspectionWrapper:
    # LangGraph Supervisor에 전달한 초기 WMSInspectionState 생성
    def build_initial_inspection_state(
        self,
        order_id: str,
        image_urls: List[str],
        display_image_urls: List[str] = None,
        book_title: str = "",
    ) -> Dict[str, Any]:
        # 실제 값들은 각 Agent가 실행되면서 채워짐.
        # 이미지 자체는 messages 텍스트에 URL 문자열로 embed하지 않는다 - Vision Agent가
        # image_paths를 직접 읽어 멀티모달 메시지(base64 image_url content block)를 구성한다.

        return {
            "messages": [
                HumanMessage(
                    content=f"다음 반품 도서(order_id: {order_id})를 AI 검수해주세요. 이미지 {len(image_urls)}장 첨부됨."
                )
            ],
            "image_paths": image_urls,
            "display_image_urls": display_image_urls or image_urls,
            # Policy Agent의 수험서 -15점 단일 Cap 판정에 실제로 쓰이는 값 (기존에는 미전달로 항상 빈 문자열)
            "book_title": book_title,

            #Vision Agent가 채울 값
            "is_mint": None,
            "defects": None,
            "special_notes": None,

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

            # 실행 노드 추적 (operator.add 리듀서 - 초기값은 반드시 빈 리스트)
            "executed_agents": [],
        }

    # LangGraph 최종 state를 기반으로 Worker가 사용할 decision 값으로 변환하는 함수
    def convert_state_to_decision(self, final_state: Dict[str, Any]) -> str:
        is_mint = final_state.get("is_mint")
        reason_code = final_state.get("reason_code")
        ubci_score = final_state.get("ubci_score")

        # [수정 이력] human_node가 그래프를 조기 종료시키는 HITL 이관 케이스를 여기서 먼저
        # 걸러내지 않으면, 아래 로직이 reason_code != "OK"라는 이유만으로 무조건 REJECT를
        # 반환해버려서 - 사람 검토를 기다려야 할 애매한 건이 사람 개입 없이 자동 반려되고
        # 있었다 (HITL_REQUIRED 상태 자체가 파이프라인에서 한 번도 만들어지지 않던 원인).
        #
        # Supervisor의 지휘 결정(supervisor_decision)을 1순위 신호로 삼는다 - 지휘 책임이
        # Supervisor에 있으므로, 그 결정이 하위 노드의 reason_code보다 우선한다.
        if final_state.get("supervisor_decision") == "ESCALATE_HUMAN" or reason_code == "AWAITING_HUMAN_REVIEW":
            return "HITL"

        if is_mint is True:
            return "APPROVE"

        if reason_code == "OK" and ubci_score is not None and ubci_score>=70: # 문서상 B급 이상으로 되어있음. 수치 수정 필요
            return "APPROVE"
        return "REJECT"


    # LangGraph 최종 WMSInspectionState를 dict 형태로 변환
    def convert_final_state_to_worker_result(self, final_state: Dict[str,Any]) -> Dict[str,Any]:
        from app.models.wms import ubci_grade_from_score

        decision = self.convert_state_to_decision(final_state)
        ubci_score = final_state.get("ubci_score")
        final_grade = "MINT" if final_state.get("is_mint") else ubci_grade_from_score(ubci_score)

        defects = final_state.get("defects") or []
        primary_reason_code = (
            defects[0].get("type") if defects and isinstance(defects[0], dict) else None
        )

        executed = final_state.get("executed_agents") or []

        return {
            "decision": decision,
            "ubci_score": ubci_score,
            "final_grade": final_grade,
            "final_report": final_state.get("final_report"),
            "auto_refund_eligible": bool(final_state.get("auto_refund_eligible")),
            "agent_logs": {
                "is_mint": final_state.get("is_mint"),
                "defects": defects,
                "special_notes": final_state.get("special_notes"),
                "reason_code": final_state.get("reason_code"),
                "repair_directive": final_state.get("repair_directive"),
                "revision_count": final_state.get("revision_count"),
                "human_feedback": final_state.get("human_feedback"),
                # Supervisor의 종합 판단 근거 - HITL 관리자 화면에서 "AI가 왜 이 건을
                # 사람에게 넘겼는가"를 그대로 보여주기 위해 함께 저장한다.
                "supervisor_decision": final_state.get("supervisor_decision"),
                "supervisor_rationale": final_state.get("supervisor_rationale"),
                # HITL 관리자 화면(/admin/hitl)의 결정/등급/사유 드롭다운 초기값으로 쓰인다
                # (features/hitl 프론트가 이미 이 키들을 기대하고 있었지만 지금까지 아무도
                # 채워준 적이 없었다).
                "suggested_grade": final_grade,
                "primary_reason_code": primary_reason_code,

                # --- 이하: 프론트 "Explainer Agent 실시간 진단 기록" 패널의 실제 데이터 소스 ---
                # [수정 이력] 이 키들이 DB에 저장된 적이 없어서, 프론트가 vision_text/policy_text/
                # critic_text/explainer_summary를 못 찾고 전부 자체 문자열 보간으로 지어낸 뒤
                # "PostgreSQL DB Verified" 뱃지를 붙이고 있었다. 이제 각 Agent가 실제로 산출한
                # 서술만 저장하며, 실행되지 않은 노드의 키는 아예 채우지 않는다(None).
                "detector_text": final_state.get("detector_text"),
                # 감점 근거 조항 (RAG grounding). 보증서/상세화면이 "이 감점의 근거는
                # 어느 조항인가"를 출처와 함께 제시할 수 있게 한다.
                "deduction_basis": final_state.get("deduction_basis") or [],
                "vision_text": final_state.get("vision_text"),
                "policy_text": final_state.get("policy_text"),
                "critic_text": final_state.get("critic_text"),
                "report_text": final_state.get("report_text"),
                # 고객 공개용 보증서 본문 (Report Agent 생성물)
                "certificate": final_state.get("certificate"),
                # 실제 실행된 노드 목록. 재검수 루프가 돌면 같은 노드가 여러 번 들어간다.
                # 프론트가 "몇 개 돌았다"를 지어내지 않고 이 값만 렌더한다.
                "executed_agents": executed,
                # WBF YOLO 앙상블 사전탐지 후보. VLM 판독이 실패해 HITL로 넘어간 경우
                # 관리자가 육안 판단할 때 참고할 유일한 기계 증거이므로 함께 보존한다.
                "yolo_candidates": final_state.get("yolo_candidates") or [],
                "vision_failed": bool(final_state.get("vision_failed")),
                # MINT 무결점 확정 건의 자동 매입/환불 대상 여부.
                # (예전 Fast-track 분기가 담당하던 비즈니스 기능을 플래그로 대체)
                "auto_refund_eligible": bool(final_state.get("auto_refund_eligible")),
                "retry_count": final_state.get("revision_count") or 0,
                # 프론트 BBox 오버레이가 바로 쓸 수 있는 이미지별 정규화 좌표
                "defect_coordinates": self.build_defect_coordinates(
                    defects, final_state.get("display_image_urls") or []
                ),
            },
        }

    # 이미지 인덱스별 BBox 묶음으로 정규화
    @staticmethod
    def build_defect_coordinates(defects: List[Dict[str, Any]], display_image_urls: List[str]) -> List[Dict[str, Any]]:
        """
        Vision Agent가 낸 평면 defects 배열(각 원소에 image_index와 bbox가 들어있음)을
        프론트 오버레이가 기대하는 이미지별 묶음 형태로 변환한다.

        [수정 이력] 프론트 resolveDefectCoordinates()는 agent_logs.defect_coordinates에
        {image_index, bboxes[]} 형태가 있을 것으로 기대했지만, 파이프라인은 defects[] 평면
        배열만 저장했다. 키 이름과 구조가 모두 달라 조회에 100% 실패했고, 프론트는 매번
        하드코딩된 목업 BBox로 폴백했다 - UBCI 100점 MINT 도서에 존재하지도 않는
        "모서리 눌림" 결함이 그려지던 원인. 변환 책임을 백엔드로 옮겨 단일 소스로 만든다.
        """
        from app.ai.agents import DEFECT_TRANSLATION_MAP

        grouped: Dict[int, Dict[str, Any]] = {}
        for d in defects:
            if not isinstance(d, dict):
                continue
            bbox = d.get("bbox")
            if not isinstance(bbox, dict):
                # BBox 좌표가 없는 결함은 그릴 수 없다 - 조용히 건너뛴다(가짜 좌표 생성 금지).
                continue

            idx = int(d.get("image_index") or 0)
            dtype = str(d.get("type") or "")
            entry = grouped.setdefault(
                idx,
                {
                    "image_index": idx,
                    "image_url": display_image_urls[idx] if idx < len(display_image_urls) else None,
                    "bboxes": [],
                },
            )
            entry["bboxes"].append({
                "xmin": bbox.get("xmin"),
                "ymin": bbox.get("ymin"),
                "xmax": bbox.get("xmax"),
                "ymax": bbox.get("ymax"),
                # Vision Agent 프롬프트가 0~1000 상대좌표를 요구하므로 스케일을 명시해
                # 프론트가 좌표계를 추측(heuristic)하지 않게 한다.
                "coord_space": 1000,
                "type": dtype,
                "label": DEFECT_TRANSLATION_MAP.get(dtype, dtype or "상태 결함"),
                "confidence": d.get("confidence"),
                "deduction": d.get("preliminary_deduction"),
            })

        return [grouped[k] for k in sorted(grouped.keys())]

    # Supervisor 실행하고, 최종 결과 dict로 반환.
    def run_inspection(
        self,
        return_job_id: str,
        order_id: str,
        image_urls: List[str],
        display_image_urls: List[str] = None,
        book_title: str = "",
    ) -> Dict[str, Any]:
        from app.ai.supervisor import app_graph, build_supervisor_graph

        graph = app_graph or build_supervisor_graph()

        initial_state = self.build_initial_inspection_state(
            order_id = order_id,
            image_urls = image_urls,
            display_image_urls = display_image_urls,
            book_title = book_title,
        )
        config = {
            "configurable" : {
                # return_job_id(항상 고유)로 키를 잡는다. order_id는 None이거나 여러 검수 건에서
                # 재사용될 수 있어 MemorySaver 체크포인트가 충돌할 위험이 있다.
                "thread_id": f"inspection-{return_job_id}"
            }
        }

        final_state = graph.invoke(
            initial_state,
            config=config,
        )

        return self.convert_final_state_to_worker_result(final_state)

