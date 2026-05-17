import socketio
import redis.asyncio as redis
import json
import base64
import time
import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routes import meeting
import models
from core.database import engine
from core.config import settings
from jose import jwt, JWTError

# Redis client
redis_client = redis.from_url(settings.REDIS_URL, decode_responses=False)

# Create Socket.io server
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')
app = FastAPI(title="Meeting Service")

# Wrap FastAPI app with Socket.io ASGI app
sio_app = socketio.ASGIApp(sio, app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(meeting.router)

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        raise ValueError("Invalid token")

async def redis_listener():
    pubsub = redis_client.pubsub()
    await pubsub.subscribe("transcript_updates")
    print("Subscribed to transcript_updates Redis channel")
    async for message in pubsub.listen():
        if message["type"] == "message":
            try:
                data_bytes = message["data"]
                data_str = data_bytes.decode('utf-8') if isinstance(data_bytes, bytes) else data_bytes
                data = json.loads(data_str)
                meeting_id = data.get("meeting_id")
                if meeting_id:
                    # Emit to socket.io room
                    await sio.emit("transcript_update", data, room=str(meeting_id))
            except Exception as e:
                print(f"Error handling pubsub message: {e}")

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(redis_listener())

@app.get("/")
def read_root():
    return {"service": "Meeting Service", "status": "running"}

# Socket.io Events
@sio.event
async def connect(sid, environ, auth=None):
    if not auth or 'token' not in auth:
        print(f"Connection rejected for {sid}: No token provided")
        return False
    
    try:
        payload = decode_token(auth['token'])
        user_id = payload.get("sub")
        user_name = payload.get("name", "Anonymous")
        
        if not user_id:
            return False
            
        async with sio.session(sid) as session:
            session['user_id'] = user_id
            session['user_name'] = user_name
            
        print(f"Client connected: {sid} (User: {user_name})")
        return True
    except Exception as e:
        print(f"Connection rejected for {sid}: {e}")
        return False

@sio.event
async def disconnect(sid):
    print(f"Client disconnected: {sid}")

@sio.on("join_room")
async def handle_join_room(sid, data):
    async with sio.session(sid) as session:
        user_id = session.get('user_id')
        user_name = session.get('user_name', "Anonymous")

    meeting_id = data.get("meeting_id")
    if meeting_id and user_id:
        await sio.enter_room(sid, str(meeting_id))
        print(f"User {user_name} ({user_id}) joined meeting: {meeting_id}")
        await sio.emit("user_joined", {"user_name": user_name, "user_id": user_id}, room=str(meeting_id), skip_sid=sid)
        await sio.emit("room_joined", {"meeting_id": meeting_id}, to=sid)

@sio.on("leave_room")
async def handle_leave_room(sid, data):
    async with sio.session(sid) as session:
        user_id = session.get('user_id')
        user_name = session.get('user_name', "Anonymous")

    meeting_id = data.get("meeting_id")
    if meeting_id:
        await sio.leave_room(sid, str(meeting_id))
        print(f"User {user_name} ({user_id}) left meeting: {meeting_id}")
        await sio.emit("user_left", {"user_name": user_name, "user_id": user_id}, room=str(meeting_id))

@sio.on("audio_chunk")
async def handle_audio_chunk(sid, data):
    async with sio.session(sid) as session:
        user_id = session.get('user_id')
        user_name = session.get('user_name')

    meeting_id = data.get("meeting_id")
    audio_bytes = data.get("audio")

    if meeting_id and audio_bytes and user_id:
        payload = {
            "meeting_id": meeting_id,
            "user_id": user_id,
            "user_name": user_name,
            "timestamp": time.time(),
            "audio": base64.b64encode(audio_bytes).decode('utf-8')
        }
        await redis_client.rpush("audio_queue", json.dumps(payload))

# --- WebRTC Signaling ---
@sio.on("webrtc_offer")
async def handle_webrtc_offer(sid, data):
    async with sio.session(sid) as session:
        user_id = session.get('user_id')
        
    meeting_id = data.get("meeting_id")
    if meeting_id:
        # Broadcast the offer to everyone else in the room
        data["sender_id"] = user_id
        await sio.emit("webrtc_offer", data, room=str(meeting_id), skip_sid=sid)

@sio.on("webrtc_answer")
async def handle_webrtc_answer(sid, data):
    async with sio.session(sid) as session:
        user_id = session.get('user_id')
        
    meeting_id = data.get("meeting_id")
    if meeting_id:
        # Broadcast the answer to everyone else in the room
        data["sender_id"] = user_id
        await sio.emit("webrtc_answer", data, room=str(meeting_id), skip_sid=sid)

@sio.on("ice_candidate")
async def handle_ice_candidate(sid, data):
    async with sio.session(sid) as session:
        user_id = session.get('user_id')
        
    meeting_id = data.get("meeting_id")
    if meeting_id:
        # Broadcast the candidate to everyone else in the room
        data["sender_id"] = user_id
        await sio.emit("ice_candidate", data, room=str(meeting_id), skip_sid=sid)    