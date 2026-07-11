from pydantic import BaseModel
from typing import List

class InspectionRequest(BaseModel):
    """
    반품 검수 API 요청용 DTO (Data Transfer Object)
    """
    book_id: int
    location_id: int
    image_urls: List[str]
