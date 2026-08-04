"""
FastAPI 애플리케이션의 진입점(Entrypoint) 파일입니다.
앱 초기화, 데이터베이스 테이블 생성 트리거, 그리고 도메인별 API 라우터를 마운트하는 역할을 합니다.
"""
from fastapi import FastAPI
from app.domains.auth import router as auth
from app.domains.dashboard import router as dashboard
from app.domains.inbound import router as inbound
from app.domains.inventory import router as inventory
from app.domains.orders import router as orders
from app.domains.po import router as po
from app.domains.returns import router as returns
from app.domains.users import router as users
from app.domains.uploads import router as uploads
from app.domains.admin.router import router as admin
from app.domains.research.router import router as research
from app.domains.notifications import router as notifications
from app.domains.fds import router as fds
from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlmodel import Session
import datetime

from app.db.session import get_db
from app.core.config import settings
from app.core.middleware import LoggingMiddleware

# FastAPI 앱 객체 생성 및 메타데이터(Swagger 문서 등) 설정
app = FastAPI(
    title="Nexus",
    description="다중 에이전트 기반 B2B 물류 자동화 플랫폼 Nexus Backend",
    version="2.12.2.0"
)

# ==========================================
# OpenTelemetry 분산 추적 (SCI 논문 데이터 수집용)
# ==========================================
try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    # 임시 콘솔 익스포터 설정 (추후 Jaeger/Zipkin OTLP 익스포터로 변경 가능)
    provider = TracerProvider()
    processor = BatchSpanProcessor(ConsoleSpanExporter())
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app)
    print("OpenTelemetry FastAPI Instrumentation enabled.")
except ImportError:
    print("OpenTelemetry not installed. Skipping tracing setup.")

# ==========================================
# SlowAPI (Rate Limiter) 전역 설정
# ==========================================
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.core.limiter import limiter

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ==========================================
# 미들웨어(Middleware) 등록
# ==========================================
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(LoggingMiddleware)

# ==========================================
# 글로벌 에러 핸들러 (Global Exception Handlers)
# ==========================================
from fastapi import HTTPException

@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    """지정된 커스텀 HTTPException이 터졌을 때 엔터프라이즈 JSON 규격으로 반환"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status": "error",
            "code": exc.status_code,
            "message": exc.detail,
            "path": request.url.path,
            "timestamp": now_kst().isoformat() + "Z"
        },
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """예상치 못한 일반 Exception(500 서버 에러) 처리"""
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "code": 500,
            "message": "Internal Server Error",
            "detail": str(exc),
            "path": request.url.path,
            "timestamp": now_kst().isoformat() + "Z"
        },
    )
@app.on_event("startup")
def on_startup():
    """
    서버 시작 시 DB에 사용자가 0명일 경우, 최초 MASTER 계정(WM2608001 장문경)을 자동으로 시딩합니다.
    """
    from app.db.session import engine
    from sqlmodel import Session, select
    from app.models.wms import User, UserRoleEnum, UserStatusEnum
    from passlib.context import CryptContext

    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

    try:
        with Session(engine) as session:
            users_list = session.exec(select(User)).all()
            if len(users_list) == 0:
                from datetime import datetime
                yymm = datetime.now().strftime("%y%m")
                dynamic_master_id = f"WM{yymm}001"
                print(f"[Startup] DB가 비어있습니다. 최초 MASTER 계정 ({dynamic_master_id} 장문경)을 자동 시딩합니다...")
                master_user = User(
                    employee_id=dynamic_master_id,
                    name="장문경",
                    password_hash=pwd_context.hash("1234"),
                    role=UserRoleEnum.MASTER,
                    status=UserStatusEnum.ACTIVE,
                    must_change_password=False
                )
                session.add(master_user)
                session.commit()
                print(f"[Startup] 최초 MASTER 계정 ({dynamic_master_id} / 비밀번호: 1234) 생성 완료!")
    except Exception as e:
        print("[Startup] 최초 MASTER 계정 자동 시딩 중 에러:", e)

# ==========================================
# 라우터 등록 (도메인별 API 분리)
# ==========================================
# settings.API_V1_STR (예: "/api/v1") Prefix를 달고 각 도메인의 라우터를 포함시킵니다.
app.include_router(dashboard.router, prefix=settings.API_V1_STR)
app.include_router(inventory.router, prefix=settings.API_V1_STR)
app.include_router(po.router, prefix=settings.API_V1_STR)
app.include_router(inbound.router, prefix=settings.API_V1_STR)
app.include_router(returns.router, prefix=settings.API_V1_STR)
app.include_router(orders.router, prefix=settings.API_V1_STR)
app.include_router(auth.router, prefix=f"{settings.API_V1_STR}/auth", tags=["Auth"])
app.include_router(users.router, prefix=f"{settings.API_V1_STR}/users", tags=["Users"])
app.include_router(uploads.router, prefix=settings.API_V1_STR)
app.include_router(admin, prefix=settings.API_V1_STR)
app.include_router(research, prefix=settings.API_V1_STR)
app.include_router(notifications.router, prefix=settings.API_V1_STR)
app.include_router(fds.router, prefix=settings.API_V1_STR)

import os
from fastapi.staticfiles import StaticFiles
from app.models.wms import now_kst
base_dir = os.path.dirname(os.path.abspath(__file__))
experiment_dir = os.path.join(base_dir, "experiment_data")
os.makedirs(experiment_dir, exist_ok=True)
app.mount("/experiment_data", StaticFiles(directory=experiment_dir), name="experiment_data")

@app.get("/health")
def health_check():
    """
    로드밸런서(K8s Ingress 등) 또는 KEDA 스케일링을 위한 서버 헬스 체크 엔드포인트입니다.
    """
    return {"status": "ok", "version": "2.12.2.0"}

@app.get("/db-check")
def db_check(session: Session = Depends(get_db)):
    """
    데이터베이스 연동이 정상적으로 되었는지 터미널과 브라우저에서 직접 확인하기 위한 테스트 엔드포인트입니다.
    """
    try:
        # DB에 간단한 1+1 핑거프린트 쿼리를 날려 연결을 테스트합니다.
        session.exec(text("SELECT 1")).first()
        return {"db_status": "connected", "ping": "ok"}
    except Exception as e:
        return {"db_status": "disconnected", "error": str(e)}
