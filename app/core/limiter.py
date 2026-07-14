from slowapi import Limiter
from slowapi.util import get_remote_address

# 클라이언트 IP 기반으로 Rate Limiting을 수행하는 Limiter 인스턴스
limiter = Limiter(key_func=get_remote_address)
