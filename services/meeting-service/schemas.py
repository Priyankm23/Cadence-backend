from pydantic import BaseModel, ConfigDict
from uuid import UUID
from datetime import datetime
from typing import Optional

class MeetingBase(BaseModel):
    title: str

class MeetingCreate(MeetingBase):
    mode: Optional[str] = "general"

class Meeting(MeetingBase):
    id: UUID
    creator_id: UUID
    creator_name: Optional[str] = None
    status: Optional[str] = "active"
    mode: Optional[str] = "general"
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    source: Optional[str] = "internal"
    audio_url: Optional[str] = None
    duration_seconds: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)

class ActionItemBase(BaseModel):
    description: str
    is_completed: bool = False
    assignee_id: Optional[UUID] = None

class ActionItemCreate(ActionItemBase):
    meeting_id: UUID

class ActionItemUpdate(BaseModel):
    description: Optional[str] = None
    is_completed: Optional[bool] = None
    assignee_id: Optional[UUID] = None

class ActionItem(ActionItemBase):
    id: UUID
    meeting_id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class DecisionBase(BaseModel):
    description: str

class DecisionCreate(DecisionBase):
    meeting_id: UUID

class Decision(DecisionBase):
    id: UUID
    meeting_id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class MeetingAlertBase(BaseModel):
    meeting_id: UUID
    user_id: UUID
    alert_type: str
    details: Optional[str] = None

class MeetingAlert(MeetingAlertBase):
    id: UUID
    created_at: datetime

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
    sentiment: str
    mode: Optional[str] = "general"
    insights: Optional[dict] = None

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

class MeetingActionItems(BaseModel):
    meeting_id: UUID
    meeting_title: str
    ended_at: Optional[datetime] = None
    action_items: list[ActionItem]

    model_config = ConfigDict(from_attributes=True)

class ScheduledMeetingCreate(BaseModel):
    title: str
    mode: Optional[str] = "general"
    scheduled_date: str
    scheduled_start_time: str
    expected_duration_min: Optional[int] = None
    objectives: Optional[str] = None
    participants: Optional[list[str]] = []

class ScheduledMeetingOut(ScheduledMeetingCreate):
    id: UUID
    creator_id: UUID
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

class UserTranscriptAnalysisCreate(BaseModel):
    analysis_data: dict

class UserTranscriptAnalysis(UserTranscriptAnalysisCreate):
    id: UUID
    meeting_id: UUID
    user_id: UUID
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
