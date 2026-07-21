import sys
import os
import secrets
import string
import bcrypt

# Passlib compatibility with bcrypt >= 4.0.0
class _BcryptAbout:
    __version__ = bcrypt.__version__
bcrypt.__about__ = _BcryptAbout

# 프로젝트 루트 경로를 sys.path에 추가 (app 모듈을 스크립트에서 찾을 수 있도록)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings
from app.db.session import engine
from sqlmodel import Session, select
from app.models.wms import User, UserRoleEnum, UserStatusEnum

def generate_random_password(length=12):
    """보안상 안전한 12자리 난수 비밀번호를 생성합니다."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(secrets.choice(alphabet) for i in range(length))

def init_db():
    print(f"[*] 환경 확인 중... 현재 APP_ENV: {settings.APP_ENV}")
    
    # 1. 환경(APP_ENV) 락 검증
    if settings.APP_ENV == "prod":
        print("🚨 [보안 경고] 운영(Prod) 환경에서는 자동화된 Seeding 스크립트를 실행할 수 없습니다.")
        print("🚨 수동으로 관리자 계정을 프로비저닝 하십시오. 작업을 즉시 중단합니다.")
        return

    print("[*] 데이터베이스에 초기 MASTER 계정 생성을 시도합니다...")
    
    # 2. DB 연결 및 멱등성 검증
    with Session(engine) as session:
        # 이미 해당 사번의 슈퍼유저가 있는지 확인
        master_user = session.exec(
            select(User).where(User.employee_id == settings.FIRST_SUPERUSER_ID)
        ).first()

        if master_user:
            print(f"[!] 초기 MASTER 계정({settings.FIRST_SUPERUSER_ID})이 이미 존재합니다. Seeding을 스킵합니다.")
            return

        # 3. 비밀번호 정책 적용 (.env에 없으면 난수 생성)
        raw_password = settings.FIRST_SUPERUSER_PASSWORD
        is_auto_generated = False
        
        if not raw_password:
            raw_password = generate_random_password()
            is_auto_generated = True
        
        # passlib 버그 우회를 위해 날것의 bcrypt 사용
        hashed_password = bcrypt.hashpw(raw_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        # 4. 엔티티 생성 및 Insert
        new_master = User(
            employee_id=settings.FIRST_SUPERUSER_ID,
            email=settings.FIRST_SUPERUSER_EMAIL,
            name="System Admin",
            password_hash=hashed_password,
            role=UserRoleEnum.MASTER.value,
            status=UserStatusEnum.ACTIVE.value,
            must_change_password=True  # 보안을 위해 로그인 성공 즉시 비밀번호 변경 강제
        )

        session.add(new_master)
        session.commit()
        session.refresh(new_master)

        # 5. 결과 리포팅
        print("\n==========================================================")
        print("[SUCCESS] 최초 관리자(MASTER) 계정이 생성되었습니다.")
        print(f"[ID]: {settings.FIRST_SUPERUSER_ID}")
        print(f"[EMAIL]: {settings.FIRST_SUPERUSER_EMAIL}")
        if is_auto_generated:
            print(f"[PASSWORD] 자동 생성된 임시 비밀번호: {raw_password}")
            print("[WARNING] 위 비밀번호는 시스템 어디에도 평문으로 저장되지 않습니다. (반드시 복사해 두세요!)")
        else:
            print("[PASSWORD] (.env 파일에 지정된 FIRST_SUPERUSER_PASSWORD 값)")
        print("==========================================================\n")

if __name__ == "__main__":
    init_db()
