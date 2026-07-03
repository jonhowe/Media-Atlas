from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from .config import CONFIG

LOGGER = logging.getLogger("media_atlas.security")

PUBLIC_API_PATHS = {
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/me",
    "/api/health",
    "/api/health/live",
    "/api/health/ready",
}


def auth_status(request: Request) -> dict[str, Any]:
    user = authenticated_user(request)
    return {
        "mode": CONFIG.auth.mode,
        "authenticated": bool(user),
        "username": user,
        "configured": auth_configured(),
        "trusted_user_header": CONFIG.auth.trusted_user_header
        if CONFIG.auth.mode == "reverse_proxy_trusted"
        else None,
    }


def auth_configured() -> bool:
    if CONFIG.auth.mode == "disabled":
        return True
    if CONFIG.auth.mode == "reverse_proxy_trusted":
        return bool(CONFIG.auth.trusted_user_header)
    return bool(CONFIG.auth.admin_password or CONFIG.auth.admin_password_hash)


def authenticated_user(request: Request) -> str | None:
    if CONFIG.auth.mode == "disabled":
        return "local"
    if CONFIG.auth.mode == "reverse_proxy_trusted":
        header = CONFIG.auth.trusted_user_header
        return request.headers.get(header) or request.headers.get(header.lower())
    cookie = request.cookies.get(CONFIG.auth.session_cookie_name)
    if not cookie:
        return None
    return _verify_session(cookie)


def require_auth_response(request: Request) -> Response | None:
    if not request.url.path.startswith("/api/"):
        return None
    if request.url.path in PUBLIC_API_PATHS:
        return None
    if authenticated_user(request):
        return None
    return JSONResponse({"detail": "Authentication required."}, status_code=401)


def login(username: str, password: str) -> str:
    if CONFIG.auth.mode != "single_admin":
        raise ValueError("Password login is only available in single_admin auth mode.")
    if not auth_configured():
        raise ValueError("Admin auth is enabled but no admin password or password hash is configured.")
    if username != CONFIG.auth.admin_username:
        raise ValueError("Invalid username or password.")
    if not _password_matches(password):
        raise ValueError("Invalid username or password.")
    return _sign_session(username)


def set_session_cookie(response: Response, session: str) -> None:
    response.set_cookie(
        CONFIG.auth.session_cookie_name,
        session,
        httponly=True,
        secure=CONFIG.auth.cookie_secure,
        samesite="lax",
        max_age=CONFIG.auth.session_ttl_seconds,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(CONFIG.auth.session_cookie_name)


def security_headers(response: Response) -> None:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'",
    )


def redacted_config() -> dict[str, Any]:
    return {
        "mode": CONFIG.auth.mode,
        "admin_username": CONFIG.auth.admin_username if CONFIG.auth.mode == "single_admin" else None,
        "admin_password_configured": bool(CONFIG.auth.admin_password or CONFIG.auth.admin_password_hash),
        "session_secret_configured": bool(CONFIG.auth.session_secret),
        "session_cookie_name": CONFIG.auth.session_cookie_name,
        "session_ttl_seconds": CONFIG.auth.session_ttl_seconds,
        "cookie_secure": CONFIG.auth.cookie_secure,
        "trusted_user_header": CONFIG.auth.trusted_user_header
        if CONFIG.auth.mode == "reverse_proxy_trusted"
        else None,
    }


def auth_warnings() -> list[str]:
    warnings: list[str] = []
    if CONFIG.auth.mode == "single_admin":
        if not CONFIG.auth.admin_password and not CONFIG.auth.admin_password_hash:
            warnings.append("single_admin auth is enabled but no admin password is configured.")
        if not CONFIG.auth.session_secret:
            warnings.append(
                "MEDIA_ATLAS_SESSION_SECRET is not set; sessions use an ephemeral secret and will reset on restart."
            )
    return warnings


def _session_secret() -> str:
    if CONFIG.auth.session_secret:
        return CONFIG.auth.session_secret
    secret = getattr(_session_secret, "_generated", None)
    if not secret:
        secret = secrets.token_urlsafe(32)
        setattr(_session_secret, "_generated", secret)
        LOGGER.warning("Using an ephemeral session secret because MEDIA_ATLAS_SESSION_SECRET is not set.")
    return secret


def _sign_session(username: str) -> str:
    now = int(time.time())
    payload = {"sub": username, "iat": now, "exp": now + CONFIG.auth.session_ttl_seconds}
    encoded = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(_session_secret().encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded}.{_b64(signature)}"


def _verify_session(value: str) -> str | None:
    try:
        encoded, supplied_signature = value.split(".", 1)
    except ValueError:
        return None
    expected = _b64(hmac.new(_session_secret().encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, supplied_signature):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(_pad_b64(encoded)).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(payload.get("exp") or 0) < int(time.time()):
        return None
    return str(payload.get("sub") or "") or None


def _password_matches(password: str) -> bool:
    if CONFIG.auth.admin_password_hash:
        return _hash_matches(password, CONFIG.auth.admin_password_hash)
    return hmac.compare_digest(password, CONFIG.auth.admin_password)


def _hash_matches(password: str, stored: str) -> bool:
    parts = stored.split("$")
    if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
        return False
    _, iterations_text, salt, expected = parts
    try:
        iterations = int(iterations_text)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return hmac.compare_digest(_b64(digest), expected)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _pad_b64(value: str) -> bytes:
    return (value + "=" * (-len(value) % 4)).encode("ascii")
