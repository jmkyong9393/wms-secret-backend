WMS Core Backend#
B2B AI Book Inspection & WMS Platform Backend (FastAPI + SQLModel + LangGraph)

Tech Stack#
Python 3.11+
FastAPI
SQLModel / PostgreSQL (Celery 큐 Queue)
LangGraph (Agentic Workflow & Supervisor Pattern Multi-Agent Architecture)
LangChain (LLMOps & Tracing)
Setup#
# 1. 패키지 동기화 및 가상환경 세팅
uv sync

# 2. FastAPI 서버 실행
uvicorn app.main:app --reload

# 3. (터미널 탭 추가 후) AI 워커 백그라운드 데몬 실행
python app/worker.py