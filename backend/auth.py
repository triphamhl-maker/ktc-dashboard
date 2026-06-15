"""
Google OAuth2 authentication for KTC Dashboard.
Only allows @ghn.vn email addresses.
Uses JWT tokens stored in httpOnly cookies for session management.
"""

import os
import logging
import time
from typing import Optional, Dict

import jwt
import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse

logger = logging.getLogger("auth")

# ── Configuration ──────────────────────────────────────────

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "change-me-in-production")
ALLOWED_DOMAIN = "ghn.vn"
COOKIE_NAME = "ktc_session"
SESSION_MAX_AGE = 8 * 3600  # 8 hours

# Google OAuth2 endpoints
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

# Public paths that don't require authentication
PUBLIC_PATHS = {"/login", "/auth/login", "/auth/callback", "/favicon.ico"}
PUBLIC_PREFIXES = ("/auth/",)

router = APIRouter(prefix="/auth", tags=["auth"])


# ── JWT Helpers ────────────────────────────────────────────

def create_session_token(user_info: Dict) -> str:
    """Create a JWT session token with user info."""
    payload = {
        "sub": user_info["email"],
        "name": user_info.get("name", ""),
        "picture": user_info.get("picture", ""),
        "email": user_info["email"],
        "iat": int(time.time()),
        "exp": int(time.time()) + SESSION_MAX_AGE,
    }
    return jwt.encode(payload, SESSION_SECRET, algorithm="HS256")


def verify_session_token(token: str) -> Optional[Dict]:
    """Verify and decode a JWT session token. Returns user info or None.
    Also re-validates the email domain as defense-in-depth."""
    try:
        payload = jwt.decode(token, SESSION_SECRET, algorithms=["HS256"])
        # Defense-in-depth: re-validate domain on EVERY request
        email = payload.get("email", "").lower().strip()
        if not email.endswith(f"@{ALLOWED_DOMAIN}"):
            logger.warning(f"Session token with non-{ALLOWED_DOMAIN} email rejected: {email}")
            return None
        return payload
    except jwt.ExpiredSignatureError:
        logger.debug("Session token expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.debug(f"Invalid session token: {e}")
        return None


def get_current_user(request: Request) -> Optional[Dict]:
    """Extract current user from request cookies. Returns None if not logged in."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    return verify_session_token(token)


def _get_redirect_uri(request: Request) -> str:
    """Build the OAuth callback URL based on the current request."""
    # Use X-Forwarded-Proto for Render's reverse proxy
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", request.url.netloc)
    return f"{scheme}://{host}/auth/callback"


def is_auth_configured() -> bool:
    """Check if Google OAuth credentials are configured."""
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and SESSION_SECRET != "change-me-in-production")


# ── Auth Routes ────────────────────────────────────────────

@router.get("/login")
async def auth_login(request: Request):
    """Redirect to Google OAuth consent screen."""
    if not is_auth_configured():
        logger.error("Google OAuth not configured. Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, SESSION_SECRET.")
        return JSONResponse(
            status_code=503,
            content={"detail": "Authentication not configured. Contact administrator."},
        )

    redirect_uri = _get_redirect_uri(request)
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "online",
        "prompt": "select_account",
        # Restrict to ghn.vn domain in Google's consent screen
        "hd": ALLOWED_DOMAIN,
    }
    from urllib.parse import urlencode
    auth_url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
    return RedirectResponse(url=auth_url, status_code=302)


@router.get("/callback")
async def auth_callback(request: Request, code: str = "", error: str = ""):
    """Handle Google OAuth callback."""
    if error:
        logger.warning(f"OAuth error: {error}")
        return RedirectResponse(url="/login?error=oauth", status_code=302)

    if not code:
        return RedirectResponse(url="/login?error=no_code", status_code=302)

    redirect_uri = _get_redirect_uri(request)

    try:
        # Exchange authorization code for tokens
        async with httpx.AsyncClient(timeout=15) as client:
            token_resp = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            if token_resp.status_code != 200:
                logger.error(f"Token exchange failed: {token_resp.status_code} — {token_resp.text[:200]}")
                return RedirectResponse(url="/login?error=token_failed", status_code=302)

            token_data = token_resp.json()
            access_token = token_data.get("access_token")

            if not access_token:
                logger.error("No access_token in token response")
                return RedirectResponse(url="/login?error=no_token", status_code=302)

            # Fetch user info
            userinfo_resp = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )

            if userinfo_resp.status_code != 200:
                logger.error(f"Userinfo fetch failed: {userinfo_resp.status_code}")
                return RedirectResponse(url="/login?error=userinfo_failed", status_code=302)

            user_info = userinfo_resp.json()

        email = user_info.get("email", "").lower().strip()
        hd = user_info.get("hd", "").lower().strip()
        logger.info(f"OAuth login attempt: {email} (hd={hd})")

        # Validate hosted domain claim from Google (primary check)
        if hd != ALLOWED_DOMAIN:
            logger.warning(f"Rejected login — hd claim '{hd}' is not {ALLOWED_DOMAIN}: {email}")
            return RedirectResponse(url="/login?error=domain", status_code=302)

        # Validate email domain (defense-in-depth)
        if not email.endswith(f"@{ALLOWED_DOMAIN}"):
            logger.warning(f"Rejected login from non-{ALLOWED_DOMAIN} email: {email}")
            return RedirectResponse(url="/login?error=domain", status_code=302)

        # Verify email is actually verified by Google
        if not user_info.get("verified_email", False):
            logger.warning(f"Rejected unverified email: {email}")
            return RedirectResponse(url="/login?error=unverified", status_code=302)

        # Create session
        session_token = create_session_token(user_info)

        # Set cookie and redirect to dashboard
        response = RedirectResponse(url="/", status_code=302)
        response.set_cookie(
            key=COOKIE_NAME,
            value=session_token,
            max_age=SESSION_MAX_AGE,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )
        logger.info(f"Login successful: {email}")
        return response

    except Exception as e:
        logger.error(f"OAuth callback error: {e}", exc_info=True)
        return RedirectResponse(url="/login?error=server_error", status_code=302)


@router.get("/logout")
async def auth_logout():
    """Clear session cookie and redirect to login."""
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return response


@router.get("/me")
async def auth_me(request: Request):
    """Return current user info (for frontend display)."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {
        "email": user.get("email", ""),
        "name": user.get("name", ""),
        "picture": user.get("picture", ""),
    }
