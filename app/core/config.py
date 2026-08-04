"""
애플리케이션 환경변수 및 전역 설정을 중앙에서 관리하는 파일입니다.
pydantic-settings를 사용하여 .env 파일의 값을 자동으로 읽고 타입 검증(Type Validation)을 수행합니다.
"""
from typing import Optional

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

    # XP-423B: 203 DPI(약 8 dots/mm), 라벨 50mm × 30mm
    LABEL_PRINTER_DPI: int = 203
    LABEL_PRINTER_LABEL_WIDTH_MM: int = 50
    LABEL_PRINTER_LABEL_HEIGHT_MM: int = 30

    # ZPL 전송 인코딩 (프린터 한글 설정에 따라 utf-8 또는 euc-kr)
    LABEL_PRINTER_ENCODING: str = "utf-8"

    # JWT & Auth
    SECRET_KEY: str = "wms_secret_master_jwt_token_key_2026_8f9a2b4c1d6e7f8a9b0c1d2e3f4a5b6c"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7일

    # 인증 쿠키 속성 (login/logout 라우터에서 공용으로 사용 - 호출부마다 다른 값을
    # 하드코딩하지 않도록 여기서 중앙 관리한다. 운영 배포 시 .env에서 COOKIE_SECURE=True로 재정의)
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: str = "lax"
    COOKIE_DOMAIN: Optional[str] = None

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
