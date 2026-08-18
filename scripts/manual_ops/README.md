# manual_ops — 운영 데이터 직접 조작 스크립트

> 🔴 **이 폴더의 스크립트는 DB와 S3를 직접 건드린다. 읽지 않고 실행하지 않는다.**

`scratch/`에 섞여 있던 것을 성격별로 분리하면서 여기로 모았다. 애플리케이션 코드 경로가
아니며, 어떤 테스트도 이 파일들을 import 하지 않는다.

## 무엇이 있나

| 파일 | 하는 일 |
| --- | --- |
| `scratch_direct_pg.py` · `_pg2` · `_pg3` | PostgreSQL 직접 접속 조회·수정 |
| `scratch_update_lpn_prefix.py` · `_prefix2` | LPN 접두사 일괄 변경 |
| `scratch_restore_lpn_station_a.py` | A 스테이션 LPN 복구 |
| `scratch_check_station_a_seq.py` | 시퀀스 상태 점검 (읽기 전용) |
| `scratch_randomize_lines_bcde.py` | 시연용 라인 데이터 셔플 |
| `scratch_s3.py` · `_s3_pure` · `_upload_s3` | S3 객체 조회·업로드 |
| `seed_mock_orders.py` | 목업 주문 생성 |

## 실행 전 확인

1. **어느 DB를 보는지** — `DATABASE_URL`이 로컬인지 운영인지 확인한다.
   운영 DB는 라이브 시연 대상이라 `SELECT` 외에는 원칙적으로 금지다.
2. **되돌릴 수 있는지** — 위 스크립트 대부분은 롤백 경로가 없다.
3. **정식 경로가 있는지** — 재고·주문 조작은 `scripts/seed/`와 API를 쓴다.
   여기 있는 것은 그 경로가 없던 시절의 임시 도구다.

되살릴 일이 없다고 판단되면 지워도 된다. 다만 "그때 데이터를 어떻게 만들었나"를
설명해야 할 때 근거가 되므로 지금은 남겨 둔다.
