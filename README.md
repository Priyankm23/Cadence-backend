# Cadence-AI: Intelligent Meeting Platform Backend

Cadence-AI is a self-hosted, microservice-based AI Meeting Intelligence Platform designed to capture real-time audio/video meetings in the browser, generate live speaker-attributed transcriptions, detect user integrity signals (tab switches/alerts), and compile structured, mode-aware post-meeting summaries with action items, decisions, and speaker analytics.

This repository hosts the complete backend microservices architecture.

---

## 🏗️ System Architecture & Topography

The system utilizes a modern, resilient microservice pattern designed to keep container hosting lightweight, performant, and completely free of expensive GPU dependencies.

```
                  ┌────────────────────────────────────────────────────────┐
                  │                 Nginx / Caddy Proxy                    │
                  │              (Secure SSL & Port Routing)               │
                  └───────────┬────────────────────────────┬───────────────┘
                              │                            │
                     /auth or /meetings                 /socket.io
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
   │ Registration, Login & │           │ Signaling, Rooms, Meta │
   │ JWT Refresh Cookies   │           │ & Subprocess Worker Host│
   └────────┬──────────────┘           └────────┬──────────▲────┘
            │                                   │          │
            │  Shared Neon PostgreSQL Cloud DB  │          │ Redis Pub/Sub Live
            └─────────────────┬─────────────────┘          │ Transcripts Broadcast
                              │                            │
   ┌──────────────────────────▼────────────────────────────┴────────────────
   │                        Redis Message Broker & Pub/Sub
   │           Queues: `audio_queue`, `meeting_ended_queue`, `personal_analysis_queue`
   └──────────────────────────┬────────────────────────────┬────────────────
                              │                            │
                   Pops raw audio chunks           Pops completed meetings
                              │                            │
                  ┌───────────▼───────────┐    ┌───────────▼───────────┐
                  │ Transcript Worker     │    │ AI Analysis Worker    │
                  │ (Local Silero VAD +   │    │ (Groq LLaMA-3.3 70B   │
                  │ Groq Whisper API)     │    │ Analysis & Reports)   │
                  └───────────────────────┘    └───────────┬───────────┘
                                                           │
                                                  Dispatches email task
                                                           │
                                               ┌───────────▼───────────┐
                                               │ Notification Worker   │
                                               │ (Celery Daemon +      │
                                               │ Brevo SMTP API Email) │
                                               └───────────────────────┘
```

---

## 🛠️ Microservice Breakdown

The backend is composed of **three primary executable web services** and **three asynchronous background workers** managed through a unified process coordinator.

### 1. API Gateway (`services/api-gateway`)
The single entry point for all HTTP traffic entering the microservices mesh.
* **Technology:** FastAPI, HTTPX Async Client, Starlette Custom Middleware.
* **Authentication Guard:** Runs [AuthMiddleware](services/api-gateway/middleware/auth.py) which intercepts requests, verifies JWT access tokens against a shared secret, and rejects unauthenticated requests for private paths.
* **Header Injection:** Extracts the authenticated user's ID and injects it downstream in the proxy request headers as `X-User-ID`.
* **Telemetry & Tracking:** Assigns a unique `X-Request-ID` UUID to every incoming HTTP request for end-to-end tracing across service logs.
* **Dynamic CORS:** Supports multi-origin development environments, custom hosting domains, and matches dynamic Vercel previews using regex: `https://.*\.vercel\.app`.

### 2. Auth Service (`services/auth-service`)
Responsible for credentials, session lifecycles, and user registration.
* **Technology:** FastAPI, Passlib (Bcrypt), Python-Jose, SQLAlchemy, Alembic.
* **Security Patterns:** 
  * Registration checks for existing emails and hashes passwords.
  * Login issues short-lived JWT Access Tokens returned in the JSON payload, and sets a cryptographically secure, HttpOnly `refresh_token` cookie.
  * Refresh token rotation automatically invalidates old tokens and rotates the cookie session.
  * Batch querying endpoint (`/auth/users/batch`) resolves lists of UUIDs into user names and profiles for downstream meeting participant lookups.

