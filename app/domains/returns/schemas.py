from pydantic import BaseModel
from typing import List

from uuid import UUID

class InspectionRequest(BaseModel):
    """
    반품 검수 API 요청용 DTO (Data Transfer Object)
    """
    book_id: UUID
    location_id: UUID
    image_urls: List[str]
