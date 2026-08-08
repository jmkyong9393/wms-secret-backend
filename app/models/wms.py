from sqlmodel import SQLModel, Field, UniqueConstraint
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from typing import Optional, List, Dict, Any
from uuid import UUID, uuid4
from datetime import datetime, timezone, timedelta
from enum import Enum
from sqlalchemy import Column, Enum as SQLEnum, Text

KST = timezone(timedelta(hours=9))

def now_kst() -> datetime:
    """한국 표준시(KST, UTC+9) 현재 시각 반환 헬퍼 (DB 적재용)"""
    return datetime.now(KST).replace(tzinfo=None)

def ubci_grade_from_score(score) -> str:
    """UBCI_Specification_v2.0.0.0.md 공식 등급 경계값 (S>=95=MINT, A>=85=GOOD, B>=65=NORMAL, else REJECT)"""
    if score is None:
        return "NORMAL"
    if score >= 95:
        return "MINT"
    if score >= 85:
        return "GOOD"
    if score >= 65:
        return "NORMAL"
    return "REJECT"

# UBCI_Specification_v2.0.0.0.md 공식 등급 경계 구간 (ubci_grade_from_score와 동일 기준의 역방향 표)
UBCI_GRADE_SCORE_BANDS = {
    "MINT": (95, 100),
    "GOOD": (85, 94),
    "NORMAL": (65, 84),
    "REJECT": (0, 64),
}

def clamp_ubci_score_to_grade(score, grade: str):
    """
    사람이 등급을 최종 확정(HITL 오버라이드)했을 때 점수를 확정 등급의 공식 경계 구간으로 사상한다.

    [수정 이력 2026-08-06] HITL에서 MINT(100점) 건을 NORMAL로 하향 승인해도 AI가 산출한
    ubci_score=100이 그대로 재고에 저장되어 "UBCI 100점 (NORMAL 등급)"이라는 모순 표기와
    함께 동적 가격 산정의 상태 보정(점수 기반)까지 MINT 가격으로 계산되던 문제의 교정.
    등급은 사람이 확정했으므로 점수가 등급을 따라간다 (역방향 금지) - 원 점수는 최소 변경
    원칙으로 구간 안에 클램프하고, 점수 미산출(None) 건은 구간 상한을 부여한다.
    """
    band = UBCI_GRADE_SCORE_BANDS.get((grade or "").upper())
    if band is None:
        return score
    lo, hi = band
    if score is None:
        return hi
    return max(lo, min(hi, int(score)))

# --- Enums (상태 및 타입 정의) ---

class ConditionGradeEnum(str, Enum):
    MINT = "MINT"
    GOOD = "GOOD"
    NORMAL = "NORMAL"
    REJECT = "REJECT"

class UserRoleEnum(str, Enum):
    MASTER = "MASTER"
    ADMIN = "ADMIN"
    WORKER = "WORKER"
    GUEST = "GUEST"