### 3. Meeting Service (`services/meeting-service`)
The coordinator of meetings, metadata, scheduling, and live signaling.
* **Technology:** FastAPI, Python-Socketio (ASGI), SQLAlchemy, Redis-py.
* **Subprocess Lifespan Orchestrator:** The application [lifespan](services/meeting-service/main.py#L77-L117) acts as a daemon manager. When the FastAPI server starts, it spawns the background worker scripts (`transcript_worker.py`, `ai_worker.py`, and the Celery `notification_worker` process) as managed subprocesses. This removes the need to deploy and manage 6 distinct processes separately in local/VPS environments.
* **Signaling & Socket.io Events:**
  * `join_room` & `leave_room`: Manages participants in real-time rooms.
  * `audio_chunk`: Accepts streaming raw PCM audio payload chunks (Base64 or binary) from client microphones and pushes them onto the Redis `audio_queue`.
  * `tab_switch_alert`: Receives browser visibility alerts when a user switches tabs, broadcasts the warning live to the room host (crucial for interview/integrity mode), and persists the violation to the DB.
  * `send_message`: Routes instant chat messages to room participants.
  * `end_meeting_for_all`: Allows the meeting creator to force close a room.
* **Redis Pub/Sub Listener:** Runs a persistent async Redis subscription task listening on the `transcript_updates` channel. When a transcript segment is processed by the background worker, this listener captures it and pushes it live to the corresponding socket room.

---

## ⚙️ Asynchronous Worker Subsystems

The processing pipelines are decoupled from the web servers via Redis task queues, preventing heavy processing workloads from blocking web loop loops.

### 1. Transcript Worker (`services/meeting-service/transcript_worker.py`)
Consumes raw audio chunks from Redis's `audio_queue` to perform real-time speech transcription.
* **Voice Activity Detection (VAD):** Normalizes incoming 16-bit integer PCM raw bytes to float32 range `[-1.0, 1.0]` and runs it locally through the state-of-the-art **Silero VAD** model using PyTorch. If no human speech is detected, the chunk is immediately dropped to prevent Groq API calls and stop Whisper from producing silent hallucinations.
* **Groq Cloud Whisper Integration:** Speech-containing chunks are packaged into WAV buffers and transcribed via Groq's high-speed cloud endpoint using `whisper-large-v3`.
* **Hallucination Filtration & Text Cleaning:** Post-processes text using a strict lowercase filter list (discarding Whisper anomalies like *"Thank you for watching"*, *"Please subscribe"*, or single-character noise) and ignores chunks shorter than 4 characters.
* **Real-time Synchronization:** Calls the local internal endpoint to save the transcript segment in the DB and publishes a notification payload to the `transcript_updates` Redis pub/sub channel.

### 2. AI Analysis Worker (`services/meeting-service/ai_worker.py`)
Triggered automatically when a meeting ends (monitoring the `meeting_ended_queue` queue) or when a participant manually requests a report (`personal_analysis_queue`).
* **Structured JSON Prompts:** Builds structured analysis requests and sends them to Groq's `llama-3.3-70b-versatile` LLM model, requesting a strictly validated JSON response.
* **Mode-Aware Summaries:** Instantly alters its extraction prompts based on the meeting's configured profile:
  * **Business Mode:** Extracts pain points, product requirements, budget metrics, and competitors.
  * **Interview Mode:** Rates communication on a scale of 1-10, compiles skill proficiencies, logs candidate red flags, resolves cheating tab-switches from the DB, and outputs a hiring recommendation (`Strong Hire`, `Hire`, `No Hire`).
  * **General Mode:** Provides default summaries, topics, and key decisions.
* **Assignee Identification:** Automatically parses action items formatted as `"Task - Owner"` and resolves the owner's name against the meeting's participant UUIDs to assign the action items directly in the database.
* **Notification Dispatch:** Once the analysis database record is compiled, it queues a task to the Celery broker to send out email notifications to all participants.

### 3. Notification Worker (`services/meeting-service/notification_worker.py`)
A background Celery worker that manages downstream notification tasks.
* **SMTP Delivery:** Generates professional email reports using **Jinja2** HTML templates (`meeting_summary.html`, `scheduled_meeting_invite.html`, and `meeting_started_notification.html`).
* **Brevo REST Client:** Sends transactional emails securely through the **Brevo (Sendinblue)** API using resilient HTTP calls.

---

## 🗄️ Database Architecture & Schemas

The database uses PostgreSQL (configured locally or via Neon Serverless Postgres). Migrations are handled independently per-service (Auth and Meeting) using Alembic.

```
  ┌──────────────────────┐
  │        users         │
  ├──────────────────────┤
  │ id (UUID, PK)        ◄────────┐
  │ email (String, Unique)       │
  │ name (String)        │        │
  │ created_at (DateTime)│        │
  └──────────────────────┘        │
             ▲                    │
             │                    │
             │                    │
  ┌──────────┴───────────┐        │
  │  scheduled_meetings  │        │
  ├──────────────────────┤        │
  │ id (UUID, PK)        │        │
  │ join_code (String)   │        │
  │ creator_id (UUID, FK)│        │
  │ title (String)       │        │
  │ mode (String)        │        │
  │ scheduled_date       │        │
  │ scheduled_start_time │        │
  │ expected_duration    │        │
  │ objectives (String)  │        │
  │ participants (JSON)  │        │
  │ created_at           │        │
  └──────────────────────┘        │
                                  │
                                  │
  ┌──────────────────────┐        │
  │       meetings       │        │
  ├──────────────────────┤        │
  │ id (UUID, PK)        ◄───┐    │
  │ join_code (String)   │   │    │
  │ title (String)       │   │    │
  │ creator_id (UUID)    │   │    │
  │ status (String)      │   │    │
  │ mode (String)        │   │    │
  │ created_at (DateTime)│   │    │
  │ started_at (DateTime)│   │    │
  │ ended_at (DateTime)  │   │    │
  │ duration_seconds     │   │    │
  └──────────────────────┘   │    │
             ▲               │    │
             │               │    │
   ┌─────────┼─────────┬─────┴────┴──────────┬──────────────────┐
   │         │         │                     │                  │
┌──┴──┐   ┌──┴──┐   ┌──┴──┐               ┌──┴──┐            ┌──┴──┐
│ A   │   │ B   │   │ C   │               │ D   │            │ E   │
└─────┘   └─────┘   └─────┘               └─────┘            └─────┘
```

* **A: `meeting_participants`**: Tracks which users joined the room, their role (`host`, `attendee`), joined/left timestamps, and calculated cumulative speaking times.
* **B: `transcript_segments`**: Stores chronological meeting statements with start/end audio timestamps, user IDs, and text blocks.
* **C: `meeting_analysis`**: Stores overall meeting metrics including executive summary, sentiment analysis, mode classification, and custom JSON-formatted insights.
* **D: `action_items`**: Holds items with a descriptions, completion states, and resolved `assignee_id` foreign keys linking back to `users`.
* **E: `decisions`**: Stores key agreements and conclusions reached during the meeting session.
* **F: `meeting_alerts`**: Tracks integrity markers (`tab_switch` alerts) linked to participants.
* **G: `user_transcript_analysis`**: Persists personal communication coaching, QA performance scoring, confidence ratings, and speech improvement recommendations.

---

## 🔄 Core Backend Data Flows

### Real-Time Transcription Pipeline
```
[Client Mic] 
     │ (AudioWorklet records 16kHz PCM chunks)
     ▼
[Meeting Service Socket] (audio_chunk event)
     │ 
     ├─► [Redis Broker] (rpush raw audio -> "audio_queue")
     ▼
[Transcript Worker] (blpop loop)
     │ 
     ├──► [Silero VAD] (Int16 to Float32; rejects noise, accepts speech)
     │          │
     │          ▼ (Speech Found)
     ├──► [Groq Whisper API] (transcribes to text)
     │          │
     │          ▼ (Filters hallucinations & saves to DB via HTTP endpoint)
     └──► [Redis Pub/Sub] (publishes text -> "transcript_updates")
                │
                ▼
[Meeting Service Redis Listener] (handles subscription)
     │
     ▼ (sio.emit "transcript_update" to Socket Room)
[All Clients in Meeting Room]
```

### Meeting Conclusion & AI Analysis Pipeline
```
[Host Clicks "End Meeting"] 
     │
     ▼ (HTTP POST /meetings/{id}/end or Webhook room_finished)
[Meeting Service REST Router]
     │
     ├──► Marks status = "ended" & updates duration_seconds
     │
     └──► [Redis Broker] (rpush -> "meeting_ended_queue")
                │
                ▼
[AI Analysis Worker] (blpop loop)
     │
     ├──► Fetches full meeting transcript & tab-switch alerts from DB
     ├──► Evaluates target template based on Mode (Business / Interview / General)
     ├──► Calls [Groq LLaMA-3.3 70B API] (generates structured JSON)
     ├──► Resolves action item assignees names into participant UUIDs
     │
     ▼ (Persists report analysis & action items to DB)
[Celery Dispatch] (send_task -> "send_meeting_summary_email")
     │
     ▼
[Notification Worker] (Celery consumer)
     │
     ├──► Renders Jinja2 HTML email templates
     └──► Calls [Brevo API Client] (Sends email to participants)
```

---

## ⚙️ Environment Variables & Config

Create a `.env` configuration file in each service folder (`/services/api-gateway/.env`, `/services/auth-service/.env`, and `/services/meeting-service/.env`) using the following structure:

```env
# --- General Config ---
PORT=8002
ALLOWED_ORIGINS=["http://localhost:3000","https://cadence-meeting-intelligence.vercel.app"]

# --- Security & Auth (Shared across gateway, auth, and meeting service) ---
SECRET_KEY=your-jwt-signing-secret-key-minimum-32-chars
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7

# --- Databases ---
# PostgreSQL URL (use Neon DB or local Postgres)
DATABASE_URL=postgresql://neondb_owner:password@ep-host-name.pooler.us-east-2.neon.tech/dbname?sslmode=require
# Redis URL (use Upstash Redis for serverless cloud or local)
REDIS_URL=redis://localhost:6379/0

# --- Downstream Service Routing (Used by API Gateway) ---
AUTH_SERVICE_URL=http://localhost:8001
MEETING_SERVICE_URL=http://localhost:8002

# --- Third-Party Integrations ---
# Groq API for Whisper (Speech) and LLaMA 3 (Intelligence)
GROQ_API=gsk_your_groq_api_credential_key

# Brevo SMTP API (Used by Notification Worker)
BREVO_API_KEY=xkeysib-your-brevo-api-key
FROM_EMAIL=your-verified-sender@domain.com

# LiveKit WebRTC Configuration (Used for Webhook WebRTC rooms verification)
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your-livekit-api-key
LIVEKIT_API_SECRET=your-livekit-api-secret
```

---

## 🚀 Deployment Manual

### Option 1: VPS Deployment (Docker Compose + Caddy)
Deploying to a single-node Linux VPS using Docker Compose is the most secure and cost-efficient method.

1. **Clone the Repo & Configure `.env` files** under each `/services/` directory.
2. **Execute Docker Compose:**
   ```bash
   docker compose up --build -d
   ```
3. **Run Database Migrations:**
   Inside the running service containers:
   ```bash
   docker compose exec auth-service alembic upgrade head
   docker compose exec meeting-service alembic upgrade head
   ```
4. **Secure Reverse Proxy using Caddy:**
   Write a `/etc/caddy/Caddyfile` to expose Gateway HTTP endpoints and Socket.io signaling on SSL:
   ```caddy
   api.yourdomain.com {
       reverse_proxy /auth/* http://localhost:8000
       reverse_proxy /meetings/* http://localhost:8000
       reverse_proxy /health http://localhost:8000

       reverse_proxy /socket.io/* http://localhost:8002 {
           header_up Host {host}
           header_up X-Real-IP {remote_host}
       }
   }
   ```
   Restart Caddy: `sudo systemctl restart caddy`.

### Option 2: 100% Free Hosting Tier
You can host this entire microservices backend for free with no credit card required.

1. **Database:** Use **Neon DB**'s free tier PostgreSQL instance.
2. **Redis Message Broker:** Use **Upstash**'s free tier serverless Redis (supports SSL `rediss://` protocol).
3. **Core APIs (`api-gateway`, `auth-service`, `meeting-service`):** Deploy as Web Services on **Render** (note: free tier containers spin down after 15 minutes of inactivity).
4. **Workers (`transcript-worker` / `ai-worker`):**
   * **Alternative A (Render Health Hack):** Deploy as Render Web Services running a background threading FastAPI health-check endpoint (listening to `$PORT` to satisfy Render checks) alongside the Celery/loop processes using a `start.sh` script.
   * **Alternative B (Hugging Face Spaces):** Deploy custom Docker containers on Hugging Face Spaces (choose Docker SDK). Expose health endpoints on port `7860`. This grants you a **16GB RAM container** completely free, which is perfect for running the Silero PyTorch VAD models.
