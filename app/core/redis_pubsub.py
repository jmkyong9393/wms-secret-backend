import json
import os
from typing import Any, Dict

import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Redis Pub/Sub 채널 이름 만드는 함수
def get_return_job_channel(return_job_id: str) -> str:
    return f"return_job:{return_job_id}"

# Publish 함수
def publish_return_job_event(
        return_job_id: str,
        event: Dict[str,Any],
)->None:
    redis_client = redis.Redis.from_url(
        REDIS_URL,
        decode_responses=True,
    )

    redis_client.publish(
        get_return_job_channel(return_job_id),
        json.dumps(event, ensure_ascii=False),
    )

    redis_client.close()