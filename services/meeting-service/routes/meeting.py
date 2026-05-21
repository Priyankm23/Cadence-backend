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
        status="active",
        mode=meeting_in.mode
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

@router.get("/action-items/last-completed", response_model=list[schemas.MeetingActionItems])
def get_action_items_last_completed(
    db: Session = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    # Get distinct meeting IDs the user is part of
    participant_meeting_ids = db.query(models.MeetingParticipant.meeting_id).filter(
        models.MeetingParticipant.user_id == user_id
    ).distinct().subquery()

    # Get last 5 completed meetings where the user is a participant
    meetings = db.query(models.Meeting).filter(
        models.Meeting.id.in_(participant_meeting_ids),
        models.Meeting.status == "ended"
    ).order_by(
        models.Meeting.ended_at.desc(),
        models.Meeting.created_at.desc()
    ).limit(5).all()

    result = []
    for meeting in meetings:
        if meeting.mode == "interview" and meeting.creator_id != user_id:
            action_items = []
        else:
            action_items = db.query(models.ActionItem).filter(
                models.ActionItem.meeting_id == meeting.id
            ).all()
            
        result.append(
            schemas.MeetingActionItems(
                meeting_id=meeting.id,
                meeting_title=meeting.title,
                ended_at=meeting.ended_at,
                action_items=action_items
            )
        )
    return result

@router.get("/{meeting_id}/internal", response_model=schemas.Meeting)
def get_meeting_internal(
    meeting_id: UUID,
    db: Session = Depends(get_db)
):
    """Auth-free endpoint for internal service-to-service calls (ai-service, transcript-service).
    NOT intended to be exposed through the API gateway to external clients.
    """
    meeting = db.query(models.Meeting).filter(models.Meeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting

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

    # Aggregate speaking time by user_id (stringified)
    per_user: Dict[str, int] = {}
    for segment in segments:
        if segment.start_time is None or segment.end_time is None:
            continue
        duration_ms = max(0, segment.end_time - segment.start_time)
        u_id = str(segment.user_id)
        per_user[u_id] = per_user.get(u_id, 0) + int(duration_ms / 1000)

    # Fetch unique participants for this meeting
    participant_rows = db.query(models.MeetingParticipant).filter(
        models.MeetingParticipant.meeting_id == meeting_id
    ).all()
    
    # Use a set of unique user IDs to avoid duplicates in the response
    # We take all user IDs that either are participants or have spoken (transcripts)
    all_user_ids = {str(p.user_id) for p in participant_rows}
    all_user_ids.update(per_user.keys())

    # Fetch user details (name, email) from auth service
    user_lookup = fetch_users_by_ids(list(all_user_ids))

    participants_stats: list[schemas.ParticipantSpeakingStat] = []
    for user_id_str in all_user_ids:
        speaking_seconds = per_user.get(user_id_str, 0)
        speaking_percentage = 0.0
        if total_meeting_seconds > 0:
            speaking_percentage = round((speaking_seconds / total_meeting_seconds) * 100, 2)

        user_data = user_lookup.get(user_id_str)
        # If user data is missing from auth service, we try to use display_name from meeting_participants
        display_name = None
        user_email = None
        if user_data:
            display_name = user_data.get("name")
            user_email = user_data.get("email")
        else:
            # Fallback to the first participant record we find for this user
            p_record = next((pr for pr in participant_rows if str(pr.user_id) == user_id_str), None)
            if p_record:
                display_name = p_record.display_name

        participants_stats.append(
            schemas.ParticipantSpeakingStat(
                user_id=UUID(user_id_str),
                user_name=display_name,
                email=user_email,
                speaking_time_seconds=speaking_seconds,
                speaking_percentage=speaking_percentage
            )
        )

    return schemas.MeetingTranscriptsWithStats(
        meeting_id=meeting.id,
        meeting_title=meeting.title,
        total_meeting_seconds=total_meeting_seconds,
        participants=participants_stats,
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

@router.get("/{meeting_id}/transcripts/user/me/analysis", response_model=schemas.UserTranscriptAnalysis)
def get_my_transcript_analysis(
    meeting_id: UUID,
    db: Session = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    analysis = db.query(models.UserTranscriptAnalysis).filter(
        models.UserTranscriptAnalysis.meeting_id == meeting_id,
        models.UserTranscriptAnalysis.user_id == user_id
    ).first()
    
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return analysis

@router.post("/{meeting_id}/transcripts/user/me/analysis/trigger")
async def trigger_personal_analysis(
    meeting_id: UUID,
    db: Session = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    meeting = db.query(models.Meeting).filter(models.Meeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
        
    is_participant = db.query(models.MeetingParticipant).filter(
        models.MeetingParticipant.meeting_id == meeting_id,
        models.MeetingParticipant.user_id == user_id
    ).first()
    if not is_participant:
        raise HTTPException(status_code=403, detail="You are not a participant of this meeting")

    # Serve existing if available
    existing = db.query(models.UserTranscriptAnalysis).filter(
        models.UserTranscriptAnalysis.meeting_id == meeting_id,
        models.UserTranscriptAnalysis.user_id == user_id
    ).first()
    if existing:
        return existing

    from main import redis_client
    import json
    await redis_client.rpush("personal_analysis_queue", json.dumps({
        "meeting_id": str(meeting_id),
        "user_id": str(user_id)
    }))

    return {
        "status": "triggered",
        "message": "Personal AI analysis has been queued. Check back shortly.",
        "meeting_id": str(meeting_id)
    }

@router.post("/{meeting_id}/transcripts/user/{target_user_id}/analysis", response_model=schemas.UserTranscriptAnalysis)
def save_personal_analysis(
    meeting_id: UUID,
    target_user_id: UUID,
    analysis_in: schemas.UserTranscriptAnalysisCreate,
    db: Session = Depends(get_db)
    # Auth-free internal endpoint for worker
):
    analysis = models.UserTranscriptAnalysis(
        meeting_id=meeting_id,
        user_id=target_user_id,
        analysis_data=analysis_in.analysis_data
    )
    db.add(analysis)
    try:
        db.commit()
        db.refresh(analysis)
    except Exception as e:
        db.rollback()
        existing = db.query(models.UserTranscriptAnalysis).filter(
            models.UserTranscriptAnalysis.meeting_id == meeting_id,
            models.UserTranscriptAnalysis.user_id == target_user_id
        ).first()
        if existing:
            existing.analysis_data = analysis_in.analysis_data
            db.commit()
            db.refresh(existing)
            return existing
        raise HTTPException(status_code=400, detail=str(e))
    return analysis

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
    
    # Deduplicate by meeting_id in case the user left and rejoined
    unique_meetings: Dict[str, models.MeetingParticipant] = {}
    for p in participants:
        mid = str(p.meeting_id)
        if mid not in unique_meetings:
            unique_meetings[mid] = p
            
    total_speaking_time = 0
    meetings_data = []
    
    for p in unique_meetings.values():
        segments = db.query(models.TranscriptSegment).filter(
            models.TranscriptSegment.meeting_id == p.meeting_id,
            models.TranscriptSegment.user_id == user_id
        ).order_by(models.TranscriptSegment.created_at.asc()).all()
        
        meeting_speaking_time = 0
        for s in segments:
            s.meeting_title = p.meeting.title
            if s.start_time is not None and s.end_time is not None:
                duration_ms = max(0, s.end_time - s.start_time)
                meeting_speaking_time += int(duration_ms / 1000)
                
        total_speaking_time += meeting_speaking_time
            
        meetings_data.append(schemas.UserMeetingTranscript(
            meeting_id=p.meeting_id,
            meeting_title=p.meeting.title,
            speaking_time_seconds=meeting_speaking_time,
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
        sentiment=analysis_in.sentiment,
        mode=analysis_in.mode,
        insights=analysis_in.insights
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
            existing.sentiment = analysis_in.sentiment
            existing.mode = analysis_in.mode
            existing.insights = analysis_in.insights
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

@router.post("/{meeting_id}/analysis/trigger")
async def trigger_analysis(
    meeting_id: UUID,
    db: Session = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    """Manually trigger the AI analysis worker for a completed meeting.
    Use this when the post-meeting analysis was not auto-generated.

    - Only callable by a participant of the meeting.
    - Meeting must have status 'ended'.
    - If analysis already exists, it is served directly from the database.
    - Only queues the AI worker if no analysis record exists yet.
    """
    # 1. Verify the meeting exists and has ended
    meeting = db.query(models.Meeting).filter(models.Meeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    if meeting.status != "ended":
        raise HTTPException(
            status_code=400,
            detail=f"Analysis can only be triggered for ended meetings. This meeting is currently '{meeting.status}'."
        )

    # 2. Verify the caller is a participant of this meeting
    is_participant = db.query(models.MeetingParticipant).filter(
        models.MeetingParticipant.meeting_id == meeting_id,
        models.MeetingParticipant.user_id == user_id
    ).first()
    if not is_participant:
        raise HTTPException(status_code=403, detail="You are not a participant of this meeting")

    # 3. If analysis already exists, serve it straight from the DB — no re-triggering
    existing_analysis = db.query(models.MeetingAnalysis).filter(
        models.MeetingAnalysis.meeting_id == meeting_id
    ).first()
    if existing_analysis:
        return existing_analysis

    # 4. Analysis is missing — push to the Redis queue so the AI worker generates it
    from main import redis_client
    import json
    await redis_client.rpush("meeting_ended_queue", json.dumps({"meeting_id": str(meeting_id)}))

    return {
        "status": "triggered",
        "message": "AI analysis has been queued. Check back in 30–60 seconds.",
        "meeting_id": str(meeting_id)
    }

# --- Action Items ---

@router.get("/{meeting_id}/action-items", response_model=List[schemas.ActionItem])
def list_action_items(
    meeting_id: UUID,
    db: Session = Depends(get_db)
):
    return db.query(models.ActionItem).filter(models.ActionItem.meeting_id == meeting_id).all()

@router.post("/{meeting_id}/action-items", response_model=schemas.ActionItem)
def create_action_item(
    meeting_id: UUID,
    item_in: schemas.ActionItemBase,
    db: Session = Depends(get_db)
):
    item = models.ActionItem(
        meeting_id=meeting_id,
        description=item_in.description,
        is_completed=item_in.is_completed,
        assignee_id=item_in.assignee_id
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item

@router.patch("/action-items/{item_id}", response_model=schemas.ActionItem)
def update_action_item(
    item_id: UUID,
    item_in: schemas.ActionItemUpdate,
    db: Session = Depends(get_db)
):
    item = db.query(models.ActionItem).filter(models.ActionItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Action item not found")
    
    if item_in.description is not None:
        item.description = item_in.description
    if item_in.is_completed is not None:
        item.is_completed = item_in.is_completed
    if item_in.assignee_id is not None:
        item.assignee_id = item_in.assignee_id
        
    db.commit()
    db.refresh(item)
    return item

# --- Decisions ---

@router.get("/{meeting_id}/decisions", response_model=List[schemas.Decision])
def list_decisions(
    meeting_id: UUID,
    db: Session = Depends(get_db)
):
    return db.query(models.Decision).filter(models.Decision.meeting_id == meeting_id).all()

@router.post("/{meeting_id}/decisions", response_model=schemas.Decision)
def create_decision(
    meeting_id: UUID,
    decision_in: schemas.DecisionBase,
    db: Session = Depends(get_db)
):
    decision = models.Decision(
        meeting_id=meeting_id,
        description=decision_in.description
    )
    db.add(decision)
    db.commit()
    db.refresh(decision)
    return decision

# --- Alerts (Anti-Cheat / Security) ---

@router.get("/{meeting_id}/alerts", response_model=List[schemas.MeetingAlert])
def list_meeting_alerts(
    meeting_id: UUID,
    db: Session = Depends(get_db)
):
    return db.query(models.MeetingAlert).filter(models.MeetingAlert.meeting_id == meeting_id).all()

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
    """Returns the unique currently-active participants in a live meeting.
    A participant is considered active if their latest session has left_at = NULL.
    If a user left and rejoined, only their current active session row is kept.
    """
    meeting = db.query(models.Meeting).filter(models.Meeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    # Only rows where the participant is currently in the meeting (left_at IS NULL)
    active_rows = db.query(models.MeetingParticipant).filter(
        models.MeetingParticipant.meeting_id == meeting_id,
        models.MeetingParticipant.left_at.is_(None)
    ).all()

    # Deduplicate by user_id — a rejoining user can have multiple active rows;
    # keep the one with the highest speaking_time_seconds (most data)
    unique: Dict[str, models.MeetingParticipant] = {}
    for row in active_rows:
        uid = str(row.user_id)
        if uid not in unique or (row.speaking_time_seconds or 0) > (unique[uid].speaking_time_seconds or 0):
            unique[uid] = row

    participants = list(unique.values())

    # Enrich with name/email from auth service
    user_lookup = fetch_users_by_ids(list(unique.keys()))
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
    """Returns every unique participant who attended the meeting (including those who left).
    Useful for post-meeting reports on ended meetings.
    A user who left and rejoined multiple times is returned only once.
    """
    meeting = db.query(models.Meeting).filter(models.Meeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    # Fetch every row regardless of left_at — covers full meeting history
    all_rows = db.query(models.MeetingParticipant).filter(
        models.MeetingParticipant.meeting_id == meeting_id
    ).all()

    # Deduplicate by user_id — keep the row with the highest speaking_time_seconds
    # so the response reflects the most accurate aggregated speaking data per user
    unique: Dict[str, models.MeetingParticipant] = {}
    for row in all_rows:
        uid = str(row.user_id)
        if uid not in unique or (row.speaking_time_seconds or 0) > (unique[uid].speaking_time_seconds or 0):
            unique[uid] = row

    participants = list(unique.values())

    # Enrich with name/email from auth service
    user_lookup = fetch_users_by_ids(list(unique.keys()))
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

@router.post("/scheduled", response_model=schemas.ScheduledMeetingOut, status_code=status.HTTP_201_CREATED)
def schedule_meeting(
    meeting_in: schemas.ScheduledMeetingCreate,
    db: Session = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    db_meeting = models.ScheduledMeeting(
        creator_id=user_id,
        title=meeting_in.title,
        mode=meeting_in.mode,
        scheduled_date=meeting_in.scheduled_date,
        scheduled_start_time=meeting_in.scheduled_start_time,
        expected_duration_min=meeting_in.expected_duration_min,
        objectives=meeting_in.objectives,
        participants=meeting_in.participants
    )
    db.add(db_meeting)
    db.commit()
    db.refresh(db_meeting)
    
    # TODO: Implement the logic to send an email with the meeting code to the participants.
    
    return db_meeting

@router.get("/scheduled", response_model=List[schemas.ScheduledMeetingOut])
def get_scheduled_meetings(
    db: Session = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    meetings = db.query(models.ScheduledMeeting).filter(models.ScheduledMeeting.creator_id == user_id).order_by(models.ScheduledMeeting.scheduled_date.asc(), models.ScheduledMeeting.scheduled_start_time.asc()).all()
    return meetings