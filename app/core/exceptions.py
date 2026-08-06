from fastapi import HTTPException, status

# [2026-08-06 신설] 실패 사유 식별 코드.
#
# 종전에는 프론트가 HTTP 상태코드와 영문 detail 문자열만 보고 사유를 추측했다. 그래서
# 429(시도 제한)가 "사번 또는 비밀번호가 올바르지 않습니다"로 표시되는 등, 화면에 뜬 사유와
# 실제 원인이 어긋나 장애 진단이 불가능했다. 사람이 읽는 문구가 아니라 **기계가 분기할 수 있는
# 코드**를 별도로 실어 보낸다 (문구는 바뀌어도 코드는 계약으로 고정된다).


class AppException(HTTPException):
    """error_code를 함께 실어 보내는 공통 예외 베이스."""

    error_code: str = "UNKNOWN"

    def __init__(self, status_code: int, detail: str, error_code: str = "UNKNOWN", headers: dict | None = None):
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.error_code = error_code


class BadRequestException(HTTPException):
    def __init__(self, detail: str = "Bad Request"):
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)

class NotFoundException(HTTPException):
    def __init__(self, detail: str = "Not Found"):
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)

class UserAlreadyExistsException(BadRequestException):
    def __init__(self):
        super().__init__(detail="Employee ID already registered")

class EmailAlreadyExistsException(BadRequestException):
    def __init__(self):
        super().__init__(detail="Email already registered")

class InvalidInvitationCodeException(BadRequestException):
    def __init__(self):
        super().__init__(detail="Invalid invitation code")

class UnauthorizedException(HTTPException):
    def __init__(self, detail: str = "Unauthorized"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"}
        )

class ForbiddenException(HTTPException):
    def __init__(self, detail: str = "Forbidden"):
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)

class InvalidCredentialsException(UnauthorizedException):
    error_code = "AUTH_INVALID_CREDENTIALS"

    def __init__(self, remaining_attempts: int | None = None):
        # 사번 미존재와 비밀번호 불일치를 구분해 알려주지 않는다 - 유효 사번을 열거당하는
        # 계정 탐색(user enumeration)의 통로가 되기 때문이다. 대신 "남은 시도 횟수"를 줘서
        # 사용자가 자기 상태를 파악할 수 있게 한다.
        super().__init__(detail="사번 또는 비밀번호가 일치하지 않습니다.")
        self.remaining_attempts = remaining_attempts

class InactiveAccountException(ForbiddenException):
    error_code = "AUTH_ACCOUNT_INACTIVE"

    def __init__(self):
        super().__init__(detail="비활성 상태의 계정입니다. 관리자에게 계정 활성화를 요청하세요.")


class PasswordPolicyViolationException(AppException):
    """새 비밀번호가 작성 규칙을 만족하지 못한 경우 (app/core/password_policy.py)."""

    def __init__(self, reasons: list[str]):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="비밀번호가 작성 규칙을 만족하지 않습니다: " + " / ".join(reasons),
            error_code="PASSWORD_POLICY_VIOLATION",
        )
        # 화면이 위반 항목을 개별 표시할 수 있게 목록 그대로도 실어 보낸다.
        self.violations = reasons


class TooManyLoginAttemptsException(AppException):
    """사번 단위 실패 스로틀에 걸린 상태 (계정 잠금이 아니라 한시적 제한)."""

    def __init__(self, retry_after_seconds: int):
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"로그인 시도가 너무 많습니다. {retry_after_seconds}초 후에 다시 시도해 주세요."
            ),
            error_code="AUTH_TOO_MANY_ATTEMPTS",
            headers={"Retry-After": str(retry_after_seconds)},
        )
        self.retry_after_seconds = retry_after_seconds
