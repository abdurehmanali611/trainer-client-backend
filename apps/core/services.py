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


def evaluate_client(client: Client, today: date | None = None):
    """Run the shared payment-timing rules (payment_engine.py) for a client."""
    from apps.billing.models import Payment

    from . import payment_engine as pe

    today = today or date.today()
    inst = client.payment_instruction
    rule = None
    if inst is not None:
        rule = pe.InstructionRule(
            billing_cycle=inst.billing_cycle,
            cycle_from=inst.cycle_from,
            cycle_to=inst.cycle_to,
            is_prepayment=inst.is_prepayment,
            start_ask_days=inst.start_ask_days or 0,
            grace_days=inst.grace_days or 0,
            amount=float(inst.amount) if inst.amount is not None else None,
        )
    periods = [
        pe.PeriodRow(p.id, p.start_date, p.end_date, float(p.amount or 0), bool(p.is_trial))
        for p in client.periods.all()
    ]
    invoices = [
        pe.InvoiceRow(i.id, i.period_id, i.due_date, i.status, float(i.amount or 0))
        for i in client.invoices.all()
    ]
    pending = set(
        Payment.objects.filter(client=client, status="pending").values_list("invoice_id", flat=True)
    )
    return pe.evaluate(
        today,
        mode=client.mode,
        is_active=client.is_active,
        created_on=client.created_at.date(),
        instruction=rule,
        periods=periods,
        invoices=invoices,
        pending_invoice_ids=pending,
    )


def ensure_due_invoice(client: Client, ev) -> int | None:
    """The invoice the client pays against right now (created on first need).

    A client is asked for one payment at a time. When the ask window opens
    there is no invoice yet, so make exactly one — keyed by the period (a
    post-paid discount) or by the first day of the cycle being paid for, so
    repeated reads never create a second.
    """
    from apps.billing.models import Invoice

    if not ev.required:
        return ev.invoice_id
    if ev.invoice_id:
        return ev.invoice_id
    if ev.pay_period_id:
        existing = Invoice.objects.filter(client=client, period_id=ev.pay_period_id).first()
        if existing:
            return existing.id
        return Invoice.objects.create(
            client=client,
            period_id=ev.pay_period_id,
            amount=ev.amount or 0,
            due_date=ev.cycle_start,
            status="pending",
        ).id
    if ev.pay_start is None or ev.pay_end is None:
        return None
    existing = (
        Invoice.objects.filter(
            client=client,
            period__isnull=True,
            due_date__gte=ev.pay_start,
            due_date__lte=ev.pay_end,
        )
        .exclude(status__in=("paid", "forgiven"))
        .first()
    )
    if existing:
        return existing.id
    return Invoice.objects.create(
        client=client,
        period=None,
        amount=ev.amount or 0,
        due_date=ev.pay_start,
        status="pending",
    ).id


def client_access_state(client: Client) -> dict:
    """
    Decide whether a client can use the app (rules: payment_engine.py).

    Returns one of:
      {state: "allowed", reason: ...}
      {state: "blocked_unpaid", invoice_id, period_id}   — asked, grace over, unpaid
      {state: "blocked_trainer_platform"}                — trainer's Apex fee unpaid (Feature 16)
    """
    if client.mode == "illustrative" or not client.is_active:
        return {"state": "allowed", "reason": "illustrative" if client.mode == "illustrative" else "inactive"}

    # Feature 16: trainer quarterly unpaid → all their clients blocked.
    if trainer_platform_blocked(client):
        return {
            "state": "blocked_trainer_platform",
            "reason": "Your trainer's subscription with the platform is paused. Try again once they restore access.",
        }

    ev = evaluate_client(client)
    if ev.needs_setup:
        return {"state": "allowed", "reason": "needs_setup"}
    if ev.kind == "trial" and not ev.required:
        return {"state": "allowed", "reason": "trial", "period_id": ev.active_period_id}
    # (a prepaid client's first paid month is asked near the END of the trial,
    # so a trial can reach the ask / grace / blocked phases too)
    if ev.blocked:
        out = {"state": "blocked_unpaid", "reason": "unpaid"}
        invoice_id = ensure_due_invoice(client, ev)
        if invoice_id:
            out["invoice_id"] = invoice_id
        if ev.active_period_id:
            out["period_id"] = ev.active_period_id
        return out
    reason = {"ask": "pay_window", "grace": "grace"}.get(ev.phase, "paid" if ev.phase == "covered" else ev.phase)
    out = {"state": "allowed", "reason": reason}
    if ev.required:
        invoice_id = ensure_due_invoice(client, ev)
        if invoice_id:
            out["invoice_id"] = invoice_id
    elif ev.invoice_id:
        out["invoice_id"] = ev.invoice_id
    if ev.active_period_id:
        out["period_id"] = ev.active_period_id
    return out


def payment_instruction_payload(trainer) -> dict:
    return {
        "telebirr_number": trainer.telebirr_number,
        "bank_name": trainer.bank_name,
        "bank_account_name": trainer.bank_account_name,
        "bank_account_number": trainer.bank_account_number,
        "cash_note": trainer.cash_note,
    }


def payment_requirement(client: Client) -> dict:
    """What, if anything, the client must pay right now (rules: payment_engine.py).

    trial period running      -> nothing to pay, nothing asked
    discount period running   -> pay the reduced amount of that period
    otherwise                 -> the payment instruction's cycle, asked from
                                 `start_ask_days` before the due day, kept open
                                 `grace_days` after it, then blocked
    illustration/deactivated  -> never asked

    Kept in parity with the trainer backend's copy (apps/fitness/services.py).
    """
    if client.mode == "illustrative" or not client.is_active:
        return {
            "required": False,
            "amount": None,
            "source": "illustrative" if client.mode == "illustrative" else "inactive",
            "period_id": None,
            "instruction_id": None,
            "phase": "none",
        }
    ev = evaluate_client(client)
    return {
        "required": ev.required,
        "amount": (str(ev.amount) if ev.amount is not None else None)
        if (ev.required or ev.kind in ("paid", "discount"))
        else None,
        "source": ev.source,
        "period_id": ev.active_period_id,
        "instruction_id": client.payment_instruction_id
        if (ev.kind == "paid" or (ev.required and ev.pay_period_id is None))
        else None,
        "days_left": ev.days_left,
        "phase": ev.phase,
        "kind": ev.kind,
        "cycle_start": ev.cycle_start.isoformat() if ev.cycle_start else None,
        "cycle_end": ev.cycle_end.isoformat() if ev.cycle_end else None,
        "due_on": ev.due_on.isoformat() if ev.due_on else None,
        "ask_opens_on": ev.ask_opens_on.isoformat() if ev.ask_opens_on else None,
        "blocked_from": ev.blocked_from.isoformat() if ev.blocked_from else None,
        "grace_ends_on": ev.grace_ends_on.isoformat() if ev.grace_ends_on else None,
        "next_due_on": ev.next_due_on.isoformat() if ev.next_due_on else None,
        "next_ask_opens_on": ev.next_ask_opens_on.isoformat() if ev.next_ask_opens_on else None,
        "awaiting_approval": ev.awaiting_approval,
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
