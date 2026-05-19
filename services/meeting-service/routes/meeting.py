from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlalchemy.orm import Session
from typing import List, AnyStr, Dict, Tuple
from uuid import UUID
from datetime import datetime, timezone
import httpx

from core.database import get_db
from core.config import settings
from livekit import api
from core.security import decode_token
import models, schemas

router = APIRouter(prefix="/meetings", tags=["meetings"])

def fetch_users_by_ids(user_ids: list[str]) -> Dict[str, dict]:
    if not user_ids:
        return {}

    try:
        auth_url = f"{settings.AUTH_SERVICE_URL}/auth/users/batch"
        response = httpx.post(auth_url, json={"user_ids": user_ids}, timeout=10)
        response.raise_for_status()
        users = response.json()
    except Exception:
        users = []

    return {str(u["id"]): u for u in users}

def get_current_user_id(authorization: str = Header(...)) -> UUID:
    try:
        token = authorization.split(" ")[1]
        payload = decode_token(token)
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return UUID(user_id)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or missing token")
    
def get_current_user_name(authorization: str = Header(...)) -> AnyStr:
    try:
        token = authorization.split(" ")[1]
        payload = decode_token(token)
        user_name = payload.get("name") or payload.get("data", {}).get("name")
        if not user_name:
            raise HTTPException(status_code=403, detail="Invalid Token - user_name missing from the token")
        return user_name
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or missing or modified Token")

@router.post("/", response_model=schemas.Meeting)
def create_meeting(
    meeting_in: schemas.MeetingCreate,
    db: Session = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
    user_name: AnyStr = Depends(get_current_user_name)
):
    meeting = models.Meeting(
        title=meeting_in.title,
        creator_id=user_id,
        status="active"
    )
    db.add(meeting)
    db.commit()
    db.refresh(meeting)

    meeting.creator_name = user_name
    
    host_name = user_name if user_name else "Host"
    
    # Automatically add creator as host participant
    host_participant = models.MeetingParticipant(
        meeting_id=meeting.id,
        user_id=user_id,
        display_name=host_name, # We could fetch actual name if available
        role="host"
    )
    db.add(host_participant)
    db.commit()
    
    return meeting

