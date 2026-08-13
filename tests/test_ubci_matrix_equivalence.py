"""
UBCI 감점 매트릭스 SSOT 이관 동치 검증.

policy_agent 안에 리터럴로 박혀 있던 감점 수치를 app/core/ubci_matrix.py로 옮겼다.
이 테스트는 **이관 전 코드의 계산식을 그대로 복제**해 두고, SSOT 상수를 쓰는 현행
구현과 모든 구간에서 같은 값을 내는지 대조한다. 값이 하나라도 달라지면 매입가가
달라지므로 회귀를 여기서 잡는다.
"""
import pytest

from app.core import ubci_matrix as UM


# ── 이관 이전 원본 계산식 (archive/2026-08-14_policy_ssot_stageB 기준) ──
def _legacy_scratch(ratio): return 2 if ratio < 5 else (5 if ratio < 15 else 10)
def _legacy_tear(ratio): return 5 if ratio < 5 else (10 if ratio < 15 else 15)
def _legacy_sticker(ratio): return 2 if ratio < 5 else (3 if ratio < 15 else 5)
def _legacy_crush(ratio): return 3 if ratio < 5 else (5 if ratio < 15 else 10)
def _legacy_spine(ratio): return 5 if ratio < 15 else 10
def _legacy_stain(ratio): return 5 if ratio < 5 else (10 if ratio < 15 else 20)
def _legacy_default(ratio): return 2 if ratio < 5 else (5 if ratio < 15 else 8)
def _legacy_wear_base(ratio): return 3 if ratio < 5 else (5 if ratio < 15 else 10)


# 구간 경계를 반드시 포함한다 - off-by-one은 경계에서만 드러난다.
RATIOS = [0, 0.1, 1, 4.9, 5, 5.1, 10, 14.9, 15, 15.1, 30, 80, 100]


@pytest.mark.parametrize("ratio", RATIOS)
@pytest.mark.parametrize("rule,legacy", [
    (UM.SCRATCH, _legacy_scratch),
    (UM.TEAR, _legacy_tear),
    (UM.STICKER, _legacy_sticker),
    (UM.CRUSH, _legacy_crush),
    (UM.SPINE_CRACK, _legacy_spine),
    (UM.STAIN, _legacy_stain),
    (UM.DEFAULT, _legacy_default),
    (UM.EDGE_WEAR, _legacy_wear_base),
])
def test_tiered_rules_match_legacy(rule, legacy, ratio):
    assert rule.tier_for(ratio) == legacy(ratio), f"{rule.label} @ ratio={ratio}"


@pytest.mark.parametrize("ratio", RATIOS)
@pytest.mark.parametrize("rule,expected", [
    (UM.STAMP, 15),
    (UM.SIGNATURE, 10),
    (UM.BINDING_LOOSE, 10),
    (UM.WORKBOOK_DOODLE, 15),
])
def test_flat_rules_are_area_independent(rule, expected, ratio):
    """고정 감점은 면적과 무관하게 같은 값이어야 한다."""
    assert rule.tier_for(ratio) == expected


@pytest.mark.parametrize("level,expected", [(1, 2), (2, 5), (3, 10)])
def test_discolor_level_matches_legacy(level, expected):
    assert UM.DISCOLOR_BY_LEVEL[level] == expected


@pytest.mark.parametrize("page_cnt,expected", [(0, 10), (1, 10), (5, 10), (6, 15), (100, 15)])
def test_doodle_page_threshold_matches_legacy(page_cnt, expected):
    """이관 전: 15 if page_cnt > 5 else 10"""
    got = UM.DOODLE.severe if page_cnt > UM.DOODLE_PAGE_THRESHOLD else UM.DOODLE.minor
    assert got == expected


@pytest.mark.parametrize("ratio,corners", [
    (0, 1), (3, 1), (3, 4), (10, 2), (20, 1), (20, 4), (50, 8),
])
def test_edge_wear_total_matches_legacy(ratio, corners):
    """이관 전: min(15, base + (spread-1)*2)"""
    legacy = min(15, _legacy_wear_base(ratio) + (max(1, corners) - 1) * 2)
    got = min(
        UM.EDGE_WEAR_CAP,
        UM.EDGE_WEAR.tier_for(ratio) + (max(1, corners) - 1) * UM.EDGE_WEAR_SPREAD_STEP,
    )
    assert got == legacy


@pytest.mark.parametrize("score,grade,decision", [
    (100, "S급 (MINT)", "APPROVE"),
    (95, "S급 (MINT)", "APPROVE"),
    (94, "A급 (GOOD)", "APPROVE"),
    (85, "A급 (GOOD)", "APPROVE"),
    (84, "B급 (NORMAL)", "APPROVE"),
    (65, "B급 (NORMAL)", "APPROVE"),
    (64, "REJECT C급 (폐기)", "REJECT"),
    (0, "REJECT C급 (폐기)", "REJECT"),
])
def test_grade_and_decision_match_legacy(score, grade, decision):
    """이관 전: S>=95, A>=85, B>=65, else REJECT / APPROVE if >=65"""
    assert UM.grade_for(score) == grade
    assert UM.decision_for(score) == decision


def test_text_overlap_exemptions_preserved():
    """면적·강도 구간에서 이미 심각도를 반영한 항목은 1.5배 가중치를 타지 않는다."""
    assert UM.STAIN.text_overlap_weighted is False
    assert UM.DISCOLOR.text_overlap_weighted is False
    assert UM.WORKBOOK_DOODLE.text_overlap_weighted is False
    # 반대로 아래 항목들은 가중치 대상이다 (이관 전 else 분기 전체가 그랬다)
    for rule in (UM.SCRATCH, UM.TEAR, UM.STICKER, UM.CRUSH,
                 UM.SPINE_CRACK, UM.BINDING_LOOSE, UM.SIGNATURE, UM.STAMP, UM.DEFAULT):
        assert rule.text_overlap_weighted is True, rule.label


def test_every_rule_carries_a_clause_reference():
    """모든 감점 규칙은 근거 조항을 가져야 한다 (정책서 제0조의1 ②)."""
    rules = [v for v in vars(UM).values() if isinstance(v, UM.DeductionRule)]
    assert len(rules) >= 12
    for rule in rules:
        assert rule.clause and rule.clause.startswith("제"), rule.label
