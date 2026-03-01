"""
api/dependencies/security.py — JWT authentication logic.
"""
import os
from datetime import datetime, timedelta

import jwt
import asyncpg
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from api.dependencies.db import get_pool

security_scheme = HTTPBearer()

def get_secret() -> str:
    # Always pull from .env. The default is just for local testing fallback.
    return os.environ.get("JWT_SECRET", "super-secret-default-key")

def create_access_token(email: str) -> str:
    """Generate a JWT token for the given user email."""
    to_encode = {"sub": email}
    # Tokens expire after 12 hours
    expire = datetime.utcnow() + timedelta(hours=12)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, get_secret(), algorithm="HS256")
    return encoded_jwt

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
    pool: asyncpg.Pool = Depends(get_pool)
) -> str:
    """
    FastAPI Dependency to get the current user's email from the JWT token.
    Inject this into any router to lock it down.
    Throws HTTP 401 if the token is invalid, missing, or expired.
    """
    token = credentials.credentials
    try:
        payload = jwt.decode(token, get_secret(), algorithms=["HS256"])
        email: str = payload.get("sub")
        if email is None:
            raise HTTPException(status_code=401, detail="Invalid auth token payload")
        
        # Check database to see if the user is active
        # New accounts are created with is_active=false to prevent unauthorized syncing
        query = 'SELECT is_active FROM "user" WHERE email = $1'
        is_active = await pool.fetchval(query, email)
        
        if not is_active:
            raise HTTPException(status_code=403, detail="Your account is not authorized to view the vault (Pending Approval).")

        return email
        
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired. Please sign in again.")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Could not validate credentials.")
