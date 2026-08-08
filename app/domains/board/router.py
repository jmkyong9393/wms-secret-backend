from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlmodel import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.domains.board import service as board_service
from app.domains.board.schemas import (
    BoardCommentCreate,
    BoardCommentResponse,
    BoardCommentUpdate,
    BoardPostCreate,
    BoardPostDetailResponse,
    BoardPostListResponse,
    BoardPostUpdate,
)
from app.models.wms import User

router = APIRouter(prefix="/board", tags=["Board"])


@router.get("/posts", response_model=BoardPostListResponse, summary="게시글 목록 조회")
def list_board_posts(
    category: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    items, total = board_service.list_posts(db, category, keyword, page, size)
    return {"items": items, "total": total, "page": page, "size": size}


@router.get("/posts/{post_id}", response_model=BoardPostDetailResponse, summary="게시글 상세 조회")
def get_board_post(
    post_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return board_service.get_post_detail(db, post_id)


@router.post(
    "/posts",
    response_model=BoardPostDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="게시글 작성",
)
def create_board_post(
    payload: BoardPostCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    board_service.assert_can_write_category(current_user, payload.category)
    return board_service.create_post(db, current_user, payload)


@router.patch("/posts/{post_id}", response_model=BoardPostDetailResponse, summary="게시글 수정")
def update_board_post(
    post_id: UUID,
    payload: BoardPostUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    post = board_service.get_post_or_404(db, post_id)
    board_service.assert_can_modify_post(current_user, post)
    if payload.category is not None:
        board_service.assert_can_write_category(current_user, payload.category)
    return board_service.update_post(db, post, payload)


@router.delete("/posts/{post_id}", summary="게시글 삭제")
def delete_board_post(
    post_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    post = board_service.get_post_or_404(db, post_id)
    board_service.assert_can_modify_post(current_user, post)
    board_service.delete_post(db, post)
    return {"status": "success", "id": str(post_id)}


@router.post(
    "/posts/{post_id}/comments",
    response_model=BoardCommentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="댓글 작성",
)
def create_board_comment(
    post_id: UUID,
    payload: BoardCommentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    post = board_service.get_post_or_404(db, post_id)
    board_service.assert_can_write_comment(current_user, post)
    return board_service.create_comment(db, post, current_user, payload)


@router.patch("/comments/{comment_id}", response_model=BoardCommentResponse, summary="댓글 수정")
def update_board_comment(
    comment_id: UUID,
    payload: BoardCommentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    comment = board_service.get_comment_or_404(db, comment_id)
    board_service.assert_can_modify_comment(current_user, comment)
    return board_service.update_comment(db, comment, payload)


@router.delete("/comments/{comment_id}", summary="댓글 삭제")
def delete_board_comment(
    comment_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    comment = board_service.get_comment_or_404(db, comment_id)
    board_service.assert_can_modify_comment(current_user, comment)
    board_service.delete_comment(db, comment)
    return {"status": "success", "id": str(comment_id)}
