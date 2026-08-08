from typing import List, Optional, Tuple
from uuid import UUID

from sqlmodel import Session, func, select

from app.core.exceptions import ForbiddenException, NotFoundException
from app.models.wms import BoardComment, BoardPost, User, now_kst


def _serialize_comment(comment: BoardComment, author: User) -> dict:
    return {
        "id": str(comment.id),
        "post_id": str(comment.post_id),
        "author_id": str(comment.author_id),
        "author_employee_id": author.employee_id,
        "author_name": author.name,
        "content": comment.content,
        "created_at": comment.created_at,
        "updated_at": comment.updated_at,
    }


def _serialize_post_list_item(post: BoardPost, author: User, comment_count: int) -> dict:
    return {
        "id": str(post.id),
        "category": post.category,
        "title": post.title,
        "author_id": str(post.author_id),
        "author_employee_id": author.employee_id,
        "author_name": author.name,
        "comment_count": comment_count,
        "created_at": post.created_at,
        "updated_at": post.updated_at,
    }


def _serialize_post_detail(post: BoardPost, author: User, comments: List[dict]) -> dict:
    return {
        "id": str(post.id),
        "category": post.category,
        "title": post.title,
        "content": post.content,
        "attachment_paths": post.attachment_paths,
        "author_id": str(post.author_id),
        "author_employee_id": author.employee_id,
        "author_name": author.name,
        "created_at": post.created_at,
        "updated_at": post.updated_at,
        "comments": comments,
    }


def list_posts(
    db: Session,
    category: Optional[str],
    keyword: Optional[str],
    page: int,
    size: int,
) -> Tuple[List[dict], int]:
    stmt = select(BoardPost)
    count_stmt = select(func.count()).select_from(BoardPost)

    if category:
        stmt = stmt.where(BoardPost.category == category)
        count_stmt = count_stmt.where(BoardPost.category == category)
    if keyword:
        like = f"%{keyword}%"
        cond = (BoardPost.title.ilike(like)) | (BoardPost.content.ilike(like))
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    total = db.exec(count_stmt).one()
    skip = (page - 1) * size
    posts = db.exec(
        stmt.order_by(BoardPost.created_at.desc()).offset(skip).limit(size)
    ).all()

    items: List[dict] = []
    for post in posts:
        author = db.get(User, post.author_id)
        comment_count = db.exec(
            select(func.count()).select_from(BoardComment).where(BoardComment.post_id == post.id)
        ).one()
        items.append(_serialize_post_list_item(post, author, comment_count))

    return items, total


def get_post_or_404(db: Session, post_id: UUID) -> BoardPost:
    post = db.get(BoardPost, post_id)
    if not post:
        raise NotFoundException(f"게시글을 찾을 수 없습니다: {post_id}")
    return post


def get_post_detail(db: Session, post_id: UUID) -> dict:
    post = get_post_or_404(db, post_id)
    author = db.get(User, post.author_id)

    comment_rows = db.exec(
        select(BoardComment)
        .where(BoardComment.post_id == post.id)
        .order_by(BoardComment.created_at.asc())
    ).all()
    comments = [
        _serialize_comment(comment, db.get(User, comment.author_id))
        for comment in comment_rows
    ]

    return _serialize_post_detail(post, author, comments)


def create_post(db: Session, author: User, payload) -> dict:
    post = BoardPost(
        author_id=author.id,
        category=payload.category,
        title=payload.title,
        content=payload.content,
        attachment_paths=payload.attachment_paths,
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return _serialize_post_detail(post, author, [])


def update_post(db: Session, post: BoardPost, payload) -> dict:
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(post, field, value)
    post.updated_at = now_kst()
    db.add(post)
    db.commit()
    db.refresh(post)
    return get_post_detail(db, post.id)


def delete_post(db: Session, post: BoardPost) -> None:
    db.delete(post)
    db.commit()


def create_comment(db: Session, post: BoardPost, author: User, payload) -> dict:
    comment = BoardComment(post_id=post.id, author_id=author.id, content=payload.content)
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return _serialize_comment(comment, author)


def get_comment_or_404(db: Session, comment_id: UUID) -> BoardComment:
    comment = db.get(BoardComment, comment_id)
    if not comment:
        raise NotFoundException(f"댓글을 찾을 수 없습니다: {comment_id}")
    return comment


def update_comment(db: Session, comment: BoardComment, payload) -> dict:
    comment.content = payload.content
    comment.updated_at = now_kst()
    db.add(comment)
    db.commit()
    db.refresh(comment)
    author = db.get(User, comment.author_id)
    return _serialize_comment(comment, author)


def delete_comment(db: Session, comment: BoardComment) -> None:
    db.delete(comment)
    db.commit()


def assert_can_write_category(user: User, category: str) -> None:
    if category == "NOTICE":
        if user.role not in ("MASTER", "ADMIN"):
            raise ForbiddenException("공지사항은 관리자만 작성할 수 있습니다.")
    elif user.role == "GUEST":
        raise ForbiddenException("게시글 작성 권한이 없습니다.")


def assert_can_write_comment(user: User, post: BoardPost) -> None:
    """댓글 작성 권한은 게시글 카테고리에 따라 다르다.
    NOTICE: 댓글 자체를 막는다 (공지는 관리자 공지 채널). MANUAL(요청사항): 관리자만
    응답(댓글) 가능 - 전 직원이 요청을 올리고 관리자가 답한다. GENERAL: GUEST만 제외."""
    if post.category == "NOTICE":
        raise ForbiddenException("공지사항에는 댓글을 작성할 수 없습니다.")
    if post.category == "MANUAL":
        if user.role not in ("MASTER", "ADMIN"):
            raise ForbiddenException("요청사항 댓글은 관리자만 작성할 수 있습니다.")
        return
    if user.role == "GUEST":
        raise ForbiddenException("댓글 작성 권한이 없습니다.")


def assert_can_modify_post(user: User, post: BoardPost) -> None:
    if user.role in ("MASTER", "ADMIN") or str(post.author_id) == str(user.id):
        return
    raise ForbiddenException("본인이 작성한 게시글만 수정/삭제할 수 있습니다.")


def assert_can_modify_comment(user: User, comment: BoardComment) -> None:
    if user.role in ("MASTER", "ADMIN") or str(comment.author_id) == str(user.id):
        return
    raise ForbiddenException("본인이 작성한 댓글만 수정/삭제할 수 있습니다.")
