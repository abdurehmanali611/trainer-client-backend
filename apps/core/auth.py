"""Client JWT issuing & verification for the standalone client backend.

Tokens are HS256-signed with the shared ``JWT_SECRET`` (same value as
trainer/BackEnd/.env) and carry the same claims as rest_framework_simplejwt
tokens (``token_type`` / ``iat`` / ``exp`` / ``jti`` + ``role`` / ``client_id``),
so they stay interchangeable with tokens the trainer backend issues.
"""
from __future__ import annotations

import time
import uuid

import jwt
from django.conf import settings
from rest_framework.exceptions import PermissionDenied

from apps.clients.models import Client
from apps.core.exceptions import BlockedAccessError

ACCESS_LIFETIME_SECONDS = 86400  # 1 day, matches trainer backend JWT policy

# Feature 15: reads stay open for an in-flight session (read-only policy —
# the app renders its "Account on hold" banner), writes are refused.
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def issue_client_jwt(client: Client) -> dict:
    now = int(time.time())
    base = {
        "role": "client",
        "client_id": client.id,
        "iat": now,
        "jti": uuid.uuid4().hex,
    }
    access = jwt.encode(
        {**base, "token_type": "access", "exp": now + ACCESS_LIFETIME_SECONDS},
        settings.JWT_SECRET,
        algorithm="HS256",
    )
    refresh = jwt.encode(
        {**base, "token_type": "refresh", "exp": now + ACCESS_LIFETIME_SECONDS},
        settings.JWT_SECRET,
        algorithm="HS256",
    )
    return {"access": access, "refresh": refresh}


def parse_token_claims(token_str: str) -> dict | None:
    """Decode an *access* token; returns claims or None when invalid."""
    try:
        payload = jwt.decode(
            token_str,
            settings.JWT_SECRET,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
    except jwt.PyJWTError:
        return None
    if payload.get("token_type") != "access":
        return None
    return payload


def require_client(request, *, allow_blocked_writes: bool = False) -> Client:
    """Resolve the calling client from the Bearer access token.

    Feature 15 — in-flight sessions are READ-ONLY after the payment
    deadline: a session issued before the deadline keeps reading (the app
    shows its "Account on hold" banner), but every write is refused with
    403 ``{blocked: true}`` until the trainer approves the payment. Fresh
    logins are blocked outright by ``ClientLoginView``. Only an unpaid
    invoice past the pay window (``blocked_unpaid``) triggers this — a
    missing period config (``blocked_no_period``, e.g. after the trainer
    deleted it) must never lock a client out (features 9–11).

    Pass ``allow_blocked_writes=True`` on the payment-submission and
    credential-change endpoints — paying is exactly what unblocks them.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise PermissionDenied("Missing client token.")
    claims = parse_token_claims(auth[7:])
    if not claims or claims.get("role") != "client":
        raise PermissionDenied("Invalid client token.")
    client = Client.objects.filter(pk=claims.get("client_id"), is_active=True).first()
    if not client:
        raise PermissionDenied("Client not found.")
    if not allow_blocked_writes and request.method not in SAFE_METHODS:
        from apps.core.services import client_access_state

        access = client_access_state(client)
        if access["state"] == "blocked_unpaid":
            raise BlockedAccessError(access["state"])
    return client
