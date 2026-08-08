from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class BoardPostCreate(BaseModel):
    category: str  # BoardCategoryEnum: NOTICE/MANUAL/GENERAL
    title: str = Field(max_length=255)
    content: str
    attachment_paths: List[str] = []


class BoardPostUpdate(BaseModel):
    category: Optional[str] = None
    title: Optional[str] = Field(default=None, max_length=255)
    content: Optional[str] = None
    attachment_paths: Optional[List[str]] = None


class BoardCommentCreate(BaseModel):
    content: str


class BoardCommentUpdate(BaseModel):
    content: str


class BoardCommentResponse(BaseModel):
    id: str
    post_id: str
    author_id: str
    author_employee_id: str
    author_name: str
    content: str
    created_at: datetime
    updated_at: datetime


class BoardPostListItem(BaseModel):
    id: str
    category: str
    title: str
    author_id: str
    author_employee_id: str
    author_name: str
    comment_count: int
    created_at: datetime
    updated_at: datetime


class BoardPostListResponse(BaseModel):
    items: List[BoardPostListItem]
    total: int
    page: int
    size: int


class BoardPostDetailResponse(BaseModel):
    id: str
    category: str
    title: str
    content: str
    attachment_paths: List[str]
    author_id: str
    author_employee_id: str
    author_name: str
    created_at: datetime
    updated_at: datetime
    comments: List[BoardCommentResponse]