@router.get("/", response_model=List[schemas.Meeting])
def list_meetings(
    db: Session = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    # Join with MeetingParticipant to get all meetings the user is part of
    meetings = db.query(models.Meeting).join(
        models.MeetingParticipant,
        models.Meeting.id == models.MeetingParticipant.meeting_id
    ).filter(
        models.MeetingParticipant.user_id == user_id
    ).order_by(models.Meeting.created_at.desc()).all()

    creator_ids = list({str(m.creator_id) for m in meetings})
    creator_lookup = fetch_users_by_ids(creator_ids)
    for meeting in meetings:
        creator_data = creator_lookup.get(str(meeting.creator_id))
        meeting.creator_name = creator_data.get("name") if creator_data else None

    return meetings

@router.get("/{meeting_id}", response_model=schemas.Meeting)
def get_meeting(
    meeting_id: UUID,
    db: Session = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    meeting = db.query(models.Meeting).filter(models.Meeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    creator_lookup = fetch_users_by_ids([str(meeting.creator_id)])
    creator_data = creator_lookup.get(str(meeting.creator_id))
    meeting.creator_name = creator_data.get("name") if creator_data else None
    return meeting

@router.post("/{meeting_id}/end", response_model=schemas.Meeting)
async def end_meeting(
    meeting_id: UUID,
    payload: schemas.MeetingEndRequest,
    db: Session = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    meeting = db.query(models.Meeting).filter(
        models.Meeting.id == meeting_id,
        models.Meeting.creator_id == user_id
    ).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found or unauthorized")
    
    meeting.status = "ended"
    meeting.ended_at = datetime.now(timezone.utc)
    if payload.duration_seconds is not None:
        meeting.duration_seconds = int(payload.duration_seconds)
    elif meeting.created_at:
        meeting.duration_seconds = int((meeting.ended_at - meeting.created_at).total_seconds())
    else:
        meeting.duration_seconds = None
    db.commit()
    db.refresh(meeting)
    
    # Notify AI service to generate report
    from main import redis_client
    import json
    await redis_client.rpush("meeting_ended_queue", json.dumps({"meeting_id": str(meeting_id)}))
    
    return meeting

@router.get("/{meeting_id}/transcripts", response_model=List[schemas.TranscriptSegment])
def get_transcripts(
    meeting_id: UUID,
    db: Session = Depends(get_db)
):
    meeting = db.query(models.Meeting).filter(models.Meeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    
    segments = db.query(models.TranscriptSegment).filter(
        models.TranscriptSegment.meeting_id == meeting_id
    ).order_by(models.TranscriptSegment.created_at.asc()).all()
    
    # Populate meeting_title for each segment
    for s in segments:
        s.meeting_title = meeting.title
    return segments

@router.get("/{meeting_id}/transcripts/with-stats", response_model=schemas.MeetingTranscriptsWithStats)
def get_transcripts_with_stats(
    meeting_id: UUID,
    db: Session = Depends(get_db)
):
    meeting = db.query(models.Meeting).filter(models.Meeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    segments = db.query(models.TranscriptSegment).filter(
        models.TranscriptSegment.meeting_id == meeting_id
    ).order_by(models.TranscriptSegment.created_at.asc()).all()

    for s in segments:
        s.meeting_title = meeting.title

    total_meeting_seconds = 0
    if meeting.duration_seconds is not None:
        total_meeting_seconds = int(meeting.duration_seconds)
    elif meeting.started_at and meeting.ended_at:
        total_meeting_seconds = int((meeting.ended_at - meeting.started_at).total_seconds())

    per_user: Dict[str, int] = {}
    for segment in segments:
        if segment.start_time is None or segment.end_time is None:
            continue
        duration_ms = max(0, segment.end_time - segment.start_time)
        per_user[str(segment.user_id)] = per_user.get(str(segment.user_id), 0) + int(duration_ms / 1000)

    participant_rows = db.query(models.MeetingParticipant).filter(
        models.MeetingParticipant.meeting_id == meeting_id
    ).all()
    user_lookup = fetch_users_by_ids([str(p.user_id) for p in participant_rows])

    participants: list[schemas.ParticipantSpeakingStat] = []
    for participant in participant_rows:
        user_id_str = str(participant.user_id)
        speaking_seconds = per_user.get(user_id_str, 0)
        speaking_percentage = 0.0
        if total_meeting_seconds > 0:
            speaking_percentage = round((speaking_seconds / total_meeting_seconds) * 100, 2)

        user_data = user_lookup.get(user_id_str)
        participants.append(
            schemas.ParticipantSpeakingStat(
                user_id=participant.user_id,
                user_name=user_data.get("name") if user_data else None,
                email=user_data.get("email") if user_data else None,
                speaking_time_seconds=speaking_seconds,
                speaking_percentage=speaking_percentage
            )
        )

    return schemas.MeetingTranscriptsWithStats(
        meeting_id=meeting.id,
        meeting_title=meeting.title,
        total_meeting_seconds=total_meeting_seconds,
        participants=participants,
        transcripts=segments
    )

@router.get("/{meeting_id}/transcripts/user/{user_id}", response_model=List[schemas.TranscriptSegment])
def get_user_transcripts(
    meeting_id: UUID,
    user_id: UUID,
    db: Session = Depends(get_db)
):
    meeting = db.query(models.Meeting).filter(models.Meeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    
    segments = db.query(models.TranscriptSegment).filter(
        models.TranscriptSegment.meeting_id == meeting_id,
        models.TranscriptSegment.user_id == user_id
    ).order_by(models.TranscriptSegment.created_at.asc()).all()

    for s in segments:
        s.meeting_title = meeting.title
    return segments

@router.get("/users/me/transcripts/aggregated", response_model=schemas.UserAggregatedTranscripts)
def get_my_aggregated_transcripts(
    user_id: UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db)
):
    return get_user_aggregated_transcripts(user_id=user_id, db=db)

@router.get("/users/{user_id}/transcripts/aggregated", response_model=schemas.UserAggregatedTranscripts)
def get_user_aggregated_transcripts(
    user_id: UUID,
    db: Session = Depends(get_db)
):
    participants = db.query(models.MeetingParticipant).filter(
        models.MeetingParticipant.user_id == user_id
    ).all()
    
    total_speaking_time = 0
    meetings_data = []
    
    for p in participants:
        total_speaking_time += (p.speaking_time_seconds or 0)
        
        segments = db.query(models.TranscriptSegment).filter(
            models.TranscriptSegment.meeting_id == p.meeting_id,
            models.TranscriptSegment.user_id == user_id
        ).order_by(models.TranscriptSegment.created_at.asc()).all()
        
        for s in segments:
            s.meeting_title = p.meeting.title
            
        meetings_data.append(schemas.UserMeetingTranscript(
            meeting_id=p.meeting_id,
            meeting_title=p.meeting.title,
            speaking_time_seconds=p.speaking_time_seconds or 0,
            transcripts=segments
        ))
    
    return schemas.UserAggregatedTranscripts(
        user_id=user_id,
        total_speaking_time_seconds=total_speaking_time,
        meetings=meetings_data
    )

@router.post("/{meeting_id}/analysis", response_model=schemas.MeetingAnalysis)
def create_analysis(
    meeting_id: UUID,
    analysis_in: schemas.MeetingAnalysisCreate,
    db: Session = Depends(get_db)
):
    meeting = db.query(models.Meeting).filter(models.Meeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
        
    analysis = models.MeetingAnalysis(
        meeting_id=meeting_id,
        summary=analysis_in.summary,
        action_items=analysis_in.action_items,
        sentiment=analysis_in.sentiment
    )
    db.add(analysis)
    try:
        db.commit()
        db.refresh(analysis)
    except Exception as e:
        db.rollback()
        # If it already exists, update it instead
        existing = db.query(models.MeetingAnalysis).filter(models.MeetingAnalysis.meeting_id == meeting_id).first()
        if existing:
            existing.summary = analysis_in.summary
            existing.action_items = analysis_in.action_items
            existing.sentiment = analysis_in.sentiment
            db.commit()
            db.refresh(existing)
            return existing
        raise HTTPException(status_code=400, detail=str(e))
    return analysis

@router.get("/{meeting_id}/analysis", response_model=schemas.MeetingAnalysis)
def get_analysis(
    meeting_id: UUID,
    db: Session = Depends(get_db)
):
    analysis = db.query(models.MeetingAnalysis).filter(models.MeetingAnalysis.meeting_id == meeting_id).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return analysis

@router.post("/{meeting_id}/transcripts", response_model=schemas.TranscriptSegment)
def create_transcript_segment(
    meeting_id: UUID,
    segment_in: schemas.TranscriptSegmentCreate,
    db: Session = Depends(get_db)
    # Note: We omit user_id auth here so the internal transcript-worker can call it directly.
    # In production, we should secure this with a service API key or internal JWT.
):
    meeting = db.query(models.Meeting).filter(models.Meeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
        
    segment = models.TranscriptSegment(
        meeting_id=meeting_id,
        user_id=segment_in.user_id,
        user_name=segment_in.user_name,
        text=segment_in.text,
        start_time=segment_in.start_time,
        end_time=segment_in.end_time
    )
    db.add(segment)
    db.commit()
    db.refresh(segment)
    return segment

@router.get("/{meeting_id}/participants", response_model=List[schemas.MeetingParticipant])
def list_participants(
    meeting_id: UUID,
    db: Session = Depends(get_db)
):
    meeting = db.query(models.Meeting).filter(models.Meeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    
    participants = db.query(models.MeetingParticipant).filter(
        models.MeetingParticipant.meeting_id == meeting_id,
        models.MeetingParticipant.left_at.is_(None)
    ).all()

    user_lookup = fetch_users_by_ids([str(p.user_id) for p in participants])
    for participant in participants:
        user_data = user_lookup.get(str(participant.user_id))
        if user_data:
            participant.user_name = user_data.get("name")
            participant.email = user_data.get("email")
        else:
            participant.user_name = None
            participant.email = None

    return participants

@router.get("/{meeting_id}/participants/all", response_model=List[schemas.MeetingParticipant])
def list_all_participants(
    meeting_id: UUID,
    db: Session = Depends(get_db)
):
    meeting = db.query(models.Meeting).filter(models.Meeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    participants = db.query(models.MeetingParticipant).filter(
        models.MeetingParticipant.meeting_id == meeting_id
    ).all()

    user_lookup = fetch_users_by_ids([str(p.user_id) for p in participants])
    for participant in participants:
        user_data = user_lookup.get(str(participant.user_id))
        if user_data:
            participant.user_name = user_data.get("name")
            participant.email = user_data.get("email")
        else:
            participant.user_name = None
            participant.email = None

    return participants

@router.post("/{meeting_id}/join", response_model=schemas.MeetingParticipant)
def join_meeting(
    meeting_id: UUID,
    participant_in: schemas.MeetingParticipantCreate,
    db: Session = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    meeting = db.query(models.Meeting).filter(models.Meeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
        
    if meeting.status != "active":
        raise HTTPException(status_code=400, detail="Cannot join an inactive meeting")

    if meeting.started_at is None and meeting.creator_id == user_id:
        meeting.started_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(meeting)
        
    # Check if already joined
    participant = db.query(models.MeetingParticipant).filter(
        models.MeetingParticipant.meeting_id == meeting_id,
        models.MeetingParticipant.user_id == user_id,
        models.MeetingParticipant.left_at.is_(None)
    ).first()
    
    if participant:
        return participant
        
    new_participant = models.MeetingParticipant(
        meeting_id=meeting_id,
        user_id=user_id,
        display_name=participant_in.display_name
    )
    db.add(new_participant)
    db.commit()
    db.refresh(new_participant)
    return new_participant

@router.post("/{meeting_id}/leave", response_model=schemas.MeetingParticipant)
def leave_meeting(
    meeting_id: UUID,
    db: Session = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    participant = db.query(models.MeetingParticipant).filter(
        models.MeetingParticipant.meeting_id == meeting_id,
        models.MeetingParticipant.user_id == user_id,
        models.MeetingParticipant.left_at.is_(None)
    ).first()

    if not participant:
        raise HTTPException(status_code=404, detail="Active participant not found")

    participant.left_at = datetime.utcnow()
    db.commit()
    db.refresh(participant)
    return participant

@router.post("/{meeting_id}/livekit-token")
def create_livekit_token(
      meeting_id: UUID,
      db: Session = Depends(get_db),
      user_id: UUID = Depends(get_current_user_id)
  ):
      if not settings.LIVEKIT_API_KEY or not settings.LIVEKIT_API_SECRET:
          raise HTTPException(status_code=500, detail="LiveKit credentials not configured")

      participant = db.query(models.MeetingParticipant).filter(
          models.MeetingParticipant.meeting_id == meeting_id,
          models.MeetingParticipant.user_id == user_id,
          models.MeetingParticipant.left_at.is_(None)
      ).first()

      display_name = (participant.display_name if participant else None) or str(user_id)

      room_name = str(meeting_id)
      identity = str(user_id)
      token = api.AccessToken(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
      token.with_identity(identity)
      token.with_name(display_name)
      token.with_grants(api.VideoGrants(room_join=True, room=room_name))
      return {
          "token": token.to_jwt(),
          "url": settings.LIVEKIT_URL,
          "room": room_name,
          "identity": identity,
          "display_name": display_name
      }
