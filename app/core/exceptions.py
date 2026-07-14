from fastapi import HTTPException, status

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
    def __init__(self):
        super().__init__(detail="Incorrect employee ID or password")

class InactiveAccountException(ForbiddenException):
    def __init__(self):
        super().__init__(detail="Account is inactive")
