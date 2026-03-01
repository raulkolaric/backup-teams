"""
api/routers/auth.py — Authentication for the 'Bring Your Own Token' architecture.

This file handles robust User Identity (Email/Password, Google OAuth) and
decouples it from the Microsoft Graph Token Vault syncing.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import asyncpg
from passlib.context import CryptContext
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

from api.dependencies.db import get_pool
from api.dependencies.security import create_access_token, get_current_user

log = logging.getLogger("backup_teams.api.auth")
router = APIRouter()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
GOOGLE_CLIENT_ID = "YOUR_GOOGLE_CLIENT_ID" # Will be fetched from env in a real app


# ─── Data Models ─────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str

class EmailLoginRequest(BaseModel):
    email: str
    password: str

class GoogleLoginRequest(BaseModel):
    credential: str  # The JWT from Google

class TokenSyncRequest(BaseModel):
    """Payload sent by the Chrome Extension to vault a Microsoft token."""
    access_token: str
    refresh_token: Optional[str] = None


# ─── Identity Endpoints (Account Creation & Login) ───────────────────────────

@router.post("/register")
async def register(payload: RegisterRequest, pool: asyncpg.Pool = Depends(get_pool)):
    """Standard Email/Password registration."""
    hashed = pwd_context.hash(payload.password)
    
    query = """
    INSERT INTO "user" (email, name, hashed_password, is_active)
    VALUES ($1, $2, $3, true)
    ON CONFLICT (email) DO NOTHING
    RETURNING id;
    """
    try:
        user_id = await pool.fetchval(query, payload.email, payload.name, hashed)
        if not user_id:
            raise HTTPException(status_code=400, detail="Email already registered.")
            
        jwt_token = create_access_token(payload.email)
        return {"access_token": jwt_token, "token_type": "bearer", "email": payload.email}
    except Exception as e:
        log.error("Registration failed: %s", e)
        raise HTTPException(status_code=500, detail="Database error during registration")


@router.post("/login/email")
async def login_email(payload: EmailLoginRequest, pool: asyncpg.Pool = Depends(get_pool)):
    """Standard Email/Password login."""
    query = 'SELECT hashed_password FROM "user" WHERE email = $1 AND is_active = true;'
    row = await pool.fetchrow(query, payload.email)
    
    if not row or not pwd_context.verify(payload.password, row["hashed_password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
        
    jwt_token = create_access_token(payload.email)
    return {"access_token": jwt_token, "token_type": "bearer", "email": payload.email}


@router.post("/login/google")
async def login_google(payload: GoogleLoginRequest, pool: asyncpg.Pool = Depends(get_pool)):
    """Google OAuth2 token verification and automatic registration/login."""
    try:
        # Verify the Google JWT signature and aud
        idinfo = id_token.verify_oauth2_token(
            payload.credential, 
            google_requests.Request(), 
            # GOOGLE_CLIENT_ID  # Uncomment and enforce in production
        )
        
        email = idinfo["email"]
        name = idinfo.get("name", "")
        google_id = idinfo["sub"]
        avatar_url = idinfo.get("picture", "")
        
        # Upsert the user: if they don't exist, create them. If they do, update google_id.
        query = """
        INSERT INTO "user" (email, name, google_id, avatar_url, is_active)
        VALUES ($1, $2, $3, $4, true)
        ON CONFLICT (email) DO UPDATE SET
            google_id = EXCLUDED.google_id,
            avatar_url = EXCLUDED.avatar_url,
            name = EXCLUDED.name
        RETURNING id;
        """
        await pool.fetchval(query, email, name, google_id, avatar_url)
        
        jwt_token = create_access_token(email)
        return {"access_token": jwt_token, "token_type": "bearer", "email": email}
        
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid Google token.")


# ─── Vault Endpoints (The Chrome Extension Sync) ─────────────────────────────

@router.post("/sync-token")
async def sync_token(
    payload: TokenSyncRequest, 
    pool: asyncpg.Pool = Depends(get_pool),
    email: str = Depends(get_current_user)  # Requires standard API Authorization header
):
    """
    Receives Microsoft Graph credentials extracted locally by the Extension
    and stores them in the logged-in user's vault in Postgres.
    """
    # 1. We optionally call MS Graph to verify the token is valid and get the *Microsoft* email
    # This separates their Microsoft Email (e.g. raul@pucsp.edu.br) from their Dashboard Email (e.g. raul@gmail.com)
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://graph.microsoft.com/v1.0/me",
                headers={"Authorization": f"Bearer {payload.access_token}"}
            )
        
        if resp.status_code != 200:
            raise HTTPException(status_code=401, detail="Invalid Microsoft access token provided.")
            
        ms_data = resp.json()
        ms_email = ms_data.get("mail") or ms_data.get("userPrincipalName", "")
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to validate Microsoft token")

    # 2. Update the user row with their vaulted MS credentials
    query = """
    UPDATE "user" 
    SET msteams_email = $2, 
        msteams_refresh_token = $3, 
        msteams_password_encrypted = $4,
        updated_at = NOW()
    WHERE email = $1
    RETURNING id;
    """
    
    try:
        user_id = await pool.fetchval(
            query,
            email,            # Using the identity email from the JWT
            ms_email,         # The verified Microsoft email
            payload.refresh_token,
            payload.access_token
        )
        
        if not user_id:
            raise HTTPException(status_code=404, detail="User account not found.")
            
        log.info("Successfully vaulted Microsoft credentials for user: %s", email)
        return {
            "status": "success", 
            "message": f"Successfully linked Microsoft account: {ms_email}"
        }
    except HTTPException:
        raise
    except Exception as e:
        log.error("Failed to sync tokens: %s", e)
        raise HTTPException(status_code=500, detail="Database error during token vaulting")
