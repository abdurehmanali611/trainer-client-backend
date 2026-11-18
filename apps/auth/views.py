"""Client auth endpoints served directly by the standalone backend
(ported from the trainer backend's apps/internal/views_auth.py — the
X-Service-Token proxy header is gone; this *is* the client-facing API)."""
import re
import time
import uuid

import jwt
from django.conf import settings
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.models import Payment
from apps.clients.models import Client
from apps.core.auth import ACCESS_LIFETIME_SECONDS, issue_client_jwt, require_client
from apps.core.services import (
    client_access_state,
    client_payment_payload,
    notify_trainer,
)
from apps.core.utils import make_reset_token, read_reset_token, send_reset_email


class ClientLoginView(APIView):
    """POST {credential} (or {phone, credential}) — credential is the OTP
    (first login) or the password they set. Returns JWT unless blocked by
    subscription gate.

    The login screen is single-field: the OTP is globally unique across all
    trainers' clients, so it identifies the client by itself. After a
    credential change the client types their own credential (must match
    exactly one account). When a phone is supplied it identifies the client
    directly (legacy contract with the trainer backend).
    """

    authentication_classes = []
    permission_classes = []

    def post(self, request):
        phone = (request.data.get("phone") or "").strip()
        credential = (request.data.get("credential") or "").strip()
        if not credential:
            return Response({"detail": "credential is required."}, status=400)

        if phone:
            client = Client.objects.filter(phone=phone, is_active=True).first()
            if not client or client.effective_credential != credential:
                return Response({"detail": "Invalid phone or credential."}, status=401)
        else:
            # Single-field login: OTP first (globally unique), then their own
            # credential — only when it identifies exactly one account.
            client = Client.objects.filter(is_active=True, otp=credential).first()
            if client is None or client.effective_credential != credential:
                matches = list(
                    Client.objects.filter(is_active=True, credential=credential)[:2]
                )
                client = matches[0] if len(matches) == 1 else None
            if client is None:
                return Response({"detail": "Invalid credential."}, status=401)

        access = client_access_state(client)
        billing = client_payment_payload(client)
        # Feature 15: the payment deadline is the pay window (period end +
        # grace). Only an unpaid invoice past that window blocks login and
        # redirects to the payment portal — `blocked_no_period` (no active
        # config, e.g. the trainer just deleted it; features 9–11 revert to
        # the instruction) must NOT false-block: the client keeps access.
        if access["state"] == "blocked_unpaid":
            return Response(
                {
                    "blocked": True,
                    "reason": access["state"],
                    "detail": (
                        "Your subscription payment is past its deadline. "
                        "Submit your payment to unlock your account."
                    ),
                    # The lock screen submits the payment with this phone —
                    # a blocked login has no JWT to identify the client.
                    "phone": client.phone,
                    "billing": billing,
                },
                status=403,
            )
        # Feature 16: trainer quarterly unpaid — clients see a block message
        # (not the client payment portal; they cannot pay the trainer's apex fee).
        if access["state"] == "blocked_trainer_platform":
            return Response(
                {
                    "blocked": True,
                    "reason": access["state"],
                    "detail": access.get(
                        "reason",
                        "Your trainer's platform subscription is paused.",
                    ),
                    "phone": client.phone,
                    "billing": billing,
                },
                status=403,
            )
        tokens = issue_client_jwt(client)
        # Feature 4: remember that this client has logged in at least once —
        # the trainer's client-details UI swaps the OTP for a Reset button.
        if client.first_login_at is None:
            client.first_login_at = timezone.now()
            client.save(update_fields=["first_login_at"])
        return Response(
            {
                "blocked": False,
                "must_change_credential": client.must_change_credential,
                "client": {
                    "id": client.id,
                    "name": client.name,
                    "phone": client.phone,
                    "email": client.email,
                    "mode": client.mode,
                    "avatar_url": client.avatar_url,
                },
                **tokens,
            }
        )


class ClientAccessStatusView(APIView):
    """Lock screen: {phone} -> blocked + billing payload (no JWT needed)."""

    authentication_classes = []
    permission_classes = []

    def post(self, request):
        phone = (request.data.get("phone") or "").strip()
        client = Client.objects.filter(phone=phone, is_active=True).first()
        if not client:
            return Response({"found": False})
        access = client_access_state(client)
        billing = client_payment_payload(client)
        # Feature 15: "blocked" means the payment deadline passed with money
        # owed (same rule as the login gate) — a missing period config is a
        # soft notice, never a lock.
        if access["state"] == "blocked_unpaid":
            return Response({"found": True, "blocked": True, "billing": billing})
        return Response({"found": True, "blocked": False, "billing": billing})


