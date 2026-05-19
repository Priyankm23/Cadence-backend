import uuid
from sqlalchemy import Column, String, DateTime, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from core.database import Base

from sqlalchemy.orm import relationship

class Meeting(Base):
    __tablename__ = "meetings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String, nullable=False)
    creator_id = Column(UUID(as_uuid=True), nullable=False)
    status = Column(String, default="active") # active, ended
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    source = Column(String, default="internal")
    audio_url = Column(String, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    
    transcripts = relationship("TranscriptSegment", back_populates="meeting", cascade="all, delete-orphan")

class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    meeting_id = Column(UUID(as_uuid=True), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    user_name = Column(String, nullable=True) # New field to store the name at the time of transcription
    text = Column(String, nullable=False)
    start_time = Column(Integer, nullable=True)
    end_time = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    meeting = relationship("Meeting", back_populates="transcripts")

class MeetingAnalysis(Base):
    __tablename__ = "meeting_analysis"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    meeting_id = Column(UUID(as_uuid=True), ForeignKey("meetings.id", ondelete="CASCADE"), unique=True, nullable=False)
    summary = Column(String, nullable=True)
    action_items = Column(String, nullable=True) # Will store JSON array string
    sentiment = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    meeting = relationship("Meeting")

class MeetingParticipant(Base):
    __tablename__ = "meeting_participants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    meeting_id = Column(UUID(as_uuid=True), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    display_name = Column(String, nullable=True)
    role = Column(String, default="attendee") # host, attendee
    joined_at = Column(DateTime(timezone=True), server_default=func.now())
    left_at = Column(DateTime(timezone=True), nullable=True)
    speaking_time_seconds = Column(Integer, default=0)

    meeting = relationship("Meeting")
