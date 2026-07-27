from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from jose import JWTError
import models, schemas
from core.security import (
    get_password_hash, 
    verify_password, 
    create_access_token, 
    create_refresh_token,
    decode_token
)
from core.config import settings

class AuthService:
    @staticmethod
    def sync_user_to_meeting_service(user: models.User):
        try:
            import httpx
            meeting_service_url = "http://meeting-service:8002/meetings/internal/users/sync"
            httpx.post(meeting_service_url, json={
                "id": str(user.id),
                "email": user.email,
                "name": user.name,
                "company_name": user.company_name,
                "role": user.role,
                "photo_url": user.photo_url,
                "meeting_name": user.meeting_name
            }, timeout=5.0)
        except Exception as e:
            print(f"Failed to sync user {user.id} to meeting service: {e}")

    @staticmethod
    def register_user(db: Session, user_in: schemas.UserCreate):
        user = db.query(models.User).filter(models.User.email == user_in.email).first()
        if user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User with this email already exists"
            )
        
        hashed_password = get_password_hash(user_in.password)
        new_user = models.User(
            email=user_in.email,
            name=user_in.name,
            hashed_password=hashed_password
        )
        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        # Sync to Meeting Service
        AuthService.sync_user_to_meeting_service(new_user)

        return new_user

    @staticmethod
    def update_user_profile(db: Session, user_id: str, profile_data: schemas.UserProfileUpdate):
        user = db.query(models.User).filter(models.User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Update fields if provided
        if profile_data.name is not None:
            user.name = profile_data.name
        if profile_data.company_name is not None:
            user.company_name = profile_data.company_name
        if profile_data.role is not None:
            user.role = profile_data.role
        if profile_data.photo_url is not None:
            user.photo_url = profile_data.photo_url
        if profile_data.meeting_name is not None:
            user.meeting_name = profile_data.meeting_name
            
        db.commit()
        db.refresh(user)
        
        # Sync to Meeting Service
        AuthService.sync_user_to_meeting_service(user)
        
        return user


    @staticmethod
    def authenticate_user(db: Session, login_data: schemas.LoginRequest):
        user = db.query(models.User).filter(models.User.email == login_data.email).first()
        if not user or not verify_password(login_data.password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password"
            )
        access_token = create_access_token(subject=user.id)
        refresh_token = create_refresh_token(subject=user.id)
        
        # Store refresh token hash in DB
        expires_at = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
        db_refresh_token = models.RefreshToken(
            user_id=user.id,
            token_hash=refresh_token,
            expires_at=expires_at
        )
        db.add(db_refresh_token)
        db.commit()
        
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "user_id": user.id
        }

    @staticmethod
    def refresh_access_token(db: Session, refresh_token: str):
        try:
            payload = decode_token(refresh_token)
            user_id = payload.get("sub")
            if payload.get("type") != "refresh" or user_id is None:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
        except JWTError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
        
        db_refresh_token = db.query(models.RefreshToken).filter(
            models.RefreshToken.token_hash == refresh_token
        ).first()
        
        if not db_refresh_token or db_refresh_token.expires_at < datetime.utcnow():
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token expired or invalid")
        
        user = db.query(models.User).filter(models.User.id == user_id).first()
        if not user:
             raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

        # Generate new tokens
        access_token = create_access_token(subject=user_id)
        
        # Reuse original expiry for the rotated refresh token
        new_refresh_token = create_refresh_token(
            subject=user_id, 
            expires_at=db_refresh_token.expires_at
        )
        
        # Update refresh token in DB
        db_refresh_token.token_hash = new_refresh_token
        # expires_at is NOT updated to maintain original session duration
        db.commit()
        
        return {
            "access_token": access_token,
            "refresh_token": new_refresh_token,
            "token_type": "bearer",
            "user_id": user.id
        }

    @staticmethod
    def logout_user(db: Session, refresh_token: str):
        db.query(models.RefreshToken).filter(
            models.RefreshToken.token_hash == refresh_token
        ).delete()
        db.commit()
        return {"message": "Successfully logged out"}

    @staticmethod
    def get_user_by_id(db: Session, user_id: str):
        user = db.query(models.User).filter(models.User.id == user_id).first()
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        return user

    @staticmethod
    def get_users_by_ids(db: Session, user_ids: list[str]):
        if not user_ids:
            return []
        return db.query(models.User).filter(models.User.id.in_(user_ids)).all()

    @staticmethod
    def get_all_users(db: Session):
        return db.query(models.User).all()