class UserStatusEnum(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"

class InboundTypeEnum(str, Enum):
    NEW_STOCK = "NEW_STOCK"
    USED_PURCHASE = "USED_PURCHASE"
    CUSTOMER_RETURN = "CUSTOMER_RETURN"

class InboundStatusEnum(str, Enum):
    RECEIVED = "RECEIVED"
    CHECKING = "CHECKING"
    COMPLETED = "COMPLETED"

class JobStatusEnum(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    HITL_REQUIRED = "HITL_REQUIRED" # Human-in-the-loop 수동 검수 대기 상태
    FAILED = "FAILED"

class TransactionTypeEnum(str, Enum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"
    RETURN_RESTOCK = "RETURN_RESTOCK"
    DISCARD = "DISCARD"
    
class OrderStatusEnum(str, Enum):
    PENDING = "PENDING"
    PICKING = "PICKING"
    SHIPPED = "SHIPPED"
    RETURN_REQUESTED = "RETURN_REQUESTED"

class OrderTypeEnum(str, Enum):
    B2B_ORDER = "B2B_ORDER"
    AUTO_PO = "AUTO_PO"

class ItemStatusEnum(str, Enum):
    IN_STOCK = "IN_STOCK"
    ALLOCATED = "ALLOCATED"
    SHIPPED = "SHIPPED"

class PickingInstructionStatusEnum(str, Enum):
    PENDING = "PENDING"          # 지시서 발행됨 (worker 수락 대기)
    ACCEPTED = "ACCEPTED"        # worker 수락 완료, 피킹 시작 전
    IN_PROGRESS = "IN_PROGRESS"  # 1건 이상 피킹됨
    PICKED = "PICKED"            # 전 품목 피킹 완료 (admin 패킹 확정 대기)
    PACKED = "PACKED"            # 패킹 확정 + 송장 발급 + 재고 차감 (worker 포장 대기)
    SHIPPED = "SHIPPED"          # worker 포장 완료 = 최종 출고
    CANCELLED = "CANCELLED"

class PickingItemStatusEnum(str, Enum):
    PENDING = "PENDING"
    PICKED = "PICKED"

class BoardTicketStatusEnum(str, Enum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"

class BoardCategoryEnum(str, Enum):
    NOTICE = "NOTICE"
    MANUAL = "MANUAL"
    GENERAL = "GENERAL"

# --- Entity Models (SQLModel) ---

class User(SQLModel, table=True):
    __tablename__ = "users"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    employee_id: str = Field(max_length=50, unique=True, index=True)
    email: Optional[str] = Field(default=None, max_length=100, unique=True, index=True)
    name: str = Field(max_length=50)
    password_hash: str = Field(max_length=255)
    phone_number: Optional[str] = Field(default=None, max_length=50)
    address: Optional[str] = Field(default=None, max_length=255)
    role: UserRoleEnum = Field(sa_column=Column(SQLEnum(UserRoleEnum), nullable=False))
    status: UserStatusEnum = Field(default=UserStatusEnum.ACTIVE, sa_column=Column(SQLEnum(UserStatusEnum), nullable=False, default=UserStatusEnum.ACTIVE))
    must_change_password: bool = Field(default=False)
    # 개인정보 수집·이용 동의 시각 (개인정보 보호법 제15조).
    # 동의 여부를 boolean이 아니라 시각으로 남기는 이유: "언제 동의를 받았는가"를 증빙하지
    # 못하면 동의를 받았다는 사실 자체를 입증할 수 없기 때문이다(입증책임은 개인정보처리자에게 있다).
    # NULL = 아직 동의하지 않음.
    privacy_consent_at: Optional[datetime] = Field(default=None)
    last_login: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=now_kst)
    updated_at: datetime = Field(default_factory=now_kst)

class Book(SQLModel, table=True):
    __tablename__ = "books"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    title: str = Field(max_length=255)
    author: Optional[str] = Field(default=None, max_length=255)
    publisher: Optional[str] = Field(default=None, max_length=255)
    published_date: Optional[str] = Field(default=None, max_length=50)
    isbn: str = Field(max_length=13, unique=True, index=True)
    category_type: str = Field(default="GENERAL", max_length=50)
    cover_image_url: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = Field(default=None, sa_column=Column(Text))
    base_price: float = Field(default=0.0)
    standard_size: Optional[str] = Field(default=None, max_length=50) # BoxStandard Enum Name
    thickness_mm: Optional[float] = Field(default=None)              # 도서 두께 (mm)
    width_mm: Optional[float] = Field(default=185.0)                  # 도서 가로 (mm)
    depth_mm: Optional[float] = Field(default=257.0)                  # 도서 세로/깊이 (mm)
    weight_g: Optional[float] = Field(default=650.0)                  # 도서 중량/무게 (g)
    page_count: Optional[int] = Field(default=380)                    # 도서 총 페이지 수 (p)
    calc_source: Optional[str] = Field(default="ALADIN_REAL_SPEC", max_length=50) # 물리 규격 수집 출처
    virtual_stock: int = Field(default=0)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=now_kst)
    updated_at: datetime = Field(default_factory=now_kst)

class InboundJob(SQLModel, table=True):
    __tablename__ = "inbound_jobs"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    inbound_type: str = Field(max_length=50) # InboundTypeEnum
    status: str = Field(max_length=50) # InboundStatusEnum
    supplier_name: Optional[str] = Field(default=None, max_length=255)
    created_at: datetime = Field(default_factory=now_kst)
    updated_at: datetime = Field(default_factory=now_kst)

class InboundItem(SQLModel, table=True):
    __tablename__ = "inbound_items"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    inbound_job_id: UUID = Field(foreign_key="inbound_jobs.id", ondelete="CASCADE")
    book_id: UUID = Field(foreign_key="books.id", ondelete="RESTRICT")
    quantity: int = Field(default=0)
    created_at: datetime = Field(default_factory=now_kst)
    updated_at: datetime = Field(default_factory=now_kst)

class Location(SQLModel, table=True):
    __tablename__ = "locations"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    zone: str = Field(max_length=50)
    rack: str = Field(max_length=50)
    shelf: str = Field(max_length=50)
    barcode: str = Field(max_length=255, unique=True, index=True)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=now_kst)
    updated_at: datetime = Field(default_factory=now_kst)

class Inventory(SQLModel, table=True):
    __tablename__ = "inventory"
    __table_args__ = (
        UniqueConstraint("book_id", "location_id", name="uq_inventory_book_loc"),
    )
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    book_id: UUID = Field(foreign_key="books.id", ondelete="CASCADE")
    location_id: UUID = Field(foreign_key="locations.id", ondelete="RESTRICT")
    quantity: int = Field(default=0)
    created_at: datetime = Field(default_factory=now_kst)
    updated_at: datetime = Field(default_factory=now_kst)

class InventoryUsedItem(SQLModel, table=True):
    __tablename__ = "inventory_used_items"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    book_id: UUID = Field(foreign_key="books.id", ondelete="CASCADE")
    location_id: UUID = Field(foreign_key="locations.id", ondelete="RESTRICT")
    lpn_barcode: str = Field(max_length=255, unique=True, index=True)
    ubci_score: Optional[int] = Field(default=None)
    condition_grade: str = Field(max_length=20) # ConditionGradeEnum
    certificate_url: Optional[str] = Field(default=None, max_length=255)
    item_status: str = Field(default="IN_STOCK", max_length=20) # ItemStatusEnum
    source_job_id: Optional[UUID] = Field(default=None, foreign_key="return_jobs.id", ondelete="SET NULL")

    # 이 품목의 등급을 최종 확정한 주체.
    # [수정 이력] 이전에는 검수자 정보를 담을 컬럼 자체가 없어서, 재고 상세/보증서 API가
    # "WM2608001" / "HITL - WM2608001 (장문경)" 문자열을 하드코딩해 내려주고 있었다.
    # 누가 판정했는지가 실제로는 어디에도 기록되지 않던 상태.
    #   inspection_source: AI_AUTO(파이프라인 자동 확정) | HITL(관리자 수동 결재) | MANUAL(현장 수기)
    #   inspected_by     : 사번 또는 판정 주체 식별자 (AI_AUTO면 사용 모델/파이프라인 명)
    inspection_source: str = Field(default="AI_AUTO", max_length=20)
    inspected_by: Optional[str] = Field(default=None, max_length=100)
    inspected_at: Optional[datetime] = Field(default=None)

    created_at: datetime = Field(default_factory=now_kst)
    updated_at: datetime = Field(default_factory=now_kst)

class Order(SQLModel, table=True):
    __tablename__ = "orders"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    customer_name: Optional[str] = Field(default=None, max_length=255)
    type: str = Field(max_length=20) # OrderTypeEnum
    total_price: float
    status: str = Field(max_length=20) # OrderStatusEnum
    created_at: datetime = Field(default_factory=now_kst)
    updated_at: datetime = Field(default_factory=now_kst)

class OrderItem(SQLModel, table=True):
    __tablename__ = "order_items"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    order_id: UUID = Field(foreign_key="orders.id", ondelete="CASCADE")
    book_id: UUID = Field(foreign_key="books.id", ondelete="RESTRICT")
    quantity: int = Field(default=1)
    unit_price: float = Field(default=0.0)
    # 주문 시점 재고 유형 선호: "NEW" | "USED" | None(중고 우선 자동 할당)
    condition_pref: Optional[str] = Field(default=None, max_length=10)
    created_at: datetime = Field(default_factory=now_kst)
    updated_at: datetime = Field(default_factory=now_kst)

class PickingInstruction(SQLModel, table=True):
    """
    AI 피킹 지시서 헤더.
    할당(어느 재고를 뺄지)·피킹 순서(pick_seq)는 결정론적 규칙 엔진(FIFO + Zone 동선)이 확정하고,
    LLM은 route_summary / worker_note 내러티브 생성에만 관여한다 (순환 논리 방지 아키텍처).
    """
    __tablename__ = "picking_instructions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    order_id: UUID = Field(foreign_key="orders.id", ondelete="CASCADE", index=True)
    instruction_no: str = Field(max_length=50, unique=True, index=True)  # PICK-YYMMDD-####
    status: str = Field(default="PENDING", max_length=20)  # PickingInstructionStatusEnum
    total_items: int = Field(default=0)      # 총 피킹 대상 권수 (수량 합)
    picked_items: int = Field(default=0)     # 피킹 완료 권수
    route_summary: Optional[str] = Field(default=None, sa_column=Column(Text))  # LLM 동선 요약
    worker_note: Optional[str] = Field(default=None, sa_column=Column(Text))    # LLM 작업자 지시문
    ai_source: str = Field(default="RULE_FIFO_ZONE+LLM_NARRATIVE", max_length=50)
    accepted_by: Optional[str] = Field(default=None, max_length=50)   # 지시서 수락 작업자 사번
    accepted_at: Optional[datetime] = Field(default=None)
    box_id: Optional[str] = Field(default=None, max_length=20)       # 패킹 확정 박스 (BOOK-S1 등)
    cushion_name: Optional[str] = Field(default=None, max_length=100)  # 확정 완충재 (worker 포장 가이드용)
    cj_waybill_no: Optional[str] = Field(default=None, max_length=50)  # 발급 송장 번호
    packed_at: Optional[datetime] = Field(default=None)               # 송장 발급(패킹 확정) 시각
    shipped_at: Optional[datetime] = Field(default=None)              # worker 포장 완료(최종 출고) 시각
    created_at: datetime = Field(default_factory=now_kst)
    updated_at: datetime = Field(default_factory=now_kst)

class PickingInstructionItem(SQLModel, table=True):
    """
    피킹 지시서 라인 아이템.
    - 신품(NEW): book 단위 + quantity N권, 스캔 매칭 키 = ISBN
    - 중고(USED): LPN 개별 단위 (quantity 항상 1), 스캔 매칭 키 = lpn_barcode
    zone/rack/shelf는 지시서 발행 시점 위치를 비정규화 저장 (지시서는 발행 시점 스냅샷).
    """
    __tablename__ = "picking_instruction_items"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    instruction_id: UUID = Field(foreign_key="picking_instructions.id", ondelete="CASCADE", index=True)
    order_item_id: Optional[UUID] = Field(default=None, foreign_key="order_items.id", ondelete="SET NULL")
    book_id: UUID = Field(foreign_key="books.id", ondelete="RESTRICT")
    used_item_id: Optional[UUID] = Field(default=None, foreign_key="inventory_used_items.id", ondelete="SET NULL")
    stock_type: str = Field(max_length=10)  # "NEW" | "USED"
    lpn_barcode: Optional[str] = Field(default=None, max_length=255)  # USED 전용
    isbn: str = Field(max_length=13)        # 스캐너 매칭용 비정규화
    title: str = Field(max_length=255)      # 지시서 출력용 비정규화
    quantity: int = Field(default=1)
    picked_quantity: int = Field(default=0)
    zone: str = Field(default="A", max_length=50)
    # 위치 표기는 zone-rack-shelf 무패딩 정본(예: A-1-1). 패딩을 넣으면 locations의
    # 실제 값과 어긋나 같은 위치가 두 표기로 표시된다.
    rack: str = Field(default="1", max_length=50)
    shelf: str = Field(default="1", max_length=50)
    pick_seq: int = Field(default=1)        # 동선 정렬 피킹 순서
    unit_price: float = Field(default=0.0)  # 주문 시점 확정 권당 도매가
    status: str = Field(default="PENDING", max_length=20)  # PickingItemStatusEnum
    picked_at: Optional[datetime] = Field(default=None)
    picked_by: Optional[str] = Field(default=None, max_length=50)
    created_at: datetime = Field(default_factory=now_kst)
    updated_at: datetime = Field(default_factory=now_kst)

class ReturnJob(SQLModel, table=True):
    __tablename__ = "return_jobs"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    order_id: Optional[UUID] = Field(default=None, foreign_key="orders.id", ondelete="SET NULL")
    book_id: UUID = Field(foreign_key="books.id", ondelete="CASCADE")
    task_id: Optional[str] = Field(default=None, max_length=255)
    status: str = Field(max_length=20) # JobStatusEnum
    mode: str = Field(default="RETURN", max_length=50)
    
    # JSONB 컬럼 매핑 (PostgreSQL 전용 고성능 JSON)
    image_urls: List[str] = Field(default=[], sa_column=Column(JSONB))
    ubci_score: Optional[int] = Field(default=None)
    agent_logs: Dict[str, Any] = Field(default={}, sa_column=Column(JSONB))
    final_report: Optional[str] = Field(default=None)
    latency_ms: Optional[int] = Field(default=None)
    retry_count: int = Field(default=0)
    
    created_at: datetime = Field(default_factory=now_kst)
    updated_at: datetime = Field(default_factory=now_kst)

class OrderProposalStatusEnum(str, Enum):
    PENDING = "PENDING"      # AI 제안 생성됨 - 관리자 결재 대기 (칸반 1열)
    APPROVED = "APPROVED"    # 관리자 승인 - AUTO_PO Order 생성 + 신품 Fast-Track 입고 완료 (칸반 2열)
    DISMISSED = "DISMISSED"  # 관리자 기각 (칸반 3열)

class OrderProposalTriggerEnum(str, Enum):
    INSPECTION_REJECT = "INSPECTION_REJECT"  # 입고 검수 반려(매입 불가) 이벤트 트리거
    SAFETY_STOCK = "SAFETY_STOCK"            # 저재고 스캔 트리거
    MANUAL = "MANUAL"                        # 관리자 수동 생성

class OrderProposal(SQLModel, table=True):
    """
    AI 자동 발주 제안(Restock Proposal) - SCM 칸반보드의 카드 1장.

    [설계 원칙 - 판정과 집행의 분리]
    Restock Agent(LLM)는 이 테이블에 PENDING 제안을 "적재"할 수만 있고, 실제 발주(Order
    AUTO_PO 생성)와 신품 재고 편입은 관리자가 칸반에서 승인(APPROVED)하는 시점에만 집행된다.
    LLM이 금전적 확정을 직접 내리지 못하게 하는 HITL 게이트 - 검수 파이프라인의
    auto_refund_eligible 플래그 집행 구조와 동일한 문법이다.

    수집 시점 수치(current_stock/sales_velocity_30d 등)는 제안 근거의 감사 추적을 위해
    비정규화 스냅샷으로 보존한다 (재고가 변해도 "제안 당시 근거"는 남아야 한다).
    """
    __tablename__ = "order_proposals"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    book_id: UUID = Field(foreign_key="books.id", ondelete="RESTRICT", index=True)
    isbn: str = Field(max_length=13)          # 조회 편의용 비정규화
    title: str = Field(max_length=255)        # 칸반 카드 출력용 비정규화
    source_job_id: Optional[UUID] = Field(default=None, foreign_key="return_jobs.id", ondelete="SET NULL")
    trigger_type: str = Field(default="INSPECTION_REJECT", max_length=30)  # OrderProposalTriggerEnum
    reject_reason_code: Optional[str] = Field(default=None, max_length=50)  # DMG_EXT_WET 등

    # --- Collector 수집 스냅샷 (결정론적) ---
    current_stock: int = Field(default=0)         # 가용 재고 = 신품 virtual_stock + 중고 IN_STOCK 합산
    sales_velocity_30d: int = Field(default=0)    # 최근 30일 출고량 (InventoryLog OUTBOUND 집계)
    rejected_quantity: int = Field(default=0)     # 이번 반려로 소실된 매입 예정 수량
    baseline_quantity: int = Field(default=0)     # 결정론적 안전재고 산식 기준 수량 (Validator 클램프 앵커)

    # --- Restock Agent 제안 (LLM, Validator 클램프 통과 값) ---
    proposed_quantity: int = Field(default=0)
    urgency: str = Field(default="MEDIUM", max_length=10)  # CRITICAL/HIGH/MEDIUM/LOW
    reasoning: str = Field(default="", sa_column=Column(Text))
    ai_source: str = Field(default="LLM_GPT4O_MINI", max_length=30)  # LLM_GPT4O_MINI | FALLBACK_RULE

    unit_cost: float = Field(default=0.0)       # 도매가 (base_price * 0.6)
    estimated_cost: float = Field(default=0.0)  # unit_cost * proposed_quantity

    # --- 관리자 결재 (집행 기록) ---
    status: str = Field(default="PENDING", max_length=20, index=True)  # OrderProposalStatusEnum
    decided_by: Optional[str] = Field(default=None, max_length=100)    # 결재자 사번 (이름)
    decided_at: Optional[datetime] = Field(default=None)
    order_id: Optional[UUID] = Field(default=None, foreign_key="orders.id", ondelete="SET NULL")  # 승인 시 생성된 AUTO_PO

    created_at: datetime = Field(default_factory=now_kst)
    updated_at: datetime = Field(default_factory=now_kst)

class InventoryLog(SQLModel, table=True):
    __tablename__ = "inventory_logs"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    transaction_type: str = Field(max_length=50) # TransactionTypeEnum
    book_id: UUID = Field(foreign_key="books.id", ondelete="CASCADE")
    condition_grade: str = Field(max_length=20) # ConditionGradeEnum
    quantity_change: int
    target_lpn: Optional[str] = Field(default=None, max_length=255)
    picked_location: Optional[str] = Field(default=None, max_length=50)
    created_at: datetime = Field(default_factory=now_kst)
    updated_at: datetime = Field(default_factory=now_kst)

class Board(SQLModel, table=True):
    __tablename__ = "boards"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    job_id: Optional[UUID] = Field(default=None, foreign_key="return_jobs.id", ondelete="CASCADE")
    ticket_status: str = Field(max_length=20) # BoardTicketStatusEnum
    created_at: datetime = Field(default_factory=now_kst)
    updated_at: datetime = Field(default_factory=now_kst)

class BoardPost(SQLModel, table=True):
    __tablename__ = "board_posts"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    author_id: UUID = Field(foreign_key="users.id", ondelete="CASCADE")
    category: str = Field(max_length=20) # BoardCategoryEnum
    title: str = Field(max_length=255)
    content: str
    attachment_paths: List[str] = Field(default=[], sa_column=Column(JSONB))
    created_at: datetime = Field(default_factory=now_kst)
    updated_at: datetime = Field(default_factory=now_kst)

class BoardComment(SQLModel, table=True):
    __tablename__ = "board_comments"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    post_id: UUID = Field(foreign_key="board_posts.id", ondelete="CASCADE")
    author_id: UUID = Field(foreign_key="users.id", ondelete="CASCADE")
    content: str
    created_at: datetime = Field(default_factory=now_kst)
    updated_at: datetime = Field(default_factory=now_kst)

class Notification(SQLModel, table=True):
    """
    WMS 전역 알림 이력.

    [수정 이력 2026-08-04] 종전에는 알림을 저장하는 테이블 자체가 없었다. 프론트
    Header.tsx가 하드코딩된 더미 4건을 useState 초기값으로 들고 있었고, SSE로 들어온
    실시간 이벤트는 메모리에만 쌓여 새로고침하면 전부 사라졌다. 읽음 상태도 마찬가지.
    또한 notifications:global 채널에 발행하는 곳이 데모용 /trigger-fds 하나뿐이라
    실제 파이프라인 사건(HITL 이관, 검수 실패, 발주 제안)은 알림이 되지 않았다.

    [설계 노트] is_read는 관제 콘솔 단위의 전역 읽음 상태다. 사용자별 읽음 분리가
    필요해지면 notification_reads(user_id, notification_id) 조인 테이블을 추가한다.
    현 단계에서는 단일 관제실 운용을 전제로 컬럼 하나로 유지한다.
    """
    __tablename__ = "notifications"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    # 이벤트 종류: AGENT_ERROR / HITL_REQUIRED / RESTOCK_PROPOSAL / FDS_ALERT / INSPECTION_DONE
    type: str = Field(max_length=40, index=True)
    # INFO / WARN / CRITICAL - 프론트 뱃지 색상 결정
    severity: str = Field(default="INFO", max_length=20)
    # 화면 뱃지에 표시할 한국어 분류명 (예: "자동발주 알림")
    category: str = Field(max_length=50)
    title: str = Field(max_length=255)
    description: Optional[str] = Field(default=None, sa_column=Column(Text))
    # 알림 클릭 시 이동할 프론트 경로 (예: /admin/hitl)
    link_url: Optional[str] = Field(default=None, max_length=255)
    # 원인이 된 도메인 객체 추적용 (RETURN_JOB / INVENTORY_ITEM / FDS_REPORT / ORDER_PROPOSAL)
    ref_type: Optional[str] = Field(default=None, max_length=40)
    ref_id: Optional[str] = Field(default=None, max_length=100)
    # 이 알림을 볼 역할. None이면 전체 공개 (WORKER에게 FDS 알림을 띄우지 않기 위함)
    target_role: Optional[str] = Field(default=None, max_length=20)
    is_read: bool = Field(default=False, index=True)
    read_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=now_kst, index=True)


