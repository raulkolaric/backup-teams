"""
api/routers/auth.py — Authentication synchronization for the 'Bring Your Own Token' architecture.

Because the University IT Admin blocks third-party app consent, the crawler's
Bearer tokens must be generated locally using a First-Party application mask
(e.g., the local Teams web app).

This endpoint allows the local machine to push the freshly acquired Graph API
credentials (Access Token and/or Refresh Token) to the AWS RDS database so
the background daemon can use them.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import asyncpg

from api.dependencies.db import get_pool

log = logging.getLogger("backup_teams.api.auth")
router = APIRouter()


class TokenSyncRequest(BaseModel):
    """Payload sent by the local token extractor."""
    email: str
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    # We require a secret configured in .env to prevent open access to this endpoint
    sync_secret: str


@router.post("/sync-token")
async def sync_token(
    payload: TokenSyncRequest, 
    pool: asyncpg.Pool = Depends(get_pool)
):
    """
    Receives Microsoft Graph credentials extracted locally and stores them
    in the 'user' table on RDS so the AWS cron job can use them.
    """
    import os
    expected_secret = os.getenv("API_SYNC_SECRET")
    
    if not expected_secret or payload.sync_secret != expected_secret:
        log.warning("Unauthorized token sync attempt for email: %s", payload.email)
        raise HTTPException(status_code=401, detail="Invalid sync_secret")

    # Upsert the user credentials into the database.
    # We update the user row if it exists, otherwise we create a new bot user target.
    query = """
    INSERT INTO "user" (email, name, msteams_email, msteams_refresh_token, msteams_password_encrypted)
    VALUES ($1, $1, $1, $2, $3)
    ON CONFLICT (email) DO UPDATE SET
        msteams_refresh_token = EXCLUDED.msteams_refresh_token,
        msteams_password_encrypted = EXCLUDED.msteams_password_encrypted,
        updated_at = NOW()
    RETURNING id;
    """

    # For the hybrid flow, we are treating `msteams_password_encrypted` as a place to hold 
    # the raw valid Access Token if needed for the current hour, and `msteams_refresh_token`
    # for the long-lived refresh credential if we captured it.
    try:
        user_id = await pool.fetchval(
            query,
            payload.email,
            payload.refresh_token,
            payload.access_token # storing standard short-lived token here for backwards compatibility
        )
        
        log.info("Successfully synced Microsoft Graph credentials for user: %s", payload.email)
        return {"status": "success", "user_id": str(user_id), "message": "Tokens synchronized to RDS."}
    except Exception as e:
        log.error("Failed to sync tokens: %s", e)
        raise HTTPException(status_code=500, detail="Database error during token sync")


class LoginRequest(BaseModel):
    """Payload sent by the frontend after a student logs in with Microsoft."""
    ms_access_token: str


@router.post("/login")
async def login(payload: LoginRequest):
    """
    Verifies the student's identity using from Microsoft Graph.
    If valid, mints our own backend JWT, which the frontend will use to hit
    the locked-down /files and /search endpoints.
    """
    import httpx
    from api.dependencies.security import create_access_token

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {payload.ms_access_token}"}
        )
        
    if resp.status_code != 200:
        log.warning("Student provided an invalid or expired Microsoft access token.")
        raise HTTPException(status_code=401, detail="Invalid Microsoft access token.")
    
    data = resp.json()
    # Handle variations in how different universities map their primary active directory email
    email = data.get("mail") or data.get("userPrincipalName")

    if not email:
        raise HTTPException(status_code=400, detail="Could not extract email from Microsoft profile.")

    # Mint the stateless JWT 
    jwt_token = create_access_token(email)
    
    return {
        "access_token": jwt_token, 
        "token_type": "bearer", 
        "email": email,
        "message": "Successfully authenticated with Backup Teams Vault."
    }

