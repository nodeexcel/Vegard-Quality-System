from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from google.auth.transport import requests
from google.oauth2 import id_token
import logging

from app.database import get_db
from app.models import User
from app.schemas import TokenResponse, UserResponse, GoogleAuthRequest
from app.auth import create_access_token, get_current_user
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/google", response_model=TokenResponse)
async def google_auth(
    request: GoogleAuthRequest,
    db: Session = Depends(get_db)
):
    """
    Authenticate user with Google OAuth token
    """
    try:
        # Verify the Google ID token
        # The token can be either an ID token (from Google Sign-In) or access token (from OAuth flow)
        try:
            idinfo = id_token.verify_oauth2_token(
                request.token,
                requests.Request(),
                settings.GOOGLE_CLIENT_ID
            )
        except ValueError:
            # If it's not an ID token, try to get user info from access token
            # This handles the case where frontend sends access token
            try:
                import httpx
                user_info_response = httpx.get(
                    'https://www.googleapis.com/oauth2/v2/userinfo',
                    headers={'Authorization': f'Bearer {request.token}'}
                )
                if user_info_response.status_code != 200:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid Google token"
                    )
                user_info = user_info_response.json()
                idinfo = {
                    'sub': user_info.get('id'),
                    'email': user_info.get('email'),
                    'name': user_info.get('name'),
                    'picture': user_info.get('picture')
                }
            except Exception as e:
                logger.error(f"Failed to verify token: {str(e)}")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid Google token"
                )
        
        # Extract user information
        google_id = idinfo.get("sub")
        email = idinfo.get("email")
        name = idinfo.get("name")
        picture = idinfo.get("picture")
        
        if not email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email not provided by Google"
            )
        
        # Check if user exists, create if not
        user = db.query(User).filter(User.google_id == google_id).first()
        
        if not user:
            # Check if email already exists (shouldn't happen, but safety check)
            existing_user = db.query(User).filter(User.email == email).first()
            if existing_user:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User with this email already exists"
                )
            
            # Create new user with 0 credits
            user = User(
                google_id=google_id,
                email=email,
                name=name,
                picture=picture,
                credits=0
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            logger.info(f"Created new user: {email}")
        else:
            # Update user info in case it changed
            user.email = email
            user.name = name
            user.picture = picture
            db.commit()
            db.refresh(user)
        
        # Create JWT token
        # JWT 'sub' claim must be a string, not an integer
        access_token = create_access_token(data={"sub": str(user.id)})
        
        return TokenResponse(
            access_token=access_token,
            user=UserResponse(
                id=user.id,
                email=user.email,
                name=user.name,
                picture=user.picture,
                credits=user.credits,
                created_at=user.created_at
            )
        )
        
    except ValueError as e:
        logger.error(f"Invalid Google token: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google token"
        )
    except Exception as e:
        logger.error(f"Authentication error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication failed"
        )

@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: User = Depends(get_current_user)
):
    """
    Get current authenticated user info
    """
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        name=current_user.name,
        picture=current_user.picture,
        credits=current_user.credits,
        created_at=current_user.created_at
    )

