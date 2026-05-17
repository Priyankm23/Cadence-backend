# Frontend Integration Guide: Meeting Service

This guide provides the necessary implementation details to integrate the Meeting Service into your frontend application. It covers the complete flow from joining a meeting to handling real-time video/audio and transcripts.

## 1. Prerequisites

- **Socket.io Client:** `npm install socket.io-client`
- **LiveKit Client SDK:** `npm install livekit-client`
- **HTTP Client:** `axios` or native `fetch`

## 2. Authentication

All requests to the Meeting Service (via the API Gateway) must include a Bearer token in the `Authorization` header.

```javascript
const headers = {
    'Authorization': `Bearer ${JWT_TOKEN}`,
    'Content-Type': 'application/json'
};
```

## 3. The Meeting Flow

### Step 1: Join the Meeting (Database)
Before connecting to any real-time services, register the user as a participant in the meeting.

**Endpoint:** `POST /meetings/{meeting_id}/join`  
**Payload:** `{"display_name": "User Name"}`

```javascript
const joinMeeting = async (meetingId, displayName) => {
    const response = await axios.post(`${GATEWAY_URL}/meetings/${meetingId}/join`, 
        { display_name: displayName },
        { headers }
    );
    return response.data; // Returns MeetingParticipant object
};
```

### Step 2: Get LiveKit Token
Retrieve a short-lived token to join the LiveKit video/audio room.

**Endpoint:** `POST /meetings/{meeting_id}/livekit-token`

```javascript
const getLiveKitToken = async (meetingId) => {
    const response = await axios.post(`${GATEWAY_URL}/meetings/${meetingId}/livekit-token`, 
        {}, 
        { headers }
    );
    return response.data.token;
};
```

### Step 3: Connect to LiveKit (Video/Audio)
Use the token to connect to the LiveKit room.

```javascript
import { Room, RoomEvent, VideoPresets } from 'livekit-client';

const connectToRoom = async (token) => {
    const room = new Room();
    
    // Handle incoming tracks (Video/Audio from others)
    room.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
        if (track.kind === 'video') {
            const element = track.attach();
            document.getElementById('video-container').appendChild(element);
        }
        if (track.kind === 'audio') {
            track.attach(); // Audio plays automatically
        }
    });

    await room.connect(LIVEKIT_URL, token);
    
    // Publish local tracks
    await room.localParticipant.enableCameraAndMicrophone();
    
    return room;
};
```

### Step 4: Connect to Socket.io (Transcripts & Events)
Connect to the Socket.io server for real-time events and transcript updates.

**URL:** `ws://api-gateway-url` (or direct meeting service URL)  
**Auth:** Must pass token in the `auth` object.

```javascript
import { io } from 'socket.io-client';

const socket = io(MEETING_SERVICE_URL, {
    auth: { token: JWT_TOKEN }
});

socket.on('connect', () => {
    // Join the specific meeting room for updates
    socket.emit('join_room', { meeting_id: MEETING_ID });
});

// Real-time transcript updates
socket.on('transcript_update', (data) => {
    console.log(`[${data.user_name}]: ${data.text}`);
    // Append to UI transcript list
});

// Participant events
socket.on('user_joined', (data) => {
    console.log(`${data.user_name} joined the meeting`);
});
```

## 4. Handling Audio for AI Transcription
The system supports two methods for transcription:

### Method A: Client-Side Audio Streaming (Recommended)
Capture chunks of audio and send them over Socket.io to the meeting service.

```javascript
// Example: Sending 100ms chunks of audio
mediaRecorder.ondataavailable = (event) => {
    if (event.data.size > 0) {
        socket.emit('audio_chunk', {
            meeting_id: MEETING_ID,
            audio: event.data // Blob or ArrayBuffer
        });
    }
};
mediaRecorder.start(100);
```

### Method B: LiveKit Egress (Server-Side)
If LiveKit is configured with an Egress/Ingress service, the AI service can tap into the room's audio directly. This requires no additional frontend code beyond Step 3.

## 5. Summary of API Endpoints

| Task | Method | Endpoint |
| :--- | :--- | :--- |
| Create Meeting | `POST` | `/meetings/` |
| List My Meetings | `GET` | `/meetings/` |
| Get Meeting Details | `GET` | `/meetings/{id}` |
| Join Meeting | `POST` | `/meetings/{id}/join` |
| Get LiveKit Token | `POST` | `/meetings/{id}/livekit-token` |
| Get Transcripts | `GET` | `/meetings/{id}/transcripts` |
| Get Analysis | `GET` | `/meetings/{id}/analysis` |

## 6. Troubleshooting
- **Video not showing:** Ensure `track.attach()` is called inside the `TrackSubscribed` event listener.
- **Socket.io connection fail:** Verify the `auth` token is passed correctly. The server checks the `sub` claim in the JWT.
- **401 Unauthorized:** Ensure the Bearer token is valid and not expired.