class ClientLockPaymentSubmitView(APIView):
    """Blocked client submits a transaction id without logging in."""

    authentication_classes = []
    permission_classes = []

    def post(self, request):
        phone = (request.data.get("phone") or "").strip()
        client = Client.objects.filter(phone=phone, is_active=True).first()
        if not client:
            return Response({"detail": "No account for this phone."}, status=404)
        transaction_id = (request.data.get("transaction_id") or "").strip()
        channel = request.data.get("channel", "telebirr")
        if not transaction_id:
            return Response({"detail": "transaction_id is required."}, status=400)
        billing = client_payment_payload(client)
        invoice_id = (billing.get("invoice") or {}).get("id")
        if not invoice_id:
            return Response(
                {"detail": "No pending invoice. Please contact your trainer."},
                status=400,
            )
        payment = Payment.objects.create(
            invoice_id=invoice_id,
            client=client,
            transaction_id=transaction_id,
            channel=channel if channel in ("telebirr", "cbe", "others") else "telebirr",
        )
        notify_trainer(
            client.trainer,
            "Payment submitted",
            f"{client.name} submitted {payment.transaction_id} ({payment.channel}) — awaiting approval.",
            "payment",
            {"payment_id": payment.id, "client_id": client.id},
        )
        return Response(
            {"detail": "Payment submitted. Your trainer will review it.", "payment_id": payment.id}
        )


class ClientChangeCredentialView(APIView):
    """Feature 6: forced credential change on first OTP login.

    Three-field contract: ``old_credential`` (the OTP they just logged in
    with), ``new_credential`` and ``confirm_credential``. Validates the old
    value against the current credential (the OTP while it is still active),
    requires new == confirm and a non-weak new value, then stores it as the
    login credential and clears ``must_change_credential``.
    """

    authentication_classes = []
    permission_classes = []

    def post(self, request):
        # Feature 15: credential hygiene is never payment-gated — the forced
        # first-login change must work even if the deadline passes mid-flow.
        client = require_client(request, allow_blocked_writes=True)
        old = (request.data.get("old_credential") or "").strip()
        new = (request.data.get("new_credential") or "").strip()
        confirm = (request.data.get("confirm_credential") or "").strip()

        if not old or not new or not confirm:
            return Response(
                {
                    "detail": (
                        "old_credential, new_credential and confirm_credential "
                        "are required."
                    )
                },
                status=400,
            )
        if client.effective_credential != old:
            return Response({"detail": "Old OTP is incorrect."}, status=400)
        if new != confirm:
            return Response(
                {"detail": "New OTP and confirmation do not match."}, status=400
            )
        if (
            len(new) < 6
            or not re.search(r"[A-Za-z]", new)
            or not re.search(r"[0-9]", new)
        ):
            return Response(
                {
                    "detail": (
                        "New OTP must be at least 6 characters and include "
                        "both letters and digits."
                    )
                },
                status=400,
            )
        client.credential = new
        client.must_change_credential = False
        client.save(update_fields=["credential", "must_change_credential"])
        return Response({"detail": "Credential updated."})


class ClientResetRequestView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        email = (request.data.get("email") or "").strip()
        client = Client.objects.filter(email__iexact=email, is_active=True).first()
        if client:
            token = make_reset_token(client.id, "client")
            reset_url = f"{settings.CLIENT_APP_RESET_URL}?token={token}"
            send_reset_email(client.email, client.name, reset_url)
        return Response({"detail": "If that email exists, a reset link was sent."})


class ClientResetConfirmView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        parsed = read_reset_token(request.data.get("token", ""))
        if not parsed or parsed[0] != "client":
            return Response({"detail": "Invalid or expired reset link."}, status=400)
        client = Client.objects.filter(id=parsed[1], is_active=True).first()
        if not client:
            return Response({"detail": "Invalid or expired reset link."}, status=400)
        new_cred = (request.data.get("new_credential") or "").strip()
        if len(new_cred) < 4:
            return Response(
                {"detail": "Credential must be at least 4 characters."}, status=400
            )
        client.credential = new_cred
        client.must_change_credential = False
        client.save(update_fields=["credential", "must_change_credential"])
        return Response({"detail": "Credential updated. You can now log in."})


class TokenRefreshView(APIView):
    """Refresh a client access token using the shared JWT_SECRET."""

    authentication_classes = []
    permission_classes = []

    def post(self, request):
        refresh = request.data.get("refresh") or ""
        if not refresh:
            return Response({"detail": "Field 'refresh' is required."}, status=400)
        try:
            claims = jwt.decode(
                refresh,
                settings.JWT_SECRET,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
        except jwt.PyJWTError:
            return Response({"detail": "Token is invalid or expired."}, status=401)
        if claims.get("token_type") != "refresh" or claims.get("role") != "client":
            return Response({"detail": "Token is invalid or expired."}, status=401)
        # PyJWT verified exp already; re-issue a fresh access token.
        client = Client.objects.filter(
            pk=claims.get("client_id"), is_active=True
        ).first()
        if not client:
            return Response({"detail": "Token is invalid or expired."}, status=401)
        # Feature 15: a blocked session must not extend itself — refresh is
        # refused so the read-only session ends and the next login is
        # evaluated fresh (landing on the payment portal).
        access_state = client_access_state(client)
        if access_state["state"] == "blocked_unpaid":
            return Response(
                {
                    "blocked": True,
                    "reason": access_state["state"],
                    "detail": (
                        "Payment required. Your subscription payment is "
                        "pending approval."
                    ),
                },
                status=403,
            )
        now = int(time.time())
        access = jwt.encode(
            {
                "role": "client",
                "client_id": client.id,
                "token_type": "access",
                "iat": now,
                "exp": now + ACCESS_LIFETIME_SECONDS,
                "jti": uuid.uuid4().hex,
            },
            settings.JWT_SECRET,
            algorithm="HS256",
        )
        return Response({"access": access})