class FdsReport(SQLModel, table=True):
    """
    FDS(Fraud Detection System) 적발 이력.

    [수정 이력 2026-08-04] 이 테이블은 init.sql에 정의만 있고 어떤 코드도 INSERT하지 않던
    죽은 테이블이었다(0건). 룰 엔진(app/domains/fds/service.py) 신설과 함께 실적재를 시작하며,
    fraud_score 등 수치는 결정론적 룰 엔진이 산출하고 fraud_reason/recommended_action 서술만
    FDS Analyst Agent(gpt-4o-mini)가 생성한다.
    """
    __tablename__ = "fds_reports"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    # 적발 대상 표시명 (고객사명 또는 관리자 사번 - target_type이 구분)
    customer_name: str = Field(max_length=255)
    fraud_score: int
    fraud_reason: Optional[str] = Field(default=None, max_length=255)
    # 발동한 탐지 룰: R1_BLIND_APPROVAL / R2_GRADE_OVERRIDE / R3_NIGHT_BULK / R4_RETURN_ABUSE / SIMULATED
    rule_code: Optional[str] = Field(default=None, max_length=30)
    # 적발 대상 유형: CUSTOMER(고객사) / ADMIN(내부 관리자)
    target_type: Optional[str] = Field(default=None, max_length=20)
    # Analyst Agent가 생성한 권고 조치 (예: "해당 관리자 결재 이력 표본 재검토 권고")
    recommended_action: Optional[str] = Field(default=None, sa_column=Column(Text))
    detected_at: datetime = Field(default_factory=now_kst)

