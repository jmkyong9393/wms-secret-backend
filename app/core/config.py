from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "WMS Core API"
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/wms_db"
    
    # Celery & Redis (For BE-2 Integration later)
    REDIS_URL: str = "redis://localhost:6379/0"

    class Config:
        env_file = ".env"

settings = Settings()
