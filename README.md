# Cadence — AI-Powered Speech Intelligence Platform

> **"Speak. Record. Walk away with intelligence."**

Cadence started as an AI Meeting Intelligence platform and has grown into a **Speech Intelligence Suite** — a self-hosted, microservice-based system that captures any human speech context (live meetings, voice memos, candidate interviews, business calls), transcribes it in real-time, and compiles structured, mode-aware intelligence reports with action items, decisions, and deep speaker analytics.

This repository contains the complete backend microservices architecture powering the [Cadence frontend](https://github.com/Priyankm23/cadence-frontend).

---

## 🚀 What Cadence Does Today

| Capability | Details |
|---|---|
| **Live Meetings** | Browser-based WebRTC rooms (via LiveKit) with real-time transcription, participant management, and role-based access |
| **Voice Memos** | Record personal audio memos (no room required) — transcribed and processed into clean AI-structured documents |
| **AI Meeting Modes** | `General`, `Business`, `Interview` — each triggers a specialized LLM prompt for domain-specific intelligence |
| **Real-Time Transcription** | Deepgram Nova-2 (primary) with Groq Whisper fallback, filtered by Silero VAD for noise/silence rejection |
| **Post-Meeting Analysis** | LLaMA-3.3-70B via Groq produces structured JSON: summaries, action items, decisions, sentiment, speaker analytics |
| **Personal Coaching** | Per-participant transcript analysis: talk time, communication rating, coaching tips, confidence score |
| **Integrity Monitoring** | Tab-switch detection for interview mode — live alerts to host, persisted as violation markers |
| **Email Reports** | OAuth2 Gmail API sends formatted HTML reports to all participants post-meeting |
| **Meeting History** | Dashboard showing all past meetings and memos with their AI reports and status |

---

## 🏗️ System Architecture

The system uses a modern microservice pattern designed to be lightweight, GPU-free, and deployable on free-tier infrastructure.

```
                  ┌────────────────────────────────────────────────────────┐
                  │                 Nginx / Caddy Proxy                    │
                  │              (Secure SSL & Port Routing)               │
                  └───────────┬────────────────────────────┬───────────────┘
                              │                            │
                     /auth or /meetings                 /socket.io
                     /memos                             (WebSocket)
                              │                            │
                  ┌───────────▼────────────────────────────┼────────────────
                  │              API Gateway (Port 8000)   │
                  │        (FastAPI + Global Auth Guard)   │
                  └───────────┬────────────────────────────┼────────────────
                              │                            │
                    Internal HTTP Proxies                  │
            ┌─────────────────┴─────────────────┐          │
            │                                   │          │
   ┌────────▼──────────────┐           ┌────────▼──────────▼────┐
   │ Auth Service          │           │ Meeting Service        │
   │ (Port 8001)           │           │ (Port 8002 - ASGI)     │
   │ Registration, Login & │           │ Meetings, Memos,       │
   │ JWT Refresh Cookies   │           │ Signaling & Worker Host│
   └────────┬──────────────┘           └────────┬──────────▲────┘
            │                                   │          │
            │  Shared Neon PostgreSQL Cloud DB  │          │ Redis Pub/Sub
            └─────────────────┬─────────────────┘          │ (Live Transcripts)
                              │                            │
   ┌──────────────────────────▼────────────────────────────┴────────────────
   │                        Redis Message Broker & Pub/Sub
   │     Queues: `audio_queue`, `meeting_ended_queue`, `personal_analysis_queue`
   └──────────────────────────┬────────────────────────────┬────────────────
                              │                            │
                   Pops raw audio chunks           Pops completed meetings
                              │                            │
                  ┌───────────▼───────────┐    ┌───────────▼───────────┐
                  │ Transcript Worker     │    │ AI Analysis Worker    │
                  │ (Silero VAD +         │    │ (Groq LLaMA-3.3 70B   │
                  │  Deepgram Nova-2 /    │    │  Mode-Aware Reports)  │
                  │  Groq Whisper)        │    └───────────┬───────────┘
                  └───────────────────────┘               │
                                                 Dispatches email task
                                                          │
                                               ┌───────────▼───────────┐
                                               │ Notification Worker   │
                                               │ (Celery Daemon +      │
                                               │  Gmail OAuth2 API)    │
                                               └───────────────────────┘
```

---

## 🛠️ Microservice Breakdown

### 1. API Gateway (`services/api-gateway`)
The single entry point for all HTTP traffic.
- **Technology:** FastAPI, HTTPX Async Client, Starlette Custom Middleware
- **Auth Guard:** `AuthMiddleware` intercepts and verifies JWT access tokens; injects `X-User-ID` into downstream proxies
- **Request Tracing:** Assigns a unique `X-Request-ID` UUID to every request for end-to-end log correlation
- **Dynamic CORS:** Supports multiple origins + regex matching for Vercel preview deployments
- **Memo Routing:** Proxies all `/memos/*` requests to the meeting-service's dedicated memo router

### 2. Auth Service (`services/auth-service`)
Handles credentials, session management, and user identity.
- **Technology:** FastAPI, Passlib (Bcrypt), Python-Jose, SQLAlchemy, Alembic
- **JWT Pattern:** Short-lived access tokens in JSON payload + HttpOnly `refresh_token` cookie with automatic rotation
- **Batch Resolution:** `/auth/users/batch` resolves lists of UUIDs into names/profiles for participant lookup

### 3. Meeting Service (`services/meeting-service`)
The coordinator for both live meetings and voice memos — the platform's core service.
- **Technology:** FastAPI, Python-Socketio (ASGI), SQLAlchemy, Redis-py, LiveKit SDK
- **Subprocess Lifespan Orchestrator:** On server start, the FastAPI lifespan spawns all three background worker scripts as managed subprocesses — no separate process management needed locally or on a VPS
- **Meeting Modes:** `general` | `business` | `interview` | `memo` — stored per meeting, used to select the AI analysis prompt
- **Memo Template Support:** Voice memos carry an optional `memo_template` field (`clean`, `structured`, `cornell`) to customize how the LLM formats the output document

**Socket.io Events:**
| Direction | Event | Purpose |
|---|---|---|
| Client → Server | `join_room` / `leave_room` | Participant lifecycle |
| Client → Server | `audio_chunk` | Streaming raw PCM audio (Base64-encoded, 16kHz mono) |
| Client → Server | `tab_switch_alert` | Interviewee integrity violation signal |
| Client → Server | `send_message` | In-room chat routing |
| Client → Server | `end_meeting_for_all` | Host force-closes the room |
| Server → Client | `transcript_update` | Live transcription pushed to room |
| Server → Client | `user_joined` / `user_left` | Participant presence updates |
| Server → Client | `room_ended` | Room closure signal to all participants |

---

## ⚙️ Asynchronous Worker Subsystems

### 1. Transcript Worker (`transcript_worker.py`)
Processes raw audio chunks from the Redis `audio_queue` in a BLPOP loop.

**Processing Pipeline:**
1. **Buffer Accumulation:** Chunks from the same `meeting_id:user_id` are accumulated into 5–15s windows or flushed on >1.2s silence gaps
2. **VAD Filtering:** Converts Int16 PCM → Float32 → PyTorch tensor → runs **Silero VAD**. Chunks with no detected speech are silently dropped
3. **Transcription (Primary — Deepgram Nova-2):** Speech-confirmed buffers are wrapped in a WAV header and sent to `api.deepgram.com/v1/listen?model=nova-2&smart_format=true`. Smart formatting auto-handles punctuation and number formatting
4. **Transcription (Fallback — Groq Whisper):** If `DEEPGRAM_API_KEY` is absent or Deepgram returns an error, the worker falls back to `whisper-large-v3` on Groq Cloud
5. **Hallucination Filtration:** Post-processes output using a lowercase denylist (e.g., *"thank you for watching"*, *"please subscribe"*) and discards results under 4 characters
6. **Real-Time Sync:** Saves the transcript segment to the DB via internal HTTP endpoint, then publishes to the `transcript_updates` Redis pub/sub channel, which the meeting-service re-broadcasts via Socket.io to all room clients

> **Why Deepgram Nova-2 over Whisper?**
> Deepgram Nova-2 offers significantly lower latency (~300ms vs 1–2s for Groq Whisper), higher accuracy on conversational speech, and native smart formatting. The fallback to Whisper ensures zero-downtime resilience if API keys are unavailable.

### 2. AI Analysis Worker (`ai_worker.py`)
Triggered by two queues:
- `meeting_ended_queue` — automatic post-meeting analysis
- `personal_analysis_queue` — on-demand personal coaching reports

**Mode-Aware Analysis:**

| Mode | What the LLM Extracts |
|---|---|
| **General** | Executive summary, sentiment, key topics, action items, decisions |
| **Business** | All of the above + client pain points, product requirements, objections, budget signals, competitor mentions |
| **Interview** | Communication rating (1–10), technical skill proficiencies, candidate red flags, tab-switch violations, hiring recommendation (`Strong Hire` / `Hire` / `No Hire`) |
| **Memo** | Cleans and structures raw voice memo transcripts into a formatted Markdown document matching the selected template (`clean`, `structured`, `cornell`) |

**Action Item Resolution:** Parses `"Task - Owner"` formatted action items and resolves owner names against participant UUIDs to create directly assignable DB records.

**Notification Dispatch:** Queues a Celery task to the notification worker upon report completion.

### 3. Notification Worker (`notification_worker.py`)
A Celery-based daemon for transactional email delivery.
- **Gmail OAuth2:** Uses Google Gmail API with refreshed credentials for secure email sending — no SMTP passwords
- **Jinja2 Templates:** Renders professional HTML emails for meeting summaries (`meeting_summary.html`), scheduled meeting invites (`scheduled_meeting_invite.html`), and meeting-started notifications

---

## 📋 Voice Memo Feature

Voice Memos are a distinct product surface within Cadence — a personal, meeting-free recording mode.

**How It Works:**
1. User initiates a memo session via `POST /memos/` (creates a `Meeting` record with `mode="memo"`)
2. The frontend streams audio chunks via Socket.io — same transcription pipeline as live meetings
3. On memo end, `POST /memos/{memo_id}/end` queues the session to `meeting_ended_queue`
4. The AI worker detects `mode="memo"` and runs `generate_memo_report()` — uses `memo_prompts` to produce a clean, formatted Markdown document instead of a meeting analysis
5. The structured document is saved to `meeting_analysis` with `mode="memo"` and served back to the Memo page

**Supported Templates:**
- **Clean** — Prose-style narrative document with bullet highlights
- **Structured** — Sections: Key Points, Decisions, Follow-ups, Notes
- **Cornell** — Cornell note-taking format: Cues | Notes | Summary

**API Endpoints:**

```
POST   /memos/               - Create a new voice memo session
GET    /memos/               - List all memos for the current user
GET    /memos/{memo_id}      - Get memo metadata and analysis
POST   /memos/{memo_id}/end  - End memo recording & trigger AI formatting
DELETE /memos/{memo_id}      - Delete a memo
```

> **Note:** Memos share the `meetings` database table (with `mode="memo"`) for architectural simplicity. On the frontend, memos are surfaced exclusively on the dedicated **Memo page** and are filtered out of the **Meeting History** view.

---

## 🗄️ Database Architecture

PostgreSQL (Neon Serverless or local). Migrations per-service via Alembic.

```
  ┌──────────────────────┐
  │        users         │
  ├──────────────────────┤
  │ id (UUID, PK)        ◄────────────────────────┐
  │ email (Unique)       │                        │
  │ name                 │                        │
  │ hashed_password      │                        │
  │ created_at           │                        │
  └──────────────────────┘                        │
             ▲                                    │
             │                                    │
  ┌──────────┴───────────┐                        │
  │  scheduled_meetings  │                        │
  ├──────────────────────┤                        │
  │ id, join_code        │                        │
  │ creator_id (FK)      │                        │
  │ title, mode          │                        │
  │ scheduled_date/time  │                        │
  │ objectives           │                        │
  │ participants (JSON)  │                        │
  └──────────────────────┘                        │
                                                  │
  ┌──────────────────────────────────────┐        │
  │             meetings                 │        │
  ├──────────────────────────────────────┤        │
  │ id (UUID, PK)                        ◄───┐    │
  │ join_code                            │   │    │
  │ title                                │   │    │
  │ creator_id (UUID)                    │   │    │
  │ status (waiting/active/ended)        │   │    │
  │ mode (general/business/interview/    │   │    │
  │       memo)                          │   │    │
  │ memo_template (clean/structured/     │   │    │
  │               cornell)              │   │    │
  │ created_at, started_at, ended_at    │   │    │
  │ duration_seconds                    │   │    │
  └──────────────────────────────────────┘   │    │
             ▲                               │    │
             │                               │    │
   ┌─────────┼──────────┬────────────────────┴────┴────────┐
   │         │          │                │                  │
┌──┴──┐   ┌──┴──┐   ┌───┴───┐        ┌───┴───┐         ┌───┴───┐
│  A  │   │  B  │   │   C   │        │   D   │         │   E   │
└─────┘   └─────┘   └───────┘        └───────┘         └───────┘
```

| Table | Description |
|---|---|
| **A: `meeting_participants`** | Tracks user roles (`host`/`attendee`), join/leave timestamps, cumulative speaking time |
| **B: `transcript_segments`** | Chronological speech segments with `speaker_id`, start/end audio offsets, and text |
| **C: `meeting_analysis`** | Full AI output: executive summary, sentiment, `insights` JSON (mode-specific), `mode` field |
| **D: `action_items`** | Tasks with descriptions, completion state, and resolved `assignee_id` FK back to `users` |
| **E: `decisions`** | Key agreements and conclusions from the meeting |
| **F: `meeting_alerts`** | Integrity markers (`tab_switch`) linked to participants with timestamps |
| **G: `user_transcript_analysis`** | Personal coaching: talk time %, communication rating, coaching recommendations, confidence score |

---

## 🔄 Core Data Flows

### Real-Time Transcription Pipeline
```
[Client Mic — AudioWorklet, 16kHz PCM, Float32→Int16]
     │
     ▼ (Socket.io "audio_chunk" — Base64 encoded binary)
[Meeting Service] → RPUSH → [Redis "audio_queue"]
     │
     ▼ (BLPOP)
[Transcript Worker]
     ├──► Buffer accumulation (5–15s per speaker)
     ├──► Silero VAD (PyTorch) → Drops silence/noise
     ├──► WAV Header wrapping
     ├──► Deepgram Nova-2 API (primary) or Groq Whisper (fallback)
     ├──► Hallucination filter → DB save (internal HTTP)
     └──► PUBLISH → [Redis "transcript_updates" pub/sub]
                          │
                          ▼
[Meeting Service Redis Listener] → sio.emit("transcript_update") → [All Room Clients]
```

### Meeting/Memo End → AI Analysis Pipeline
```
[Host ends meeting / Memo recording stops]
     │
     ▼ (POST /meetings/{id}/end or /memos/{id}/end)
[Meeting Service REST Router]
     ├──► Marks status = "ended", sets duration_seconds
     └──► RPUSH → [Redis "meeting_ended_queue"]
                          │
                          ▼ (BLPOP)
[AI Analysis Worker]
     ├──► Reads meeting.mode from DB
     ├──► Fetches transcript_segments + tab_switch alerts
     ├──► Selects prompt: meeting_prompts / memo_prompts
     ├──► Calls Groq LLaMA-3.3-70B → structured JSON response
     ├──► Resolves action item assignees → UUIDs
     ├──► Saves: meeting_analysis, action_items, decisions
     └──► Celery dispatch → "send_meeting_summary_email"
                          │
                          ▼
[Notification Worker]
     ├──► Renders Jinja2 HTML email template
     └──► Sends via Gmail OAuth2 API
```

---

## ⚙️ Environment Variables

Create `.env` files in each service directory (`/services/api-gateway/.env`, `/services/auth-service/.env`, `/services/meeting-service/.env`):

```env
# --- General ---
PORT=8002
ALLOWED_ORIGINS=["http://localhost:3000","https://cadence-meeting-intelligence.vercel.app"]

# --- Security (shared across gateway, auth, meeting) ---
SECRET_KEY=your-jwt-signing-secret-key-minimum-32-chars
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7

# --- Databases ---
DATABASE_URL=postgresql://user:pass@ep-host.neon.tech/dbname?sslmode=require
REDIS_URL=redis://localhost:6379/0

# --- Service Routing (API Gateway) ---
AUTH_SERVICE_URL=http://localhost:8001
MEETING_SERVICE_URL=http://localhost:8002

# --- Transcription (Primary + Fallback) ---
DEEPGRAM_API_KEY=your-deepgram-api-key        # Primary: Nova-2 real-time transcription
GROQ_API=gsk_your_groq_api_key                # Fallback transcription + LLM analysis

# --- Email Notifications (Gmail OAuth2) ---
GMAIL_CLIENT_ID=your-google-oauth2-client-id
GMAIL_CLIENT_SECRET=your-google-oauth2-client-secret
GMAIL_REFRESH_TOKEN=your-google-oauth2-refresh-token
FROM_EMAIL=your-gmail@gmail.com

# --- LiveKit WebRTC ---
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your-livekit-api-key
LIVEKIT_API_SECRET=your-livekit-api-secret
```

---

## 🗺️ Upcoming Features (Roadmap)

The platform is actively expanding from a meeting-centric tool to a full **Speech Intelligence Layer** applicable to any spoken context.

### Near-Term

| Feature | Description |
|---|---|
| **Screen Sharing** | LiveKit's `setScreenShareEnabled()` — already architected, needs frontend toggle |
| **Multilingual Transcription** | Deepgram and Whisper both support non-English — Hindi, Spanish, French support planned |
| **Action Item Workspace** | Per-action-item comment threads + status tracking. Export to Jira / Linear / Notion |
| **Scheduled Meeting Invites** | Calendar-based meeting creation with email invites sent to participants |

### Medium-Term

| Feature | Description |
|---|---|
| **Speaker Diarization** | Replace socket-based speaker tracking with `pyannote.audio` for ML-based "who said what" voice fingerprinting |
| **Semantic Meeting Search** | Store transcript embeddings in `pgvector`. Query: *"What did we decide about the API schema last sprint?"* — full RAG over your own meeting history |
| **Real-Time AI Coaching** | Mid-meeting LLM sidebar whispers: *"You've been talking for 8 minutes — consider pausing for input"* or *"This topic has been unresolved for 20 minutes"* |
| **Meeting Coach (Personal AI)** | Cross-meeting personal pattern analysis: talk time trends, interruption count, monologue length, question frequency |
| **Auto Follow-up Email Draft** | One-click LLM drafts a professional follow-up email with decisions and action items pre-filled for all participants |

### Long-Term (V2 Scope)

| Feature | Description |
|---|---|
| **External Platform Integration** | Webhook-based Zoom/Google Meet recording ingestion → same Deepgram + LLM pipeline |
| **Slack / Notion Export** | Post-meeting report pushed to Slack channel or Notion page automatically |
| **Stripe Billing** | Free tier: 5 meetings/month. Pro: unlimited meetings, longer retention, priority transcription |
| **Chrome Extension** | Capture audio from any browser tab (Google Meet, Zoom web, Teams) without a room join |
| **Mobile App** | React Native app for quick voice memos and meeting joins on the go |

---

## 🧠 Intelligence Stack Summary

| Layer | Technology | Role |
|---|---|---|
| **Speech → Text (Primary)** | Deepgram Nova-2 | Real-time conversational transcription with smart formatting |
| **Speech → Text (Fallback)** | Groq Whisper Large-v3 | Cloud-hosted Whisper for resilience |
| **Voice Activity Detection** | Silero VAD (PyTorch) | Local neural noise/silence rejection before any API call |
| **Language Model** | LLaMA-3.3-70B via Groq | Mode-aware meeting analysis and memo formatting |
| **Real-Time Transport** | LiveKit (WebRTC SFU) | Browser-based audio/video rooms |
| **Message Broker** | Redis (Lists + Pub/Sub) | Audio queue + live transcript broadcast |
| **Task Queue** | Celery | Async email notifications |
| **Database** | PostgreSQL (Neon) | All persistent state |
| **Email Delivery** | Gmail OAuth2 API | Transactional HTML emails |

---

## 🔧 Local Development

```bash
# 1. Start infrastructure
docker-compose up redis

# 2. Start Auth Service
cd services/auth-service && uvicorn main:app --port 8001 --reload

# 3. Start Meeting Service (auto-spawns transcript + AI + notification workers)
cd services/meeting-service && uvicorn main:app --port 8002 --reload

# 4. Start API Gateway
cd services/api-gateway && uvicorn main:app --port 8000 --reload

# 5. Run database migrations (first time)
cd services/meeting-service && alembic upgrade head
cd services/auth-service && alembic upgrade head
```

> **Note:** The Meeting Service lifespan manager automatically spawns `transcript_worker.py`, `ai_worker.py`, and the Celery notification worker as subprocesses on startup. No separate terminals needed for workers in local dev.

---

## 📁 Project Structure

```
cadence-backend/
├── docker-compose.yml
├── README.md
│
└── services/
    ├── api-gateway/
    │   ├── main.py                  ← FastAPI + AuthMiddleware + CORS
    │   ├── middleware/auth.py        ← JWT verification + X-User-ID injection
    │   └── routers/proxy.py         ← HTTP proxy routes (auth, meetings, memos)
    │
    ├── auth-service/
    │   ├── main.py
    │   ├── models.py                ← User, RefreshToken
    │   └── routes/auth.py           ← register, login, refresh, logout, /me
    │
    └── meeting-service/
        ├── main.py                  ← FastAPI + Socket.io ASGI mount + lifespan worker spawner
        ├── models.py                ← Meeting (mode, memo_template), Participant, Transcript, Analysis...
        ├── schemas.py               ← Pydantic models including MemoCreate/MemoOut
        ├── socket_handlers.py       ← All Socket.io event handlers
        ├── transcript_worker.py     ← Deepgram Nova-2 / Whisper + Silero VAD pipeline
        ├── ai_worker.py             ← LLaMA-3.3-70B mode-aware analysis + memo formatting
        ├── notification_worker.py   ← Celery + Gmail OAuth2 email delivery
        ├── routes/
        │   ├── meeting.py           ← Meeting CRUD + end/leave/analysis endpoints
        │   └── memos.py             ← Voice memo create/list/get/end/delete endpoints
        ├── prompts/
        │   ├── meeting_prompts.py   ← General, Business, Interview prompt templates
        │   ├── memo_prompts.py      ← Clean, Structured, Cornell document templates
        │   └── personal_prompts.py  ← Per-participant coaching prompt
        └── alembic/                 ← DB migration versions
```

---

## 💡 What This Demonstrates (Engineering Perspective)

| Skill | Where It Appears |
|---|---|
| **FastAPI microservices** | All 3 services with proper lifespan, middleware, and dependency injection |
| **WebSockets / Socket.io** | Real-time room signaling, audio streaming, transcript broadcast |
| **WebRTC + LiveKit SFU** | Browser-native audio/video with SFU-managed rooms |
| **Real-time audio processing** | AudioWorklet → PCM → VAD → cloud STT pipeline |
| **Multi-provider STT** | Deepgram Nova-2 primary + Groq Whisper fallback with graceful degradation |
| **Async task queues** | Redis-backed BLPOP workers + Celery notification daemon |
| **LLM integration & prompt engineering** | Mode-specific structured JSON extraction, memo document formatting |
| **PostgreSQL + SQLAlchemy** | Relational schema across 7+ tables with Alembic migrations |
| **Redis Pub/Sub** | Real-time transcript broadcast across service boundaries |
| **JWT authentication** | HttpOnly refresh cookies, short-lived access tokens, automatic rotation |
| **Gmail OAuth2 API** | Transactional email without SMTP credentials |
| **Docker + multi-service orchestration** | Docker Compose for full local stack |
| **Subprocess lifecycle management** | Meeting service spawns and manages worker processes via Python `subprocess` |
