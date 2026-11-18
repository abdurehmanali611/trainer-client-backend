"""Domain logic shared by the client-facing endpoints (ported from the
trainer backend's apps/fitness/services.py — client-relevant subset)."""
from __future__ import annotations

from datetime import date

from apps.billing.models import Invoice
from apps.clients.models import Client, Notification, PushToken, TrainerBilling

from .utils import send_expo_push


def trainer_platform_blocked(client: Client) -> bool:
    """Feature 16: when the trainer's quarterly (or setup) apex fee is past due,
    every client of that trainer is blocked with a message — except when the
    trainer is exempt/illustrative (Feature 32). Setup-fee block for the
    trainer alone does NOT block clients (Feature 17)."""
    billing = TrainerBilling.objects.filter(trainer_id=client.trainer_id).first()
    if billing is None:
        return False
    if billing.is_illustration or billing.account_status == "exempt":
        return False
    if billing.billing_hold:
        return True
    # Only quarterly expiry blocks clients — unpaid setup fee alone does not.
    if not billing.setup_fee_paid:
        return False
    today = date.today()
    if billing.paid_until and today > billing.paid_until:
        return True
    return False


def client_access_state(client: Client) -> dict:
    """
    Decide whether a client can use the app.

    Returns one of:
      {state: "allowed", ...}
      {state: "blocked_unpaid", invoice: ...}   — expired period with unpaid invoice
      {state: "blocked_no_period"}              — no period covers today (contact trainer)
      {state: "blocked_trial_expired", ...}     — trial over, no paid period
      {state: "blocked_trainer_platform"}       — trainer apex quarterly unpaid (Feature 16)
    """
    from datetime import datetime  # noqa: F401  (kept for parity with trainer backend)

    today = date.today()

    if client.mode == "illustrative" or not client.is_active:
        return {"state": "allowed", "reason": "illustrative" if client.mode == "illustrative" else "inactive"}

    # Feature 16: trainer quarterly unpaid → all their clients blocked.
    if trainer_platform_blocked(client):
        return {
            "state": "blocked_trainer_platform",
            "reason": "Your trainer's subscription with the platform is paused. Try again once they restore access.",
        }

    period = (
        client.periods.filter(start_date__lte=today, end_date__gte=today)
        .order_by("-end_date")
        .first()
    )
    if period:
        if period.is_trial:
            return {"state": "allowed", "reason": "trial", "period_id": period.id}
        invoice = period.invoices.order_by("-created_at").first()
        if invoice is None:
            return {"state": "allowed", "reason": "no_invoice", "period_id": period.id}
        if invoice.status in ("paid", "forgiven"):
            return {"state": "allowed", "reason": "paid", "period_id": period.id}
        # Still inside the pay window (start..end) — allowed while they pay
        if today <= period.end_date:
            return {
                "state": "allowed",
                "reason": "pay_window",
                "period_id": period.id,
                "invoice_id": invoice.id,
            }
        return {
            "state": "blocked_unpaid",
            "invoice_id": invoice.id,
            "period_id": period.id,
        }

    # Grace window — the payment instruction can keep the app open for a few
    # days after the final day of the subscription before blocking.
    grace = 0
    if client.payment_instruction_id:
        grace = client.payment_instruction.grace_days or 0
    last_period = client.periods.order_by("-end_date").first()
    if grace and last_period:
        from datetime import timedelta

        if today <= last_period.end_date + timedelta(days=grace):
            return {
                "state": "allowed",
                "reason": "grace",
                "period_id": last_period.id,
            }

    # No period covers today
    unpaid = (
        client.invoices.filter(status__in=("pending", "overdue")).order_by("-due_date").first()
    )
    if unpaid:
        return {"state": "blocked_unpaid", "invoice_id": unpaid.id}
    return {"state": "blocked_no_period"}


def payment_instruction_payload(trainer) -> dict:
    return {
        "telebirr_number": trainer.telebirr_number,
        "bank_name": trainer.bank_name,
        "bank_account_name": trainer.bank_account_name,
        "bank_account_number": trainer.bank_account_number,
        "cash_note": trainer.cash_note,
    }


