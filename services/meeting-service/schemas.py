from pydantic import BaseModel, ConfigDict
from uuid import UUID
from datetime import datetime
from typing import Optional

class MeetingBase(BaseModel):
    title: str

class MeetingCreate(MeetingBase):
    pass

class Meeting(MeetingBase):
    id: UUID
    creator_id: UUID
    creator_name: Optional[str] = None
    status: Optional[str] = "active"
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    source: Optional[str] = "internal"
    audio_url: Optional[str] = None
    duration_seconds: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)

class TranscriptSegmentCreate(BaseModel):
    user_id: UUID
    user_name: Optional[str] = None
    text: str
    start_time: Optional[int] = None
    end_time: Optional[int] = None

class TranscriptSegment(TranscriptSegmentCreate):
    id: UUID
    meeting_id: UUID
    meeting_title: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

class UserMeetingTranscript(BaseModel):
    meeting_id: UUID
    meeting_title: Optional[str] = None
    speaking_time_seconds: int = 0
    transcripts: list[TranscriptSegment]

class UserAggregatedTranscripts(BaseModel):
    user_id: UUID
    total_speaking_time_seconds: int = 0
    meetings: list[UserMeetingTranscript]

class ParticipantSpeakingStat(BaseModel):
    user_id: UUID
    user_name: Optional[str] = None
    email: Optional[str] = None
    speaking_time_seconds: int = 0
    speaking_percentage: float = 0.0

class MeetingTranscriptsWithStats(BaseModel):
    meeting_id: UUID
    meeting_title: Optional[str] = None
    total_meeting_seconds: int = 0
    participants: list[ParticipantSpeakingStat]
    transcripts: list[TranscriptSegment]

class MeetingAnalysisCreate(BaseModel):
    summary: str
    action_items: str
    sentiment: str

class MeetingEndRequest(BaseModel):
    duration_seconds: Optional[int] = None

class MeetingAnalysis(MeetingAnalysisCreate):
    id: UUID
    meeting_id: UUID
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

class MeetingParticipantCreate(BaseModel):
    display_name: Optional[str] = None

class MeetingParticipant(MeetingParticipantCreate):
    id: UUID
    meeting_id: UUID
    user_id: UUID
    user_name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = "attendee"
    joined_at: Optional[datetime] = None
    left_at: Optional[datetime] = None
    speaking_time_seconds: Optional[int] = 0

    model_config = ConfigDict(from_attributes=True)
