"""
애플리케이션 환경변수 및 전역 설정을 중앙에서 관리하는 파일입니다.
pydantic-settings를 사용하여 .env 파일의 값을 자동으로 읽고 타입 검증(Type Validation)을 수행합니다.
"""
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
    PROJECT_NAME: str = "WMS Core API"
    APP_ENV: str = "local" # local, dev, prod
    API_V1_STR: str = "/api/v1"
    DATABASE_URL: str = "postgresql://admin:password@localhost:5432/wms_db"
    
    # Celery & Redis (추후 비동기 작업 및 상태 캐싱을 위해 사용 예정)
    REDIS_URL: str = "redis://localhost:6379/0"

    # JWT & Auth
    SECRET_KEY: str = "super_secret_key_for_wms_platform_change_me_in_production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7일

    # Invitation Codes (회원가입 제한코드)
    WORKER_INVITATION_CODE: str = "WMS_WORKER_2026"
    MASTER_INVITATION_CODE: str = "WMS_MASTER_2026"

    # AWS & CloudFront (이미지 다이렉트 업로드 용도)
    AWS_S3_BUCKET: str = "mock-wms-bucket"
    AWS_REGION: str = "ap-northeast-2"
    CLOUDFRONT_DOMAIN: str = "https://mock1234.cloudfront.net"
    CLOUDFRONT_KEY_PAIR_ID: str = "mock_key_pair_id"
    CLOUDFRONT_PRIVATE_KEY: str = "mock_private_key"

    # 최초 MASTER 초기화 변수
    FIRST_SUPERUSER_ID: str = "EMP0001"
    FIRST_SUPERUSER_EMAIL: str = "admin@wms.com"
    FIRST_SUPERUSER_PASSWORD: str = "" # 빈 값일 경우 난수 자동 생성

    # Pydantic V2 설정 방식: .env 파일을 우선적으로 읽도록 지정합니다.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

# 설정 객체를 싱글톤처럼 하나만 생성하여 전역적으로 사용합니다.
settings = Settings()
