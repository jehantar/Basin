"""Strava OAuth routes — one-time authorization flow."""

import os
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from shared.db import get_session

router = APIRouter()

STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/api/v3/oauth/token"


@router.get("/strava/auth")
def strava_auth():
    """Redirect to Strava's OAuth authorization page."""
    client_id = os.environ.get("STRAVA_CLIENT_ID")
    redirect_uri = os.environ.get("STRAVA_REDIRECT_URI", "")
    if not client_id or not redirect_uri:
        raise HTTPException(500, "STRAVA_CLIENT_ID and STRAVA_REDIRECT_URI must be set")

    params = urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "activity:read_all",
        "approval_prompt": "auto",
    })
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"{STRAVA_AUTH_URL}?{params}", status_code=307)


@router.get("/strava/callback")
def strava_callback(code: str, scope: str = ""):
    """Exchange authorization code for tokens and store in DB."""
    client_id = os.environ.get("STRAVA_CLIENT_ID")
    client_secret = os.environ.get("STRAVA_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise HTTPException(500, "STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET must be set")

    resp = httpx.post(STRAVA_TOKEN_URL, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
    }, timeout=15)

    if resp.status_code != 200:
        raise HTTPException(502, f"Strava token exchange failed: {resp.text}")

    data = resp.json()
    access_token = data["access_token"]
    refresh_token = data["refresh_token"]
    expires_at = data["expires_at"]  # Unix timestamp

    with get_session() as session:
        session.execute(text("""
            INSERT INTO strava.tokens (id, access_token, refresh_token, expires_at, updated_at)
            VALUES (1, :access, :refresh, to_timestamp(:expires), now())
            ON CONFLICT (id) DO UPDATE SET
                access_token = :access,
                refresh_token = :refresh,
                expires_at = to_timestamp(:expires),
                updated_at = now()
        """), {"access": access_token, "refresh": refresh_token, "expires": expires_at})

    athlete = data.get("athlete", {})
    name = athlete.get("firstname", "User")
    return HTMLResponse(f"""
        <h2>Strava Connected</h2>
        <p>Authorized as <strong>{name}</strong>. Tokens stored.</p>
        <p>You can close this tab and run the collector.</p>
    """)
