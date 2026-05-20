# 시시콜콜 (Sisicallcall)

> 전화를 받고, 이해하고, 요약하고, 분석하고, 후속 업무까지 실행하는 **AI 음성 고객상담 운영 플랫폼**
> Team **Of-Calls** · Korea IT 아카데미 KDT Final Project

PDF 매뉴얼만 업로드하면 LangGraph 기반 AI 에이전트가 실제 전화로 고객 응대를 수행하고,
통화 종료 후엔 요약·VOC 분석·외부 시스템 액션까지 자동 실행하는 B2B SaaS 플랫폼입니다.

---

## ✨ 핵심 기능

| 단계 | 기능 |
|---|---|
| **Call Agent** (실시간) | STT → 의도 분류 → RAG/Task 처리 → TTS, 1.5~2초 응답 목표 |
| **Post-Call Agent** (비동기) | 통화 요약, VOC 분석, Reviewer 검토 후 외부 액션 실행 |
| **본인 인증** | SMS + 얼굴인식 + OCR(처방전·신분증) 결합 |
| **비전** | 상품 이미지 인식으로 불량/모델 판별 |
| **멀티테넌시** | Twilio 번호 → `tenant_id` 매핑, 모든 테이블 `tenant_id` 격리 |

---

## 🏗️ 아키텍처

### Call Agent — LangGraph 그래프

```
사용자 발화 (STT)
      ↓
  query_refine         ← 질의 재작성 / 명확성 판단 / 종료 의도 감지
      ↓
  ┌───┴───┬──────────┐
goodbye  clarify   intent_router
  ↓        ↓           ↓
 END      END    ┌──┬──┬──┬──┬──┬──┐
               faq task auth vision ocr escalation repeat
                  ↓ (각 branch 처리)
                 END
```

- **Intent Router**: GPT-4o-mini (분류만, 저비용)
- **Dialogue Manager / Branch**: GPT-4o (응답 생성)
- **Function Calling**: `task_branch` 에서 OpenAI Function Calling 으로 API 호출

### Post-Call Agent — MCP Gateway

```
Reviewer 승인 actions
      ↓
 Action Executor
      ↓
 MCPGatewayConnector → MCPProtocolClient
      ↓ (stdio transport)
 자체 MCP Server (별도 process)
      ↓
 Slack / Gmail / Jira / Calendar / SMS / Notion / Company DB
```

- 8개 provider × 12 tool 을 dotted name 으로 통합
- **Idempotency 안전망**: 같은 의도의 액션은 1회만 발송, 재시도 시 `already_succeeded` / `already_attempted` 매칭
- `mcp_action_logs` 테이블 영속화 (source / via_mcp / mcp_tool 메타)

---

## 🛠️ 기술 스택

| 영역 | 사용 기술 |
|---|---|
| **음성 입출력** | Twilio PSTN, Deepgram STT (화자분리·VAD·소음억제), ElevenLabs / Google TTS WaveNet |
| **VAD / 화자 검증** | Silero VAD, TitaNet-S (ONNX, 파인튜닝 BN-only · AAM-Softmax · Telephony Aug, EER 20.65% → 12.38%) |
| **에이전트** | LangGraph, LangChain, OpenAI GPT-4o / GPT-4o-mini |
| **RAG** | ChromaDB, OpenAI Embeddings (실시간), BGE-M3 (KNN Router 임베딩 연구) |
| **백엔드** | FastAPI, WebSocket, asyncio |
| **데이터** | PostgreSQL (영속), Redis (세션·TTL), ChromaDB (벡터) |
| **프론트엔드** | Vite + React + TypeScript, React Flow |
| **부가 기능** | Tesseract OCR, Fine-tuned CNN (얼굴), Solapi SMS |
| **MCP** | Slack · Gmail · Jira · Calendar · SMS · Notion |

---

## 🎯 마일스톤

| 단계 | 범위 |
|---|---|
| **M1 (MVP)** | PDF 기반 FAQ 음성 응답, ChromaDB 실시간 RAG (300~500ms) |
| **M2** | 콜백 접수·메모·요약 알림, 관리자 대시보드, MCP 외부 액션 |
| **M3** | 외부 시스템 연동 데모, 비전/OCR 본인인증 확장 |

> 무거운 처리(임베딩·멀티모달·파인튜닝)는 **관리자 설정 시 1회만** 수행하고,
> 실시간 통화 경로는 텍스트 기반으로 비용·속도를 최적화합니다.

---

## 🚀 빠른 시작 — 로컬 "실사용 모드"

실사용 흐름에서는 아래 3개 저장소를 모두 사용합니다.

