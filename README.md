# sisicallcall
Korea IT 아카데미 Final_Project 저장소입니다.

## 로컬에서 “실사용 모드”로 실행하기 (Docker Compose)

이 프로젝트는 실사용 흐름에서 아래 저장소들을 사용합니다.

- **PostgreSQL**: 테넌트/콜/전사/요약/로그 등 영속 데이터
- **Redis**: 세션/TTL 상태(인증·OCR·비전 등)
- **ChromaDB**: RAG 벡터 저장소

### 사전 준비

- Docker Desktop 설치 및 실행
- Python (프로젝트 `requirements.txt` 설치 가능해야 함)

### Windows PowerShell (추천: Make 없이)

프로젝트 루트에서 아래를 그대로 실행하세요.

```powershell
cd D:\OF_CALLs\sisicallcall

# 1) 환경 변수 파일 준비 (최초 1회)
copy .env.example .env

# 2) DB/Redis/Chroma 기동 + healthcheck + seed
.\scripts\docker_stack_up.ps1 -Seed

# 3) 서버 실행 (실사용 흐름에 가까운 startup)
$env:STARTUP_PROFILE="full"
python scripts\run_dev.py
```

### 동작 확인(눈으로 보기)

- **헬스 체크**: 브라우저에서 `http://127.0.0.1:8000/health`

### 한 줄 요약

`.\scripts\docker_stack_up.ps1 -Seed`로 **Postgres/Redis/Chroma 올린 뒤** `python scripts\run_dev.py`로 **FastAPI 서버를 실행**하면 됩니다.
