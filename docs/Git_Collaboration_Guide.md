# 🌿 B2B WMS AI Platform - 실무 Git 협업 가이드 (GitHub Flow)

본 프로젝트는 실무(Enterprise) 수준의 엄격한 Git 형상 관리를 따릅니다.
`main` 브랜치에 직접 Push하는 것은 시스템적으로 **완벽히 차단(Branch Protection)**되어 있으므로, 모든 팀원은 반드시 아래의 '브랜치 생성 -> PR -> 리뷰' 절차를 거쳐야 합니다.

---

## 1. 🔀 브랜치(Branch) 생성 및 명명 규칙

작업을 시작하기 전, 항상 최신 `main` 코드를 내려받은 후 **기능별로 독립된 브랜치**를 생성합니다.

### 브랜치 생성 명령어
> 💡 최신 Git 버전에서는 `checkout` 대신 `switch` (이동) 및 `switch -c` (생성 후 이동) 명령어를 사용하셔도 무방합니다.

```bash
# 1. 항상 최신 main 브랜치로 이동 후 당겨오기
git checkout main  # 또는 git switch main
git pull origin main

# 2. 새로운 작업용 브랜치 생성 및 이동
git checkout -b <브랜치타입>/<이슈번호>-<작업내용>  # 또는 git switch -c <브랜치타입>/...

# (작성 예시) 백엔드 S3 API 개발 티켓(BE-1.2)을 작업할 경우:
# git checkout -b feat/BE-1.2-s3-upload
```

### 브랜치 명명 규칙 (Naming Convention)
- `feat/`: 새로운 기능 개발 (예: `feat/BE-1.2-s3-upload-api`)
- `fix/`: 버그 수정 (예: `fix/FE-1.1-sidebar-css`)
- `design/`: UI/퍼블리싱 전용 (예: `design/FE-login-page`)
- `refactor/`: 코드 리팩토링 (기능 변화 없음)

---

## 2. 💾 커밋(Commit) 컨벤션

코드를 저장할 때는 **어떤 작업을 했는지 한눈에 알 수 있도록** 접두사를 붙여 커밋합니다.

```bash
git add .
git commit -m "feat: S3 Pre-signed URL 발급 API 구현"
```

**[커밋 메시지 접두사 규칙]**
- `feat:` : 새로운 기능 추가
- `fix:` : 버그 수정
- `docs:` : 문서 (README, 가이드 등) 수정
- `style:` : 코드 포맷팅, 세미콜론 누락 등 (비즈니스 로직 변경 없음)
- `refactor:` : 코드 리팩토링
- `chore:` : 빌드 업무 수정, 패키지(npm, uv) 매니저 설정 변경

---

## 3. 🚀 Pull Request (PR) 및 병합 전 최신화 (중요!)

내가 작업하는 동안 다른 팀원이 먼저 `main`에 코드를 병합했을 수 있습니다. 따라서 **PR을 올리기 전에는 반드시 최신 `main`을 내 브랜치에 병합(Merge)**하여 충돌(Conflict)이 없는지 확인해야 합니다.

### 1) 깃허브로 Push 하기 전 충돌 방지 및 최신화
```bash
# 1. main 브랜치로 이동하여 원격 저장소의 최신 코드 받기
git checkout main
git pull origin main

# 2. 다시 내가 작업하던 브랜치로 돌아오기
git checkout <내_작업_브랜치_이름>

# 3. 최신 main 브랜치의 내용을 내 브랜치에 병합하기 (충돌이 발생하면 이 단계에서 해결)
git merge main

# 4. 내 로컬 브랜치를 깃허브로 안전하게 업로드
git push origin <내_작업_브랜치_이름>
```

### 2) GitHub에서 PR 생성
- 깃허브 레포지토리에 접속하면 나타나는 초록색 **`Compare & pull request`** 버튼을 클릭합니다.
- PR 제목은 커밋 메시지와 동일하게 `feat: S3 업로드 기능 구현` 처럼 작성합니다.
- 작업 내용(어떤 기능을 만들었는지, 확인할 부분은 무엇인지)을 본문에 상세히 적습니다.
- **Reviewers**에 PM(장문경 님) 및 다른 프론트/백엔드 팀원을 지정합니다.

### 3) CI/CD 테스트 및 Merge (병합)
- PR을 올리면 깃허브 봇(GitHub Actions)이 자동으로 린트(Lint)와 테스트 에러를 검사합니다.
- ❌ 에러가 나면 `main`으로 합칠 수 없습니다. 코드를 수정해서 다시 Push 하세요.
- ✅ 테스트를 통과하고, **Git Gatekeeper(PM 장문경 님)의 리뷰 및 승인(Approve)**이 떨어지면 최종적으로 `main` 브랜치에 코드가 병합(Merge)됩니다!
