"""FDS 시연용 적발 소재 시드 (R1 / R2 / R3).

[배경] FDS 룰 엔진은 100% 결정론적이라 실제 사기 패턴이 없으면 아무것도 적발하지 않는다.
운영 DB로 실스캔을 돌린 결과 적발 0건이었고(2026-08-12 실측), 시연 영상에서 FDS 관제 화면이
비어 보이는 문제가 있었다. **룰이나 임계값을 건드리지 않고**, 룰이 실제로 잡아낼 "소재"만
DB에 심어 스캔이 정직하게 탐지하도록 한다.

심는 것:
  R1_BLIND_APPROVAL : 1초 미만 HITL 결재 로그 (관측창 7일 내 비율 60% 이상이면 적발)
  R2_GRADE_OVERRIDE : AI 제안등급 대비 2단계 상향 승인 로그 (관측창 30일, 2건 이상)
  R3_NIGHT_BULK     : 야간(22~06시) 200만원 초과 B2B 주문 (신규 거래처는 절대하한만 적용)

설계 원칙:
  - **적발 대상 관리자는 조장 본인(WM2608001) 고정.** 팀원 실명이 "부정 의심" 기록으로
    남는 것을 피하기 위한 선택이며, --admin 으로 바꿀 수 있다.
  - R2는 **이미 종결된 검수 건**(APPROVED/REJECTED)만 참조한다. 결재 대기(HITL) 건을
    건드리면 시연에 쓸 소재가 사라진다 (조장 지시: C언어 Express 건 보존).
  - 심은 행은 전부 마커(`[FDS_DEMO_SEED]`)를 달아 --purge로 되돌릴 수 있다.
  - 주문은 SHIPPED(출고 완료)로 만든다 - 피킹 대기열을 비워둔 시연 시나리오를 깨지 않는다.

사용법:
    python -m app.scripts.seed_fds_demo             # dry-run (기본)
    python -m app.scripts.seed_fds_demo --apply     # 실제 반영
    python -m app.scripts.seed_fds_demo --purge --apply   # 심은 것 되돌리기
"""
import sys
from datetime import timedelta
from uuid import uuid4

from sqlmodel import Session, select

from app.db.session import engine
from app.models.wms import (
    AdminAuditLog, Book, Order, OrderItem, ReturnJob, User, now_kst,
)

APPLY = "--apply" in sys.argv
PURGE = "--purge" in sys.argv

# 심은 행 식별 마커. AdminAuditLog는 상태 필드에, Order는 고객명에 붙인다.
SEED_MARKER = "[FDS_DEMO_SEED]"
SEED_CUSTOMER = f"세종문고 야간발주점 {SEED_MARKER}"

# 적발 대상 관리자 (기본: 조장 본인)
TARGET_ADMIN = "WM2608001"
for i, a in enumerate(sys.argv):
    if a == "--admin" and i + 1 < len(sys.argv):
        TARGET_ADMIN = sys.argv[i + 1]

_GRADE_RANK = {"REJECT": 0, "NORMAL": 1, "GOOD": 2, "MINT": 3}
_TWO_STEPS_UP = {"REJECT": "GOOD", "NORMAL": "MINT"}


def purge(db: Session) -> None:
    logs = db.exec(
        select(AdminAuditLog).where(AdminAuditLog.new_state.like(f"%{SEED_MARKER}%"))
    ).all()
    orders = db.exec(
        select(Order).where(Order.customer_name.like(f"%{SEED_MARKER}%"))
    ).all()

    print(f"제거 대상: 감사로그 {len(logs)}건 / 주문 {len(orders)}건")
    if not APPLY:
        print("실제 반영하려면 --apply 를 붙이세요.")
        return

    for lg in logs:
        db.delete(lg)
    for o in orders:
        for oi in db.exec(select(OrderItem).where(OrderItem.order_id == o.id)).all():
            db.delete(oi)
        db.delete(o)
    db.commit()
    print("되돌리기 완료.")


