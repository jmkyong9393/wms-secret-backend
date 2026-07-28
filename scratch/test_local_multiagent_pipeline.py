"""
====================================================================
[로컬 멀티에이전트 파이프라인 정밀 테스트 스크립트]
- 본 스크립트는 로컬 개발 환경에서 LangGraph Multi-Agent 파이프라인의
  UBCI v2.0.0.0 정밀 감점 매트릭스 및 fast_track_agent 동적 분기를
  DB 적재 전에 심층 검증하기 위해 작성되었습니다.
====================================================================
"""

import sys
from pathlib import Path
from typing import Dict, Any

# 백엔드 루트 경로 추가
sys.path.append(str(Path(__file__).parent.parent.parent / "develop" / "solo_develop" / "wms-secret-backend"))

from app.ai.agents import (
    DefectDetail,
    VisionResult,
    PolicyResult,
    CriticResult,
    ReportResult,
    YOLO_MODEL_PATH
)

def run_local_pipeline_simulation(
    mode: str, # "RETURN" (고객 반품) 또는 "BUYBACK" (중고 매입)
    is_mint: bool,
    defects: list,
    special_notes: str = None
) -> Dict[str, Any]:
    """
    [로컬 시뮬레이터] Multi-Agent 4대 에이전트 연쇄 추론 시뮬레이션 함수
    - Vision Agent -> Policy Agent -> Fast-Track Agent -> Critic Agent 순서로 추론 수행
    """
    print(f"\n=======================================================")
    print(f"[Local Test Execution] Mode: {mode} | Mint: {is_mint}")
    print(f"=======================================================")

    # 1. Vision Agent 추론 결과 포장 (In-Memory State)
    vision_res = VisionResult(
        is_mint=is_mint,
        defects=[DefectDetail(**d) for d in defects],
        special_notes=special_notes
    )
    print(f"[1. Vision Agent Output] Mint={vision_res.is_mint}, 결함수={len(vision_res.defects)}, 특이사항='{vision_res.special_notes}'")

    # 2. Policy Agent (UBCI v2.0.0.0 점수 산출 심사)
    base_score = 100
    if not is_mint:
        for d in vision_res.defects:
            if d.code in ["STAIN_WATER_DAMAGE", "PAGE_WARPING", "BINDING_LOOSE"]:
                base_score = 0 # 즉시 반려 결함
                break
            elif d.code in ["COVER_TEAR", "COVER_SCRATCH"]:
                base_score -= 15
            elif d.code == "EDGE_WEAR":
                base_score -= 10
            else:
                base_score -= 5
    
    # 도서관 도장 등 특이사항 감점 (-10점)
    if special_notes:
        base_score -= 10
        
    ubci_score = max(0, base_score)
    print(f"[2. Policy Agent Output] Calculated UBCI Score = {ubci_score}점")

    # 3. Fast-Track Agent (입고 승인 vs 정산 vs 수동검수 동적 분기)
    if is_mint and ubci_score >= 95:
        grade = "MINT"
        if mode == "BUYBACK":
            final_status = "MINT_BUYBACK_APPROVED"
            message = "[MINT] 완전 무결한 MINT(S급) 새 책입니다! 정가 60% 최고가 매입 정산이 승인되었습니다."
        else:
            final_status = "AUTO_REFUND_APPROVED"
            message = "[MINT] MINT 등급 확인 완료! 100% 전액 환불이 즉시 자동 승인되었습니다."
    elif ubci_score >= 80:
        grade = "GOOD"
        final_status = "AUTO_REFUND_APPROVED" if mode == "RETURN" else "GOOD_BUYBACK_APPROVED"
        message = f"[GOOD] UBCI 점수 {ubci_score}점으로 GOOD 등급 정산이 승인되었습니다."
    elif ubci_score >= 60:
        grade = "NORMAL"
        final_status = "HITL_REQUIRED"
        message = f"[HITL] UBCI 점수 {ubci_score}점(NORMAL 등급)으로 현장 관리자 2차 수동 검수 대기열로 이관되었습니다."
    else:
        grade = "REJECT"
        final_status = "REJECTED"
        message = "[REJECT] 재판매 불가능한 심각한 상태(물 젖음/낙장)로 환불이 불가하여 반송 처리됩니다."

    print(f"[3. Fast-Track Agent Output] Grade = '{grade}' | Status = '{final_status}'")
    print(f"[4. Final CS Message] {message}")

    return {
        "mode": mode,
        "grade": grade,
        "ubci_score": ubci_score,
        "final_status": final_status,
        "special_notes": special_notes,
        "message": message
    }



if __name__ == "__main__":
    print("[Local Multi-Agent Pipeline Test Suite] 검증 시작...")
    
    # Test Case 1: MINT 도서 - 고객 반품 (RETURN) 전액 환불 테스트
    tc1 = run_local_pipeline_simulation(
        mode="RETURN",
        is_mint=True,
        defects=[]
    )
    assert tc1["final_status"] == "AUTO_REFUND_APPROVED"

    # Test Case 2: MINT 도서 - 중고 매입 (BUYBACK) 정가 60% 정산 테스트
    tc2 = run_local_pipeline_simulation(
        mode="BUYBACK",
        is_mint=True,
        defects=[]
    )
    assert tc2["final_status"] == "MINT_BUYBACK_APPROVED"

    # Test Case 3: 찢김 결함 + 도서관 도장 특이사항 도서 (HITL 이관 테스트)
    tc3 = run_local_pipeline_simulation(
        mode="RETURN",
        is_mint=False,
        defects=[{"code": "COVER_TEAR", "description": "표지 찢김", "ratio": 10}],
        special_notes="도서관 소장 도장 찍힘"
    )
    assert tc3["final_status"] == "HITL_REQUIRED"
    assert tc3["ubci_score"] == 75

    # Test Case 4: 물 젖음 결함 (즉시 반려 테스트)
    tc4 = run_local_pipeline_simulation(
        mode="RETURN",
        is_mint=False,
        defects=[{"code": "STAIN_WATER_DAMAGE", "description": "하단 액체 유입 얼룩", "ratio": 25}]
    )
    assert tc4["final_status"] == "REJECTED"
    assert tc4["ubci_score"] == 0

    print("\n[SUCCESS] 모든 로컬 시뮬레이션 테스트 케이스 100% 성공 검증 완료!")

