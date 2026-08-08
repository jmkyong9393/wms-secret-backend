"""
애플리케이션 환경변수 및 전역 설정을 중앙에서 관리하는 파일입니다.
pydantic-settings를 사용하여 .env 파일의 값을 자동으로 읽고 타입 검증(Type Validation)을 수행합니다.
"""
from typing import Optional, Literal

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# LangChain 등 파이썬 내장 os.environ을 직접 참조하는 외부 라이브러리를 위해 
# .env 파일의 내용을 OS 환경변수로 강제 주입(Load)합니다.
load_dotenv()


class Settings(BaseSettings):
    """
    애플리케이션에서 사용될 모든 환경변수를 정의하는 모델 클래스입니다.
    여기에 정의된 속성들은 .env 파일 또는 OS 환경변수에서 자동으로 값을 찾아 매핑됩니다.
    """
    PROJECT_NAME: str = "Nexus Core API"
    APP_ENV: str = "local" # local, dev, prod
    API_V1_STR: str = "/api/v1"
    DATABASE_URL: str = "postgresql://admin:password@localhost:5432/wms_db"
    
    # Celery & Redis (추후 비동기 작업 및 상태 캐싱을 위해 사용 예정)
    REDIS_URL: str = "redis://localhost:6379/0"

    # 검수 DLQ 보관정책: 최대 1000건, 마지막 실패 적재 후 14일 보관
    # (무한 적재로 인한 Redis 메모리 누수 방지 — 상한 초과 시 오래된 항목부터 절삭)
    INSPECTION_DLQ_MAX_ENTRIES: int = 1000
    INSPECTION_DLQ_TTL_SECONDS: int = 60 * 60 * 24 * 14

    # 라벨 QR이 가리킬 프론트엔드 공개 주소 (품질보증서 /certificate/{lpn} 생성용)
    PUBLIC_WEB_BASE_URL: str = "http://localhost:3000"

    # 네트워크 라벨 프린터 (Xprinter XP-423B, LAN Raw TCP)
    # 개발·테스트 환경에서는 False로 두어 프린터 연결 실패가
    # 입고·검수 처리 실패로 이어지지 않게 한다. 실제 장비 IP는 .env에서 주입.
    LABEL_PRINTER_ENABLED: bool = False
    LABEL_PRINTER_HOST: str = ""
    LABEL_PRINTER_PORT: int = 9100
    LABEL_PRINTER_TIMEOUT_SECONDS: float = 5.0

    # XP-423B: 203 DPI(약 8 dots/mm), 라벨 50mm × 31mm (다이컷 라벨 RS5031 실측 규격)
    LABEL_PRINTER_DPI: int = 203
    LABEL_PRINTER_LABEL_WIDTH_MM: int = 50
    LABEL_PRINTER_LABEL_HEIGHT_MM: int = 31

    # RS5031 라벨 인쇄 여백 (제조사 규격: 왼쪽 1.5mm, 위쪽 1.44mm)
    LABEL_PRINTER_MARGIN_LEFT_MM: float = 1.5
    LABEL_PRINTER_MARGIN_TOP_MM: float = 1.44

    # ZPL 전송 인코딩 (프린터 한글 설정에 따라 utf-8 또는 euc-kr)
    LABEL_PRINTER_ENCODING: str = "utf-8"

    # 라벨 인쇄 경로 선택:
    # DIRECT = 백엔드가 프린터 LAN IP로 직접 전송 (로컬/온프레미스 — 시연 기본값)
    # QUEUE  = DB 큐에 적재 후 창고 PC의 프린트 브리지 에이전트가 중계 (클라우드 배포)
    LABEL_PRINT_MODE: Literal["DIRECT", "QUEUE"] = "DIRECT"

    # JWT & Auth
    SECRET_KEY: str = "wms_secret_master_jwt_token_key_2026_8f9a2b4c1d6e7f8a9b0c1d2e3f4a5b6c"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7일

    # 인증 쿠키 속성 (login/logout 라우터에서 공용으로 사용 - 호출부마다 다른 값을
    # 하드코딩하지 않도록 여기서 중앙 관리한다. 운영 배포 시 .env에서 COOKIE_SECURE=True로 재정의)
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: str = "lax"
    COOKIE_DOMAIN: Optional[str] = None

    # --- Rate Limiting / 브루트포스 방어 ---
    #
    # [배경] API는 Next.js 프록시(rewrites) 뒤에 있어, 컨테이너가 관찰하는 peer IP가
    # 항상 도커 게이트웨이(172.20.0.1) 하나로 수렴한다. 그 상태로 IP 기준 리밋을 걸면
    # 접속자 전원이 단일 버킷을 공유해 서로를 잠근다(터널 시연에서 실측 확인).
    # 아래 대역에서 들어온 요청에 한해 X-Forwarded-For의 원 클라이언트 IP를 채택한다.
    # 신뢰 대역 밖에서 온 XFF는 위조 가능하므로 무시한다.
    TRUSTED_PROXY_CIDRS: str = "127.0.0.0/8,::1/128,172.16.0.0/12,10.0.0.0/8"

    # IP 기준 리밋은 봇의 대량 시도를 거르는 광역 그물 역할만 한다.
    # 계정 단위 브루트포스 방어는 아래 실패 카운터가 담당한다.
    LOGIN_IP_RATE_LIMIT: str = "30/minute"

    # 사번 단위 로그인 실패 스로틀 (성공하면 즉시 리셋된다).
    # 계정을 영구 잠그지 않는 이유: 남의 사번으로 일부러 실패시켜 잠그는 DoS를 막기 위해
    # 짧은 TTL 스로틀로만 제한한다.
    LOGIN_FAIL_MAX_ATTEMPTS: int = 10
    LOGIN_FAIL_WINDOW_SECONDS: int = 300

    # SSE 스트림 접근 티켓 유효시간(초). 발급 직후 연결에만 쓰이므로 짧게 유지한다.
    SSE_TICKET_EXPIRE_SECONDS: int = 30

    # Invitation Codes (회원가입 제한코드)
    WORKER_INVITATION_CODE: str = "WMS_WORKER_2026"
    MASTER_INVITATION_CODE: str = "WMS_MASTER_2026"

    # AWS & CloudFront (이미지 다이렉트 업로드 용도)
    # 자격증명은 반드시 .env / 환경변수로만 주입한다 - 코드에 하드코딩 폴백을 두지 않는다.
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    AWS_S3_BUCKET: str = "wms-secret-vision-assets"
    AWS_REGION: str = "ap-northeast-2"
    CLOUDFRONT_DOMAIN: str = "https://deao4fid6qoyp.cloudfront.net"
    CLOUDFRONT_KEY_PAIR_ID: str = "mock_key_pair_id"
    CLOUDFRONT_PRIVATE_KEY: str = "mock_private_key"

    # LangSmith Tracing & MSW Settings
    LANGCHAIN_TRACING_V2: str = "true"
    LANGCHAIN_ENDPOINT: str = "https://api.smith.langchain.com"
    LANGCHAIN_PROJECT: str = "wms-secret-vision-agent"
    NEXT_PUBLIC_MSW_ENABLED: bool = False
    BACKEND_API_URL: str = "http://localhost:8000/api/v1"

    # Aladin Open API Key for ISBN Lookup
    ALADIN_TTB_KEY: str = "ttbjmkyong20022330001"

    # 최초 MASTER 초기화 변수
    FIRST_SUPERUSER_ID: str = "EMP0001"
    FIRST_SUPERUSER_EMAIL: str = "admin@wms.com"
    FIRST_SUPERUSER_PASSWORD: str = "" # 빈 값일 경우 난수 자동 생성

    # Pydantic V2 설정 방식: .env 파일을 우선적으로 읽도록 지정합니다.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

# 설정 객체를 싱글톤처럼 하나만 생성하여 전역적으로 사용합니다.
settings = Settings()
