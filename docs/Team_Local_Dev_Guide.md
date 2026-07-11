# 🚀 WMS AI Platform: 팀원 로컬 개발 및 실행 가이드 (Local E2E Guide)

프론트엔드, 백엔드, AI 파트 개발자 여러분들이 로컬 환경에서 개발하고 테스트할 때 반드시 알아야 할 **통합 실행 가이드 및 협업 규칙**입니다.

---

## 1. 🐳 로컬 E2E 통합 실행 방법 (Docker Compose)

기존에는 프론트와 백엔드를 각각 따로 켜야 했지만, 이제 최상위(Root) 폴더에 통합된 `docker-compose.yml`이 구축되어 명령어 한 줄로 전체 인프라를 가동할 수 있습니다.

* **포함된 컨테이너 서비스:**
  * `wms-postgres` (DB - 5432)
  * `wms-redis` (Celery AI Message Broker - 6379)
  * `wms-api` (FastAPI 백엔드 - 8000)
  * `wms-worker` (Celery AI 워커)
  * `wms-frontend` (Next.js 웹 - 3000)

### 📌 실행 명령어
터미널을 열고 **프로젝트 최상위 폴더(예: `wms-ai-platform/` 등 본인이 Clone 받은 루트 폴더)** 로 이동한 뒤 아래 명령어를 실행하세요.

```bash
# 전체 시스템 백그라운드 구동 (최초 실행 시 빌드 진행)
docker-compose up -d --build

# 실시간 전체 로그 확인 (에러 디버깅용)
docker-compose logs -f

# 특정 컨테이너(예: api)의 로그만 확인하고 싶을 때
docker-compose logs -f api

# 전체 시스템 종료 및 컨테이너 삭제
docker-compose down
```

> **🔥 주의사항:** API 서버(`wms-api`)와 워커(`wms-worker`)는 **DB와 Redis가 완전히 가동(Healthy)된 이후에 자동으로 부팅**되도록 설정되어 있습니다. 처음 켰을 때 API가 바로 뜨지 않는다고 당황하지 마시고 로그를 지켜보세요.

---

## 2. 🧱 각 파트별 개발 및 테스트 규칙

### 🌐 프론트엔드 파트
- 로컬 웹 접속 주소: `http://localhost:3000`
- 백엔드 API 서버는 `http://localhost:8000`에 떠있습니다. CORS 및 Proxy 설정은 이미 되어 있으니, API 요청 시 `NEXT_PUBLIC_API_URL` 환경변수를 활용하세요.

### ⚙️ 백엔드 파트 (API & DB)
- 로컬 API Docs (Swagger): `http://localhost:8000/docs`
- DB 접속: `localhost:5432` (User: `admin`, PW: `password`, DB: `wms_db`)
- **Celery 비동기 테스트:** `/upload` API를 통해 반품 작업을 요청하면 즉시 응답(202)이 오고, 백그라운드의 Redis 큐를 통해 `wms-worker`가 작업을 처리합니다.

### 🧠 AI 워커 파트 (LangGraph)
- AI 검수 로직 수정 및 테스트는 백엔드 폴더 내부의 `app/ai/` 하위에서 진행합니다.
- 코드를 변경한 뒤에는 워커가 변경사항을 물고 다시 뜰 수 있도록 `docker-compose restart worker`를 실행해 주세요.

---

## 3. 🚨 Git 협업 및 배포 (CI/CD) 규칙

저희 프로젝트는 단기 속성(6+1주)으로 진행되므로 배포 파이프라인(GitHub Actions -> AWS EKS)이 이미 자동화되어 있습니다. **따라서 다음의 Git 룰을 엄격하게 지켜주세요.**

1. **절대 `main` 브랜치에 직접 Push하지 마세요.**
   - 모든 작업은 Kanban 티켓 번호 기반의 브랜치 명명 규칙을 따릅니다.
   - 기능 개발 시 `feat/<이슈번호>-<작업내용>` (예: `feat/BE-1.2-s3-upload`) 브랜치를 생성하여 작업하세요.
   - 버그 수정은 `fix/...`, UI 작업은 `design/...` 접두사를 사용합니다.
2. **배포는 오직 PR(Pull Request) 병합 시에만 일어납니다.**
   - 기능이 완성되면 `main` 브랜치를 향해 PR을 올리고, **Tech PM(장문경 님)의 코드 리뷰와 승인(Merge)** 을 받아야만 클라우드(AWS EKS) 서버에 배포됩니다.
3. **오류가 나면 즉시 공유하세요.**
   - Github Actions가 실패하거나 Docker 빌드 에러가 나면 지체 없이 슬랙/단톡방에 로그 캡처본을 올려주세요.

행운을 빕니다! 🍀


