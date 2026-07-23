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
    base_price DECIMAL NOT NULL DEFAULT 0,
    standard_size VARCHAR(50),
    thickness_mm INT,
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
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

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

-- 12. FDS 적발 이력 (fds_reports)
CREATE TABLE fds_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_name VARCHAR(255) NOT NULL,
    fraud_score INT NOT NULL,
    fraud_reason VARCHAR(255),
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
    created_at TIMESTAMP DEFAULT NOW()
);