class WeeklyInsight(SQLModel, table=True):
    __tablename__ = "weekly_insights"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    report_week: str = Field(unique=True, index=True, max_length=20)
    saved_labor_cost_krw: int = Field(default=0)
    top_defective_publishers: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSONB))
    location_hotspots: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSONB))
    logistics_hotspots: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSONB))
    predicted_returns: int = Field(default=0)
    # Insight Analyst Agent(gpt-4o-mini)가 집계 수치를 바탕으로 생성한 주간 경영 서사
    # (수치 자체는 전부 결정론적 SQL 집계 - LLM은 해석 문장만 생성)
    ai_narrative: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=now_kst)

class AdminAuditLog(SQLModel, table=True):
    __tablename__ = "admin_audit_logs"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    admin_id: UUID = Field(foreign_key="users.id", ondelete="RESTRICT")
    target_type: str = Field(max_length=50) # e.g., "RETURN_JOB"
    target_id: str = Field(max_length=255) # job_id string or uuid
    action: str = Field(max_length=50) # e.g., "APPROVE_DOWNGRADE", "REJECT_RETURN"
    previous_state: str = Field(max_length=255)
    new_state: str = Field(max_length=255)
    
    # Advanced HITL Metrics
    target_grade: Optional[str] = Field(default=None, max_length=10)
    primary_reason_code: Optional[str] = Field(default=None, max_length=50)
    defect_coordinates: Optional[List[Dict[str, Any]]] = Field(default=None, sa_column=Column(JSONB))
    review_duration_ms: Optional[int] = Field(default=None)
    
    created_at: datetime = Field(default_factory=now_kst)


class LabelPrintJob(SQLModel, table=True):
    """
    LPN/UBCI 라벨 인쇄 작업 큐 (클라우드 배포용).

    LABEL_PRINT_MODE=QUEUE일 때 /labels/print가 직접 전송 대신 이 테이블에 적재하고,
    창고 PC의 프린트 브리지 에이전트(scripts/print_bridge_agent.py)가 폴링해
    로컬 프린터(Raw TCP 9100)로 중계한 뒤 결과를 보고한다.
    """
    __tablename__ = "label_print_jobs"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    lpn: str = Field(max_length=64, index=True)
    mode: str = Field(default="LPN", max_length=8)  # LPN | UBCI
    zpl: str
    status: str = Field(default="PENDING", max_length=12, index=True)  # PENDING | PRINTED | FAILED
    error: Optional[str] = Field(default=None, max_length=500)
    created_at: datetime = Field(default_factory=now_kst)
    printed_at: Optional[datetime] = Field(default=None)
