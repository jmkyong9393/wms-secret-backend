-- WMS Core Database DDL (PostgreSQL)
-- 기반 문서: 01_엔지니어링_산출물.md (ver 1.5.2.0 - 단품 추적 LPN 바코드 체계 및 테이블 통폐합)

-- 1. UUID 생성을 위한 확장 모듈 활성화
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- 2. 사내 계정 및 회원 (users)
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(100) UNIQUE,
    name VARCHAR(50) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    phone_number VARCHAR(50),
    address VARCHAR(255),
    role VARCHAR(20) NOT NULL,
    status VARCHAR(20) DEFAULT 'ACTIVE',
    must_change_password BOOLEAN DEFAULT FALSE,
    -- 개인정보 수집·이용 동의 시각 (개인정보 보호법 제15조). NULL = 미동의.
    privacy_consent_at TIMESTAMP,
    last_login TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 3. 도서 마스터 (books)
CREATE TABLE books (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(255) NOT NULL,
    author VARCHAR(255),
    publisher VARCHAR(255),
    published_date VARCHAR(50),
    isbn VARCHAR(13) UNIQUE NOT NULL,
    category_type VARCHAR(50) DEFAULT 'GENERAL',
    cover_image_url VARCHAR(255),
    description TEXT,
    base_price DECIMAL NOT NULL DEFAULT 0,
    standard_size VARCHAR(50),
    -- 택배 송장/3D Bin Packing용 실측 물리 규격 (알라딘 OptResult=packing 연동, GET /inbound/book-lookup)
    thickness_mm DOUBLE PRECISION,
    width_mm DOUBLE PRECISION DEFAULT 185.0,
    depth_mm DOUBLE PRECISION DEFAULT 257.0,
    weight_g DOUBLE PRECISION DEFAULT 650.0,
    page_count INT DEFAULT 380,
    calc_source VARCHAR(50) DEFAULT 'ALADIN_REAL_SPEC',
    virtual_stock INT DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 4. 입고 트랜잭션 (inbound_jobs, inbound_items)
CREATE TABLE inbound_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    inbound_type VARCHAR(50) NOT NULL,
    status VARCHAR(50) NOT NULL,
    supplier_name VARCHAR(255),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE inbound_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    inbound_job_id UUID NOT NULL REFERENCES inbound_jobs(id) ON DELETE CASCADE,
    book_id UUID NOT NULL REFERENCES books(id) ON DELETE RESTRICT,
    quantity INT NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 5. 창고 진열대 (locations)
CREATE TABLE locations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    zone VARCHAR(50) NOT NULL,
    rack VARCHAR(50) NOT NULL,
    shelf VARCHAR(50) NOT NULL,
    barcode VARCHAR(255) UNIQUE NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 6. 새 상품 전용 묶음 재고 (inventory)
CREATE TABLE inventory (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    book_id UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    location_id UUID NOT NULL REFERENCES locations(id) ON DELETE RESTRICT,
    quantity INT NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (book_id, location_id)
);

-- 7. 주문 및 발주 (orders) (이전 8번)
CREATE TABLE orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_name VARCHAR(255),
    type VARCHAR(20) NOT NULL,
    total_price DECIMAL NOT NULL,
    status VARCHAR(20) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE order_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    book_id UUID NOT NULL REFERENCES books(id) ON DELETE RESTRICT,
    quantity INT NOT NULL DEFAULT 1,
    unit_price DECIMAL NOT NULL DEFAULT 0,
    -- 주문 시점 재고 유형 선호: "NEW" | "USED" | NULL(중고 우선 자동 할당)
    condition_pref VARCHAR(10),
    -- 주문 시 지정된 중고 개체(LPN). 없으면 할당 엔진이 같은 책의 다른 LPN을 FIFO로 고른다.
    -- FK는 inventory_used_items가 아래에서 생성된 뒤 ALTER로 건다 (생성 순서 때문).
    used_item_id UUID,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 8. 반품 작업 및 에이전트 로그 (return_jobs) (이전 9번)
CREATE TABLE return_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID REFERENCES orders(id) ON DELETE SET NULL,
    book_id UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    task_id VARCHAR(255),
    status VARCHAR(20) NOT NULL,
    mode VARCHAR(50) DEFAULT 'RETURN',
    image_urls JSONB,
    ubci_score INT,
    agent_logs JSONB,
    final_report TEXT,
    latency_ms INT,
    retry_count INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 9. 중고/반품 전용 단품 재고 - LPN 분리 (inventory_used_items) (이전 7번)
-- return_jobs가 생성된 이후이므로 FK 정상 연결됨
CREATE TABLE inventory_used_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    book_id UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    location_id UUID NOT NULL REFERENCES locations(id) ON DELETE RESTRICT,
    lpn_barcode VARCHAR(255) UNIQUE NOT NULL,
    ubci_score INT,
    condition_grade VARCHAR(20) NOT NULL,
    certificate_url VARCHAR(255),
    item_status VARCHAR(20) DEFAULT 'IN_STOCK',
    source_job_id UUID REFERENCES return_jobs(id) ON DELETE SET NULL,
    -- 이 품목의 등급을 최종 확정한 주체 (AI_AUTO / HITL / PENDING_HITL / MANUAL).
    -- 이 컬럼이 없던 시절에는 재고 상세 API가 "HITL - WM2608001 (장문경)" 문자열을
    -- 하드코딩해 모든 품목에 같은 담당자를 표시했다.
    inspection_source VARCHAR(20) DEFAULT 'AI_AUTO',
    inspected_by VARCHAR(100),
    inspected_at TIMESTAMP,
    -- LPN 선부착(라벨 발급) 시점 작업자 사번. 검수 접수 전 품목의 작업자 표시용.
    prelabel_worker_id VARCHAR(64),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- order_items.used_item_id FK — order_items가 먼저 생성되므로 여기서 건다.
ALTER TABLE order_items
    ADD CONSTRAINT fk_order_items_used_item_id
    FOREIGN KEY (used_item_id) REFERENCES inventory_used_items(id) ON DELETE SET NULL;

-- 10. 통합 재고 원장 (inventory_logs)
CREATE TABLE inventory_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_type VARCHAR(50) NOT NULL,
    book_id UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    condition_grade VARCHAR(20) NOT NULL,
    quantity_change INT NOT NULL,
    target_lpn VARCHAR(255),
    picked_location VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 11. 고밀도 데이터 그리드 티켓 및 일반 게시판 (boards, board_posts)
CREATE TABLE boards (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID REFERENCES return_jobs(id) ON DELETE CASCADE,
    ticket_status VARCHAR(20) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE board_posts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    author_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    category VARCHAR(20) NOT NULL,
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    attachment_paths JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 11-1. WMS 전역 알림 이력 (notifications)
-- 이 테이블이 없던 시절에는 프론트 Header.tsx가 더미 4건을 하드코딩해 들고 있었고,
-- SSE로 받은 실시간 이벤트는 메모리에만 쌓여 새로고침 시 소실됐다.
CREATE TABLE notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- AGENT_ERROR / HITL_REQUIRED / RESTOCK_PROPOSAL / FDS_ALERT / INSPECTION_DONE
    type VARCHAR(40) NOT NULL,
    severity VARCHAR(20) DEFAULT 'INFO',   -- INFO / WARN / CRITICAL
    category VARCHAR(50) NOT NULL,         -- 화면 뱃지 표기용 한국어 분류명
    title VARCHAR(255) NOT NULL,
    description TEXT,
    link_url VARCHAR(255),                 -- 클릭 시 이동할 프론트 경로
    ref_type VARCHAR(40),                  -- RETURN_JOB / INVENTORY_ITEM / FDS_REPORT 등
    ref_id VARCHAR(100),
    target_role VARCHAR(20),               -- NULL이면 전체 공개
    is_read BOOLEAN DEFAULT FALSE,
    read_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX idx_notifications_created_at ON notifications (created_at DESC);
CREATE INDEX idx_notifications_is_read ON notifications (is_read);

-- 12. FDS 적발 이력 (fds_reports)
CREATE TABLE fds_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_name VARCHAR(255) NOT NULL,
    fraud_score INT NOT NULL,
    fraud_reason VARCHAR(255),
    -- 발동 룰: R1_BLIND_APPROVAL / R2_GRADE_OVERRIDE / R3_NIGHT_BULK / R4_RETURN_ABUSE / SIMULATED
    rule_code VARCHAR(30),
    -- 적발 대상 유형: CUSTOMER(고객사) / ADMIN(내부 관리자)
    target_type VARCHAR(20),
    -- FDS Analyst Agent(gpt-4o-mini) 생성 권고 조치
    recommended_action TEXT,
    detected_at TIMESTAMP DEFAULT NOW()
);

-- 13. 주간 대시보드 스냅샷 (weekly_insights)
CREATE TABLE weekly_insights (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_week VARCHAR(20) UNIQUE NOT NULL,
    saved_labor_cost_krw INT DEFAULT 0,
    top_defective_publishers JSONB,
    location_hotspots JSONB,
    logistics_hotspots JSONB,
    predicted_returns INT DEFAULT 0,
    -- Insight Analyst Agent(gpt-4o-mini) 생성 주간 경영 서사 (수치는 결정론적 집계)
    ai_narrative TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 14. AI 피킹 지시서 (picking_instructions, picking_instruction_items)
-- app/models/wms.py의 PickingInstruction/PickingInstructionItem 모델에 대응.
-- 할당·피킹 순서(pick_seq)는 결정론적 규칙 엔진(FIFO + Zone 동선)이 확정하고,
-- LLM은 route_summary/worker_note 내러티브 생성에만 관여한다.
CREATE TABLE picking_instructions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    instruction_no VARCHAR(50) UNIQUE NOT NULL,
    status VARCHAR(20) DEFAULT 'PENDING',
    total_items INT DEFAULT 0,
    picked_items INT DEFAULT 0,
    route_summary TEXT,
    worker_note TEXT,
    ai_source VARCHAR(50) DEFAULT 'RULE_FIFO_ZONE+LLM_NARRATIVE',
    box_id VARCHAR(20),
    cj_waybill_no VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX idx_picking_instructions_order_id ON picking_instructions(order_id);

CREATE TABLE picking_instruction_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    instruction_id UUID NOT NULL REFERENCES picking_instructions(id) ON DELETE CASCADE,
    order_item_id UUID REFERENCES order_items(id) ON DELETE SET NULL,
    book_id UUID NOT NULL REFERENCES books(id) ON DELETE RESTRICT,
    used_item_id UUID REFERENCES inventory_used_items(id) ON DELETE SET NULL,
    stock_type VARCHAR(10) NOT NULL,
    lpn_barcode VARCHAR(255),
    isbn VARCHAR(13) NOT NULL,
    title VARCHAR(255) NOT NULL,
    quantity INT DEFAULT 1,
    picked_quantity INT DEFAULT 0,
    zone VARCHAR(50) DEFAULT 'A',
    rack VARCHAR(50) DEFAULT '01',
    shelf VARCHAR(50) DEFAULT '01',
    pick_seq INT DEFAULT 1,
    unit_price DECIMAL DEFAULT 0,
    status VARCHAR(20) DEFAULT 'PENDING',
    picked_at TIMESTAMP,
    picked_by VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX idx_picking_instruction_items_instruction_id ON picking_instruction_items(instruction_id);

-- 15. HITL 관리자 결재 감사 로그 (admin_audit_logs)
-- app/models/wms.py의 AdminAuditLog 모델에 대응. HITL 오버라이드(/admin/hitl/override) 시
-- 관리자의 판단 근거와 체류 시간을 규제 대응(ISMS-P)용으로 남긴다.
CREATE TABLE admin_audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    admin_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    target_type VARCHAR(50) NOT NULL,
    target_id VARCHAR(255) NOT NULL,
    action VARCHAR(50) NOT NULL,
    previous_state VARCHAR(255) NOT NULL,
    new_state VARCHAR(255) NOT NULL,
    target_grade VARCHAR(10),
    primary_reason_code VARCHAR(50),
    defect_coordinates JSONB,
    review_duration_ms INT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 16. AI 자동 발주 제안 (order_proposals) - SCM 칸반보드 카드
-- app/models/wms.py의 OrderProposal 모델에 대응. Restock 판정 그래프(Collector→Agent→Validator)가
-- 입고 검수 반려/저재고 스캔 시 PENDING 카드를 적재하고, 관리자가 칸반에서 승인해야만
-- Order(AUTO_PO) 생성 + 신품 Fast-Track 입고가 집행된다 (LLM 판정/집행 분리 게이트).
CREATE TABLE order_proposals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    book_id UUID NOT NULL REFERENCES books(id) ON DELETE RESTRICT,
    isbn VARCHAR(13) NOT NULL,
    title VARCHAR(255) NOT NULL,
    source_job_id UUID REFERENCES return_jobs(id) ON DELETE SET NULL,
    trigger_type VARCHAR(30) NOT NULL DEFAULT 'INSPECTION_REJECT',
    reject_reason_code VARCHAR(50),
    current_stock INT NOT NULL DEFAULT 0,
    sales_velocity_30d INT NOT NULL DEFAULT 0,
    rejected_quantity INT NOT NULL DEFAULT 0,
    baseline_quantity INT NOT NULL DEFAULT 0,
    proposed_quantity INT NOT NULL DEFAULT 0,
    urgency VARCHAR(10) NOT NULL DEFAULT 'MEDIUM',
    reasoning TEXT NOT NULL DEFAULT '',
    ai_source VARCHAR(30) NOT NULL DEFAULT 'LLM_GPT4O_MINI',
    unit_cost DOUBLE PRECISION NOT NULL DEFAULT 0,
    estimated_cost DOUBLE PRECISION NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    decided_by VARCHAR(100),
    decided_at TIMESTAMP,
    order_id UUID REFERENCES orders(id) ON DELETE SET NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX idx_order_proposals_book_id ON order_proposals(book_id);
CREATE INDEX idx_order_proposals_status ON order_proposals(status);