- **PostgreSQL** — 테넌트/콜/전사/요약/로그 등 영속 데이터
- **Redis** — 세션/TTL 상태 (인증·OCR·비전 등)
- **ChromaDB** — RAG 벡터 저장소

### 사전 준비

- Docker Desktop 설치 및 실행
- Python 3.11+ (`requirements.txt` 설치 가능해야 함)
- Node.js 18+ (프론트엔드 실행 시)
- `.env` 환경 변수 (OpenAI, Twilio, Deepgram, Solapi 등 API Key)

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

### macOS / Linux (Make 사용)

```bash
cp .env.example .env       # 최초 1회
make up                    # 3개 DB 컨테이너 기동 + healthcheck
make seed                  # 시드 데이터 주입 (병원·식당 테넌트)

export STARTUP_PROFILE=full
python scripts/run_dev.py
```

### 동작 확인

- **헬스 체크**: 브라우저에서 [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

### 프론트엔드 실행

```bash
cd front
npm ci
npm run dev                # http://localhost:5173
```

### 한 줄 요약

`.\scripts\docker_stack_up.ps1 -Seed` 로 **Postgres/Redis/Chroma 올린 뒤**, `$env:STARTUP_PROFILE="full"; python scripts\run_dev.py` 로 **FastAPI 서버를 실행**하면 됩니다.

### 주요 Make 명령어

| 명령 | 용도 |
|---|---|
| `make up` / `make down` | DB 컨테이너 기동 / 중지 |
| `make reset` | 볼륨까지 완전 삭제 (스키마 변경 후) |
| `make check` | 3개 DB healthcheck 일괄 검증 |
| `make psql` / `make redis-cli` / `make chroma` | 각 DB CLI 접속 |
| `make seed` | 시드 데이터 주입 |

---

## 🗄️ 데이터베이스

8개 핵심 테이블 (모든 테이블 `tenant_id` 컬럼으로 멀티테넌시 격리):

```
tenants            — 기업/테넌트 정보
calls              — 통화 메타데이터
transcripts        — STT 결과
call_summaries     — Post-Call 요약
voc_analyses       — VOC 분석 결과
face_embeddings    — 얼굴 인증 임베딩
knn_intents        — KNN Router 의도 임베딩 (BGE-M3)
rag_documents      — RAG 청크 메타
mcp_action_logs    — MCP 외부 액션 실행 로그
```

### 포트 할당

| 서비스 | 포트 |
|---|---|
| PostgreSQL | 5432 |
| Redis | 6379 |
| ChromaDB | **8001** (FastAPI 8000 충돌 회피) |
| FastAPI | 8000 |

---

## 👥 팀 — Of-Calls

| 이름 | 담당 |
|---|---|
| **이희원** | 얼굴 인증, Call Agent LangGraph 전체 구조 설계, 프론트엔드 설계, DB 설계 |
| **김대영** | 화자 검증 파인튜닝(TitaNet), Post-Call Agent 설계 및 구현, 대시보드, 배포 |
| **김주미** | 임베딩 모델 연구, PDF Chunking·임베딩 전략, 후처리 Agent 설계 |
| **김신용** | 임베딩 모델 연구, PDF Chunking·임베딩 전략, 후처리 Agent 설계 |
| **안희영** | VAD 모델 연구, LangGraph VAD 노드 설계, KNN Router 연구 |
| **김수현** | Intent Router 연구·노드 설계, 비전 모델 연구 |

---

## 📌 응답 / 라우팅 규칙 (Call Agent)

Intent Router 가 분류하는 7개 의도:

| Intent | 설명 |
|---|---|
| `faq` | 일반 정보 질의 — RAG 응답 |
| `task` | 예약·회원조회 등 업무 처리 (Function Calling) |
| `auth` | 본인 인증 요청 / 완료 통보 |
| `vision` | 사진 업로드 (제품/상품 이미지 분석) |
| `ocr` | 문서 업로드 (처방전·신분증·영수증) |
| `escalation` | 상담원 연결 요청 (명시적 키워드만) |
| `repeat` | 직전 안내 반복 요청 |

---

## 🧪 트러블슈팅

| 증상 | 해결 |
|---|---|
| `make up` 후 healthcheck 실패 | 포트 충돌 — `lsof -i :5432,6379,8001` |
| ChromaDB healthcheck timeout | `docker compose logs chromadb`, 디스크 여유 확인 |
| 스키마 변경 후 반영 안 됨 | `make reset` → `make up` → `make seed` |
| Windows `make` 명령 없음 | WSL2 사용 또는 `choco install make` |
| 프론트 API 호출 실패 | `front/.env` 의 `VITE_API_BASE_URL` 확인 |