def main() -> None:
    with Session(engine) as db:
        if PURGE:
            purge(db)
            return

        admin = db.exec(select(User).where(User.employee_id == TARGET_ADMIN)).first()
        if not admin:
            print(f"관리자를 찾을 수 없습니다: {TARGET_ADMIN}")
            return
        print(f"적발 대상 관리자: {admin.employee_id}({admin.name})  모드: {'APPLY' if APPLY else 'DRY-RUN'}")

        now = now_kst()
        planned = []

        # ---------- R1: 블라인드 결재 (관측창 내 평균 1초 미만) ----------
        # 룰이 최근 7일 관측창 + 최소 5건을 보므로(2026-08-12 개정), 그 창 안에
        # 짧은 결재를 심으면 된다. 종전 누적 평균 방식이었다면 같은 효과를 내는 데
        # 1,325건이 필요했다 - 지표를 무력화하는 방식이라 채택하지 않았다.
        from app.domains.fds.service import (
            BLIND_APPROVAL_MIN_SAMPLES, BLIND_APPROVAL_WINDOW_DAYS,
        )

        win_since = now - timedelta(days=BLIND_APPROVAL_WINDOW_DAYS)
        in_window = [
            x for x in db.exec(
                select(AdminAuditLog).where(AdminAuditLog.admin_id == admin.id)
            ).all()
            if x.review_duration_ms and x.created_at and x.created_at >= win_since
        ]
        r1_jobs = db.exec(
            select(ReturnJob).where(ReturnJob.status.in_(["APPROVED", "REJECTED"])).limit(12)
        ).all()

        r1_rows = []
        for idx, job in enumerate(r1_jobs):
            r1_rows.append(AdminAuditLog(
                id=uuid4(),
                admin_id=admin.id,
                target_type="RETURN_JOB",
                target_id=str(job.id),
                action="APPROVE_NORMAL",
                previous_state=f"HITL_REQUIRED {SEED_MARKER}",
                new_state=f"APPROVED {SEED_MARKER}",
                target_grade="NORMAL",
                primary_reason_code="CLEAN",
                review_duration_ms=380 + (idx % 5) * 90,  # 380~740ms (건당 검토시간)
                created_at=now - timedelta(hours=5, minutes=idx * 2),
            ))
        # 룰은 "임계 미만 비율"을 보므로(평균이 아니라) 같은 기준으로 예측한다.
        from app.domains.fds.service import (
            BLIND_APPROVAL_MIN_FAST_RATIO, BLIND_APPROVAL_THRESHOLD_MS,
        )

        win_ms = [x.review_duration_ms for x in in_window]
        all_ms = win_ms + [r.review_duration_ms for r in r1_rows]
        fast_n = len([d for d in all_ms if d < BLIND_APPROVAL_THRESHOLD_MS])
        ratio = fast_n / max(1, len(all_ms))
        verdict = "적발됨" if (len(all_ms) >= BLIND_APPROVAL_MIN_SAMPLES
                            and ratio >= BLIND_APPROVAL_MIN_FAST_RATIO) else "미적발"
        planned.append(
            f"R1 블라인드 결재: 감사로그 {len(r1_rows)}건 "
            f"(관측창 {BLIND_APPROVAL_WINDOW_DAYS}일 기존 {len(win_ms)}건 + 신규 {len(r1_rows)}건 "
            f"-> 1초 미만 {fast_n}/{len(all_ms)}건 = {ratio*100:.0f}%, "
            f"기준 {BLIND_APPROVAL_MIN_FAST_RATIO*100:.0f}% -> {verdict})"
        )

        # ---------- R2: 등급 오버라이드 남용 (2단계 상향 2건) ----------
        # 종결된 건만 참조한다 (결재 대기 건은 시연 소재라 보존).
        candidates = db.exec(
            select(ReturnJob).where(ReturnJob.status.in_(["APPROVED", "REJECTED"]))
        ).all()
        r2_rows = []
        for job in candidates:
            sg = ((job.agent_logs or {}).get("suggested_grade") or "").upper()
            if sg not in _TWO_STEPS_UP:
                continue
            up = _TWO_STEPS_UP[sg]
            r2_rows.append(AdminAuditLog(
                id=uuid4(),
                admin_id=admin.id,
                target_type="RETURN_JOB",
                target_id=str(job.id),
                action="APPROVE_UPGRADE",
                previous_state=f"{sg} {SEED_MARKER}",
                new_state=f"{up} {SEED_MARKER}",
                target_grade=up,
                primary_reason_code="MANUAL_OVERRIDE",
                review_duration_ms=520,
                created_at=now - timedelta(hours=3, minutes=len(r2_rows) * 11),
            ))
            if len(r2_rows) >= 3:
                break
        planned.append(f"R2 등급 오버라이드: 감사로그 {len(r2_rows)}건 (2단계 상향, 기준 2건 이상)")

        # ---------- R3: 야간 대량 주문 (22~06시 / 50만원 초과) ----------
        books = db.exec(select(Book).where(Book.base_price > 20000).limit(4)).all()
        r3_orders = []
        night_base = now.replace(hour=2, minute=40, second=0, microsecond=0)
        for n, (hour, price) in enumerate([(2, 4_800_000), (23, 6_300_000)]):
            when = (night_base - timedelta(days=n + 1)).replace(hour=hour)
            order = Order(
                id=uuid4(),
                customer_name=SEED_CUSTOMER,
                type="B2B_ORDER",
                total_price=float(price),
                status="SHIPPED",   # 피킹 대기열을 비워둔 시연 시나리오를 깨지 않는다
                created_at=when,
                updated_at=when,
            )
            r3_orders.append((order, books))
        planned.append(f"R3 야간 대량주문: 주문 {len(r3_orders)}건 "
                       f"(고객 '{SEED_CUSTOMER}', 480만/630만원, 절대하한 200만원). "
                       f"신규 거래처라 이력이 없어 배수 조건은 적용되지 않는다.")

        for line in planned:
            print("  -", line)

        if not APPLY:
            print("\nDRY-RUN입니다. 실제 반영하려면 --apply 를 붙이세요.")
            print("반영 후 FDS 스캔(POST /api/v1/fds/scan)을 실행해야 적발 리포트가 생성됩니다.")
            return

        for r in r1_rows + r2_rows:
            db.add(r)
        for order, bks in r3_orders:
            db.add(order)
            db.flush()
            for b in bks[:3]:
                db.add(OrderItem(
                    order_id=order.id, book_id=b.id, quantity=8,
                    unit_price=float(b.base_price or 25000), condition_pref="NEW",
                ))
        db.commit()
        print(f"\n완료: 감사로그 {len(r1_rows) + len(r2_rows)}건 + 주문 {len(r3_orders)}건 적재")
        print("이어서 FDS 스캔을 실행하세요: POST /api/v1/fds/scan")


if __name__ == "__main__":
    main()
