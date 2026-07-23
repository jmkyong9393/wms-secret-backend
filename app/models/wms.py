from sqlmodel import SQLModel, Field, UniqueConstraint
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from typing import Optional, List, Dict, Any
from uuid import UUID, uuid4
from datetime import datetime
from enum import Enum
from sqlalchemy import Column, Enum as SQLEnum, Text

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
    last_login: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

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
    thickness_mm: Optional[int] = Field(default=None)
    virtual_stock: int = Field(default=0)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class InboundJob(SQLModel, table=True):
    __tablename__ = "inbound_jobs"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    inbound_type: str = Field(max_length=50) # InboundTypeEnum
    status: str = Field(max_length=50) # InboundStatusEnum
    supplier_name: Optional[str] = Field(default=None, max_length=255)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class InboundItem(SQLModel, table=True):
    __tablename__ = "inbound_items"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    inbound_job_id: UUID = Field(foreign_key="inbound_jobs.id", ondelete="CASCADE")
    book_id: UUID = Field(foreign_key="books.id", ondelete="RESTRICT")
    quantity: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class Location(SQLModel, table=True):
    __tablename__ = "locations"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    zone: str = Field(max_length=50)
    rack: str = Field(max_length=50)
    shelf: str = Field(max_length=50)
    barcode: str = Field(max_length=255, unique=True, index=True)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class Inventory(SQLModel, table=True):
    __tablename__ = "inventory"
    __table_args__ = (
        UniqueConstraint("book_id", "location_id", name="uq_inventory_book_loc"),
    )
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    book_id: UUID = Field(foreign_key="books.id", ondelete="CASCADE")
    location_id: UUID = Field(foreign_key="locations.id", ondelete="RESTRICT")
    quantity: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

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
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class Order(SQLModel, table=True):
    __tablename__ = "orders"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    customer_name: Optional[str] = Field(default=None, max_length=255)
    type: str = Field(max_length=20) # OrderTypeEnum
    total_price: float
    status: str = Field(max_length=20) # OrderStatusEnum
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class OrderItem(SQLModel, table=True):
    __tablename__ = "order_items"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    order_id: UUID = Field(foreign_key="orders.id", ondelete="CASCADE")
    book_id: UUID = Field(foreign_key="books.id", ondelete="RESTRICT")
    quantity: int = Field(default=1)
    unit_price: float = Field(default=0.0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

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
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class InventoryLog(SQLModel, table=True):
    __tablename__ = "inventory_logs"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    transaction_type: str = Field(max_length=50) # TransactionTypeEnum
    book_id: UUID = Field(foreign_key="books.id", ondelete="CASCADE")
    condition_grade: str = Field(max_length=20) # ConditionGradeEnum
    quantity_change: int
    target_lpn: Optional[str] = Field(default=None, max_length=255)
    picked_location: Optional[str] = Field(default=None, max_length=50)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class Board(SQLModel, table=True):
    __tablename__ = "boards"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    job_id: Optional[UUID] = Field(default=None, foreign_key="return_jobs.id", ondelete="CASCADE")
    ticket_status: str = Field(max_length=20) # BoardTicketStatusEnum
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class BoardPost(SQLModel, table=True):
    __tablename__ = "board_posts"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    author_id: UUID = Field(foreign_key="users.id", ondelete="CASCADE")
    category: str = Field(max_length=20) # BoardCategoryEnum
    title: str = Field(max_length=255)
    content: str
    attachment_paths: List[str] = Field(default=[], sa_column=Column(JSONB))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class FdsReport(SQLModel, table=True):
    __tablename__ = "fds_reports"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    customer_name: str = Field(max_length=255)
    fraud_score: int
    fraud_reason: Optional[str] = Field(default=None, max_length=255)
    detected_at: datetime = Field(default_factory=datetime.utcnow)

class WeeklyInsight(SQLModel, table=True):
    __tablename__ = "weekly_insights"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    report_week: str = Field(unique=True, index=True, max_length=20)
    saved_labor_cost_krw: int = Field(default=0)
    top_defective_publishers: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSONB))
    location_hotspots: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSONB))
    logistics_hotspots: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSONB))
    predicted_returns: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)

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
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
