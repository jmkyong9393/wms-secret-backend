import time
import logging
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

# 기본 로거 설정 (시간, 로그 레벨, 메시지 포맷 지정)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()

        # 1. API 처리로 넘김
        response = await call_next(request)

        # 2. 처리 후 지연시간 계산
        process_time = (time.time() - start_time) * 1000

        # 3. 로깅 출력 (형식: [INFO] 2026-07-14 12:00:00 | POST /api/v1/users/register | Status: 201 | Latency: 12.34ms)
        logger.info(
            f"{request.method} {request.url.path} | Status: {response.status_code} | Latency: {process_time:.2f}ms"
        )

        # 응답 헤더에 서버 처리 시간을 추가로 실어보냄 (선택사항, 디버깅 용이)
        response.headers["X-Process-Time"] = str(process_time)
        return response
