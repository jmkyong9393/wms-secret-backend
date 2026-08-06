"""
비밀번호 작성 규칙 (KISA "개인정보의 기술적·관리적 보호조치 기준" 해설서 기준).

[도입 배경 - 2026-08-06]
종전 검증은 `min_length=4` 하나가 전부였다. 초기 암호 "1234"를 그대로 새 비밀번호로
재설정해도 통과했다는 뜻이다.

규칙 (해설서의 "안전한 비밀번호 작성규칙"):
- 영문 대/소문자, 숫자, 특수문자 중 **2종류 조합이면 10자리 이상**, **3종류 이상 조합이면
  8자리 이상**.
- 연속되거나 동일한 문자/숫자 반복 금지 (예: 1234, aaaa, abcd).
- 사번 등 개인정보로 유추 가능한 문자열 사용 금지.

정책을 서버가 단일 진실 공급원으로 들고, 화면은 같은 규칙을 안내/사전검증용으로만 쓴다
(프론트 검증만으로는 API 직접 호출을 막지 못하므로 서버 검증이 최종 관문이다).
"""
import re
from typing import List, Optional

# 화면 안내 문구와 서버 검증이 어긋나지 않도록 임계값을 여기 한 곳에서만 정의한다.
MIN_LENGTH_TWO_CLASSES = 10
MIN_LENGTH_THREE_CLASSES = 8
MAX_LENGTH = 64
# 동일/연속 문자가 이 개수 이상 이어지면 거부 (1234, aaaa 등)
MAX_SEQUENTIAL_RUN = 4


def _count_character_classes(password: str) -> int:
    classes = 0
    if re.search(r"[a-zA-Z]", password):
        classes += 1
    if re.search(r"[0-9]", password):
        classes += 1
    if re.search(r"[^a-zA-Z0-9]", password):
        classes += 1
    return classes


def _has_sequential_run(password: str) -> bool:
    """동일 문자 반복(aaaa) 또는 연속 문자/숫자(1234, abcd, 4321)를 탐지한다."""
    if len(password) < MAX_SEQUENTIAL_RUN:
        return False

    run_same = 1
    run_up = 1
    run_down = 1
    for prev, cur in zip(password, password[1:]):
        run_same = run_same + 1 if cur == prev else 1
        # 문자 코드가 1씩 증가/감소하는 흐름 (1234, abcd / 4321, dcba)
        run_up = run_up + 1 if ord(cur) - ord(prev) == 1 else 1
        run_down = run_down + 1 if ord(prev) - ord(cur) == 1 else 1
        if max(run_same, run_up, run_down) >= MAX_SEQUENTIAL_RUN:
            return True
    return False


def validate_password(password: str, employee_id: Optional[str] = None, name: Optional[str] = None) -> List[str]:
    """
    규칙 위반 사유 목록을 반환한다 (빈 리스트면 통과).

    예외를 던지지 않고 목록을 돌려주는 이유: 화면이 "무엇이 부족한지"를 항목별로 표시할 수
    있어야 하기 때문이다. 첫 위반에서 끊으면 사용자가 시행착오를 반복하게 된다.
    """
    reasons: List[str] = []

    if not password:
        return ["비밀번호를 입력해 주세요."]

    if len(password) > MAX_LENGTH:
        reasons.append(f"비밀번호는 {MAX_LENGTH}자 이하여야 합니다.")

    classes = _count_character_classes(password)
    if classes <= 1:
        reasons.append("영문, 숫자, 특수문자 중 2종류 이상을 조합해야 합니다.")
    elif classes == 2 and len(password) < MIN_LENGTH_TWO_CLASSES:
        reasons.append(f"2종류 조합은 {MIN_LENGTH_TWO_CLASSES}자 이상이어야 합니다.")
    elif classes >= 3 and len(password) < MIN_LENGTH_THREE_CLASSES:
        reasons.append(f"3종류 조합은 {MIN_LENGTH_THREE_CLASSES}자 이상이어야 합니다.")

    if _has_sequential_run(password):
        reasons.append("연속되거나 동일한 문자/숫자를 4자 이상 사용할 수 없습니다. (예: 1234, aaaa)")

    lowered = password.lower()
    if employee_id and employee_id.lower() in lowered:
        reasons.append("사번이 포함된 비밀번호는 사용할 수 없습니다.")
    if name and len(name) >= 2 and name.lower() in lowered:
        reasons.append("이름이 포함된 비밀번호는 사용할 수 없습니다.")

    return reasons


# 화면 안내 문구. 서버 규칙이 바뀌면 이 목록도 같은 파일에서 함께 바뀌므로 설명과 검증이
# 어긋날 수 없다 (프론트는 GET /auth/password-policy로 이 값을 그대로 받아 쓴다).
POLICY_DESCRIPTIONS = [
    f"영문/숫자/특수문자 중 2종류 조합 시 {MIN_LENGTH_TWO_CLASSES}자 이상, 3종류 이상 조합 시 {MIN_LENGTH_THREE_CLASSES}자 이상",
    f"연속되거나 동일한 문자/숫자 {MAX_SEQUENTIAL_RUN}자 이상 사용 금지 (예: 1234, aaaa)",
    "사번, 이름 등 유추하기 쉬운 정보 포함 금지",
]