def payment_requirement(client: Client) -> dict:
    """Feature 10: what, if anything, the client must pay RIGHT NOW.

    Resolves from the ACTIVE period config (the same row edit/delete operate
    on), evaluated on every read — so a config edit or delete changes the
    answer immediately, with no cache and no re-login:

      trial period (config ON) -> nothing to pay during that period
      paid config (discount)   -> pay the configured amount
      no config for today      -> the creation-time payment instruction
                                   (Client.payment_instruction, the default)

    Feature 11: the fallback to the instruction happens the moment the
    period's end date passes — the boundary is re-evaluated on every read
    (no cron, no scheduled job, so there is no gap where the old config
    still applies). `days_left` exposes that boundary for the warning state.

    Returns {required, amount, source, period_id, instruction_id, days_left}.
    """
    today = date.today()

    # Illustrative / deactivated clients are never asked for money.
    if client.mode == "illustrative" or not client.is_active:
        return {
            "required": False,
            "amount": None,
            "source": "illustrative",
            "period_id": None,
            "instruction_id": None,
        }

    period = (
        client.periods.filter(start_date__lte=today, end_date__gte=today)
        .order_by("-end_date")
        .first()
    )

    if period:
        # Days until the config's final day (0 on the last day itself). This is
        # the boundary the warning state flips on — feature 13 renders it.
        days_left = (period.end_date - today).days
        # Trial ON -> the client is not asked for payment in this period.
        if period.is_trial:
            return {
                "required": False,
                "amount": None,
                "source": "trial",
                "period_id": period.id,
                "instruction_id": None,
                "days_left": days_left,
            }
        invoice = period.invoices.order_by("-created_at").first()
        if invoice is not None and invoice.status in ("paid", "forgiven"):
            # Already settled for this period.
            return {
                "required": False,
                "amount": None,
                "source": "paid",
                "period_id": period.id,
                "instruction_id": None,
                "days_left": days_left,
            }
        # Paid config -> the configured amount is the ask (discount included).
        return {
            "required": True,
            "amount": str(period.amount),
            "source": "period",
            "period_id": period.id,
            "instruction_id": None,
            "days_left": days_left,
        }

    # Feature 11: no config covers today (the period's end date has passed)
    # -> the creation-time instruction takes over automatically, on read.
    # There is no cron and no scheduled job: the boundary is evaluated every
    # time this runs, so no gap is possible.
    instruction = client.payment_instruction
    if instruction is None:
        return {
            "required": False,
            "amount": None,
            "source": "no_instruction",
            "period_id": None,
            "instruction_id": None,
            "days_left": None,
        }
    return {
        "required": True,
        "amount": str(instruction.amount) if instruction.amount is not None else None,
        "source": "instruction",
        "period_id": None,
        "instruction_id": instruction.id,
        "days_left": None,
    }


def notify_trainer(trainer, title: str, body: str, ntype: str = "alert", data: dict | None = None):
    Notification.objects.create(
        target="trainer", user_id=trainer.id, title=title, body=body,
        type=ntype, data=data or {},
    )
    tokens = list(
        PushToken.objects.filter(user_type="trainer", user_id=trainer.id).values_list("token", flat=True)
    )
    send_expo_push(tokens, title, body, {"type": ntype, **(data or {})})


def client_payment_payload(client: Client) -> dict:
    """Access + billing snapshot used by the client app lock screen & billing tab."""
    access = client_access_state(client)
    trainer = client.trainer
    invoice_data = None
    if access.get("invoice_id"):
        invoice = Invoice.objects.filter(id=access["invoice_id"]).first()
    elif access.get("state") == "blocked_unpaid":
        invoice = client.invoices.filter(status__in=("pending", "overdue")).first()
    else:
        invoice = None
    if invoice:
        period = invoice.period
        invoice_data = {
            "id": invoice.id,
            "amount": str(invoice.amount),
            "status": invoice.status,
            "due_date": invoice.due_date.isoformat(),
            "period": (
                {
                    "id": period.id,
                    "start_date": period.start_date.isoformat(),
                    "end_date": period.end_date.isoformat(),
                }
                if period
                else None
            ),
        }
    active_period = (
        client.periods.filter(start_date__lte=date.today(), end_date__gte=date.today())
        .order_by("-end_date")
        .first()
    )
    return {
        "access": access,
        "invoice": invoice_data,
        # Feature 10: the payment-requirement resolver (trial => none,
        # paid config => configured amount, no config => default instruction).
        "payment_requirement": payment_requirement(client),
        "active_period": (
            {
                "id": active_period.id,
                "start_date": active_period.start_date.isoformat(),
                "end_date": active_period.end_date.isoformat(),
                "is_trial": active_period.is_trial,
                "amount": str(active_period.amount),
                "package_name": active_period.package.name if active_period.package else None,
            }
            if active_period
            else None
        ),
        "payment_instructions": payment_instruction_payload(trainer),
        "mode": client.mode,
    }
