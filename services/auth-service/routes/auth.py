from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from sqlalchemy.orm import Session
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError

from core.database import get_db
from core.security import decode_token
from core.config import settings
import models, schemas
from services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    try:
        payload = decode_token(token)
        user_id = payload.get("sub")
        if payload.get("type") != "access" or user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return AuthService.get_user_by_id(db, user_id)

@router.post("/register", response_model=schemas.UserOut)
def register(user_in: schemas.UserCreate, db: Session = Depends(get_db)):
    return AuthService.register_user(db, user_in)

@router.post("/login", response_model=schemas.Token)
def login(response: Response, login_data: schemas.LoginRequest, db: Session = Depends(get_db)):
    auth_data = AuthService.authenticate_user(db, login_data)
    user = AuthService.get_user_by_id(db, auth_data["user_id"])
    
    # Set refresh token in HttpOnly cookie
    response.set_cookie(
        key="refresh_token",
        value=auth_data["refresh_token"],
        httponly=True,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        expires=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        samesite="lax",
        secure=False, # Set to True in production with HTTPS
    )
    
    return {
        "access_token": auth_data["access_token"],
        "token_type": "bearer",
        "user_id": auth_data["user_id"],
        "user_name": user.name
    }

@router.post("/refresh", response_model=schemas.Token)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token missing")
    
    auth_data = AuthService.refresh_access_token(db, refresh_token)
    user = AuthService.get_user_by_id(db, auth_data["user_id"])
    
    # Rotate refresh token in cookie
    response.set_cookie(
        key="refresh_token",
        value=auth_data["refresh_token"],
        httponly=True,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        expires=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        samesite="lax",
        secure=False,
    )
    
    return {
        "access_token": auth_data["access_token"],
        "token_type": "bearer",
        "user_id": auth_data["user_id"],
        "user_name": user.name
    }

@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    refresh_token = request.cookies.get("refresh_token")
    if refresh_token:
        AuthService.logout_user(db, refresh_token)
    
    response.delete_cookie(key="refresh_token")
    return {"message": "Successfully logged out"}

@router.get("/me", response_model=schemas.UserOut)
def get_me(current_user: models.User = Depends(get_current_user)):
    return current_user
