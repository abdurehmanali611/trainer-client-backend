"""Client payment timing — one pure, testable rule set.

THIS FILE IS DUPLICATED, byte for byte, in the client backend
(`trainer-client/BackEnd/apps/core/payment_engine.py`). The two services can't
share a process, so they share this source instead. Edit both together — the
test-suite in each repo runs the same scenarios.

It has no Django imports on purpose: every rule below is a plain function of
dates and numbers, so it can be unit-tested without a database.

THE MODEL
---------
A client's life is a sequence of SEGMENTS on a calendar:

    free trial   — a period with is_trial        (nothing was paid)
    discount     — a non-trial period, amount>0   (the reduced price)
    cycle        — one billing cycle of the payment instruction
                   (monthly / quarterly / half-yearly / yearly / custom range)

The rules below are written once and hold for EVERY cycle length.

Illustration clients never pay, are never asked and are never blocked.

Every payment has a DEADLINE DAY — the day "0 days remain":

    post-paid  -> the last day of the segment being paid for
    prepaid    -> the last day of the segment BEFORE the one being paid for
                  (the day the new one is about to begin)

`start_ask_days`  the request (warning + payment portal) opens this many days
                  BEFORE the deadline ("5 days remaining" = 5). It never opens
                  before the segment it belongs to exists.
`grace_days`      the app stays open this many extra days. The client is
                  BLOCKED from `deadline + grace_days` until a payment is
                  approved (with grace 0: on the deadline day itself).

WHAT IS ALREADY PAID (prepaid only)
-----------------------------------
* A brand-new client's first cycle: assumed paid (that is what prepaid means).
* A discount: the client already paid the discounted price — counted as paid.
* A free trial: nothing was paid, so the NEXT payment (the first paid cycle) is
  asked near the end of the trial, by the same ask / grace rules.
* After a trial or discount ends, cycles start again from scratch the next day.
  The payment for that first cycle is the one asked near the end of the trial
  or discount (its deadline = the last day of that trial/discount).

POST-PAID
---------
* A free trial asks nothing; the cycle that follows is paid at ITS end.
* A discount is paid once, at the discounted price, at ITS end.

A payment is *covered* once an invoice for it is paid or forgiven.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta

CYCLE_MONTHS = {"monthly": 1, "quarterly": 3, "half_yearly": 6, "yearly": 12}

# How many cycles back/forward from "today's cycle" are examined. Older unpaid
# cycles are history, not a reason to lock someone out today.
LOOK_BACK = 1
LOOK_AHEAD = 1

ONE_DAY = timedelta(days=1)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InstructionRule:
    billing_cycle: str = "monthly"
    cycle_from: date | None = None
    cycle_to: date | None = None
    is_prepayment: bool = True
    start_ask_days: int = 0
    grace_days: int = 0
    amount: float | None = None


@dataclass(frozen=True)
class PeriodRow:
    id: int
    start_date: date
    end_date: date
    amount: float
    is_trial: bool


@dataclass(frozen=True)
class InvoiceRow:
    id: int
    period_id: int | None
    due_date: date
    status: str  # pending | paid | overdue | forgiven
    amount: float = 0.0


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


@dataclass
class Evaluation:
    # illustration | trial | discount | paid | none (no payment set-up yet)
    kind: str
    # none | covered | upcoming | ask | grace | blocked
    phase: str = "none"
    active_period_id: int | None = None
    # The amount being asked (or, when nothing is asked, the relevant price).
    amount: float | None = None
    # The trial / discount / cycle the client is in right now.
    cycle_start: date | None = None
    cycle_end: date | None = None
    # due_on = the deadline day ("0 days remain"); blocked_from = deadline +
    # grace; grace_ends_on = the last day the app is still open.
    due_on: date | None = None
    ask_opens_on: date | None = None
    blocked_from: date | None = None
    grace_ends_on: date | None = None
    days_left: int | None = None  # to the end of the trial/discount/cycle
    invoice_id: int | None = None  # existing unpaid invoice for the due payment
    # What the requested payment is FOR (so an invoice can be created for it):
    # a period-linked invoice (post-paid discount) or a plain cycle invoice.
    pay_period_id: int | None = None
    pay_start: date | None = None
    pay_end: date | None = None
    # When this payment is covered: when the NEXT one is due / asked.
    next_due_on: date | None = None
    next_ask_opens_on: date | None = None
    awaiting_approval: bool = False  # a payment was submitted, not yet decided
    needs_setup: bool = False  # not illustration, but no payment instruction
    source: str = "none"  # trial | period | instruction | illustration | ...
    timeline: dict = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return self.phase == "blocked"

    @property
    def required(self) -> bool:
        """True while the client should be shown the payment portal."""
        return self.phase in ("ask", "grace", "blocked")


# ---------------------------------------------------------------------------
# Date maths
# ---------------------------------------------------------------------------


def add_months(d: date, months: int) -> date:
    """`d` + N calendar months, clamping the day (31 Jan + 1 month = 28/29 Feb)."""
    index = d.month - 1 + months
    year = d.year + index // 12
    month = index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def cycle_bounds(anchor: date, rule: InstructionRule, k: int) -> tuple[date, date]:
    """First and last day (inclusive) of cycle number `k` (0-based)."""
    if rule.billing_cycle == "custom" and rule.cycle_from and rule.cycle_to:
        length = max(1, (rule.cycle_to - rule.cycle_from).days + 1)
        start = anchor + timedelta(days=k * length)
        return start, start + timedelta(days=length - 1)
    months = CYCLE_MONTHS.get(rule.billing_cycle, 1)
    start = add_months(anchor, k * months)
    end = add_months(anchor, (k + 1) * months) - ONE_DAY
    return start, end


def cycle_index(anchor: date, rule: InstructionRule, today: date) -> int:
    """Index of the cycle that contains `today` (anchor <= today)."""
    k = 0
    # Cycles are at most a year long, so this loop is short; it is also exact
    # for month arithmetic (no approximation).
    while True:
        _, end = cycle_bounds(anchor, rule, k)
        if today <= end:
            return k
        k += 1
        if k > 5000:  # pathological safety net
            return k


def phase_for(
    today: date,
    *,
    deadline: date,
    ask_days: int,
    grace_days: int,
    floor: date | None,
    covered: bool,
) -> tuple[str, date, date, date]:
    """(phase, deadline, ask_opens_on, blocked_from) for one payment.

    covered  : paid / forgiven (or assumed paid)
    upcoming : the request has not opened yet
    ask      : request open, deadline not reached (days remaining > 0)
    grace    : deadline reached, still inside the grace days (app open)
    blocked  : deadline + grace reached and still unpaid
    """
    ask_open = deadline - timedelta(days=max(0, ask_days))
    if floor is not None and ask_open < floor:
        ask_open = floor
    blocked_from = deadline + timedelta(days=max(0, grace_days))
    if covered:
        return "covered", deadline, ask_open, blocked_from
    if today >= blocked_from:
        return "blocked", deadline, ask_open, blocked_from
    if today >= deadline:
        return "grace", deadline, ask_open, blocked_from
    if today >= ask_open:
        return "ask", deadline, ask_open, blocked_from
    return "upcoming", deadline, ask_open, blocked_from


# ---------------------------------------------------------------------------
# The evaluation
# ---------------------------------------------------------------------------

_PAID = ("paid", "forgiven")


def _is_priced(p: PeriodRow) -> bool:
    return p.is_trial or p.amount > 0


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def evaluate(
    today: date,
    *,
    mode: str,
    is_active: bool,
    created_on: date,
    instruction: InstructionRule | None,
    periods: list[PeriodRow],
    invoices: list[InvoiceRow],
    pending_invoice_ids: set[int] | frozenset[int] = frozenset(),
) -> Evaluation:
    # 1. Illustration clients never pay.
    if mode == "illustrative":
        return Evaluation(kind="illustration", source="illustration")

    # A non-trial period with no amount is legacy noise and is ignored.
    priced = [p for p in periods if _is_priced(p)]
    running = [p for p in priced if p.start_date <= today <= p.end_date]
    active = max(running, key=lambda p: p.end_date) if running else None

    rule = instruction or InstructionRule()
    plain = [i for i in invoices if i.period_id is None]

    def next_cycle_payment(segment: PeriodRow):
        """PREPAID: the first paid cycle after a trial/discount is asked near the
        END of that trial/discount (its last day is the deadline). Returns None
        when another period already continues straight on from it."""
        for other in priced:
            if other.id != segment.id and other.start_date <= segment.end_date + ONE_DAY \
                    and other.end_date > segment.end_date:
                return None
        s0, e0 = cycle_bounds(segment.end_date + ONE_DAY, rule, 0)
        in_cycle = [i for i in plain if s0 <= i.due_date <= e0]
        covered = any(i.status in _PAID for i in in_cycle)
        open_inv = next((i for i in in_cycle if i.status not in _PAID), None)
        phase, due, ask_open, blocked_from = phase_for(
            today,
            deadline=segment.end_date,
            ask_days=rule.start_ask_days,
            grace_days=rule.grace_days,
            floor=segment.start_date,
            covered=covered,
        )
        return {
            "phase": phase, "due": due, "ask_open": ask_open, "blocked_from": blocked_from,
            "invoice": open_inv, "pay_start": s0, "pay_end": e0, "covered": covered,
        }

    # 2. A free trial running today.
    if active is not None and active.is_trial:
        ev = Evaluation(
            kind="trial",
            phase="none",
            source="trial",
            active_period_id=active.id,
            cycle_start=active.start_date,
            cycle_end=active.end_date,
            days_left=(active.end_date - today).days,
        )
        if instruction is not None and rule.is_prepayment:
            nxt = next_cycle_payment(active)
            if nxt is not None:
                ev.due_on, ev.ask_opens_on = nxt["due"], nxt["ask_open"]
                ev.blocked_from = nxt["blocked_from"]
                ev.grace_ends_on = nxt["blocked_from"] - ONE_DAY
                if nxt["phase"] in ("ask", "grace", "blocked"):
                    # Nothing was paid during the trial, so the first paid cycle
                    # is requested near the trial's end, by the normal rules.
                    ev.phase = nxt["phase"]
                    ev.amount = rule.amount
                    ev.invoice_id = nxt["invoice"].id if nxt["invoice"] else None
                    ev.awaiting_approval = bool(nxt["invoice"] and nxt["invoice"].id in pending_invoice_ids)
                    ev.pay_start, ev.pay_end = nxt["pay_start"], nxt["pay_end"]
                elif nxt["covered"]:
                    ev.phase = "covered"
        ev.timeline = _timeline(ev)
        return ev

    # 3. A discount running today.
    if active is not None:
        ev = Evaluation(
            kind="discount",
            source="period",
            active_period_id=active.id,
            amount=active.amount,
            cycle_start=active.start_date,
            cycle_end=active.end_date,
            days_left=(active.end_date - today).days,
        )
        if rule.is_prepayment:
            # Prepaid: the discounted price is already paid — counted as paid.
            # What is asked later is the first full-price cycle after it.
            ev.phase = "covered"
            nxt = next_cycle_payment(active) if instruction is not None else None
            if nxt is not None:
                ev.due_on, ev.ask_opens_on = nxt["due"], nxt["ask_open"]
                ev.blocked_from = nxt["blocked_from"]
                ev.grace_ends_on = nxt["blocked_from"] - ONE_DAY
                if nxt["phase"] in ("ask", "grace", "blocked"):
                    ev.phase = nxt["phase"]
                    ev.amount = rule.amount
                    ev.source = "instruction"
                    ev.invoice_id = nxt["invoice"].id if nxt["invoice"] else None
                    ev.awaiting_approval = bool(nxt["invoice"] and nxt["invoice"].id in pending_invoice_ids)
                    ev.pay_start, ev.pay_end = nxt["pay_start"], nxt["pay_end"]
        else:
            # Post-paid: the discounted price is paid once, at the period's end.
            mine = [i for i in invoices if i.period_id == active.id]
            covered = any(i.status in _PAID for i in mine)
            open_inv = next((i for i in mine if i.status not in _PAID), None)
            phase, due, ask_open, blocked_from = phase_for(
                today,
                deadline=active.end_date,
                ask_days=rule.start_ask_days,
                grace_days=rule.grace_days,
                floor=active.start_date,
                covered=covered,
            )
            ev.phase = phase
            ev.due_on, ev.ask_opens_on, ev.blocked_from = due, ask_open, blocked_from
            ev.grace_ends_on = blocked_from - ONE_DAY
            ev.invoice_id = open_inv.id if open_inv else None
            ev.awaiting_approval = bool(open_inv and open_inv.id in pending_invoice_ids)
            ev.pay_period_id = active.id
        ev.timeline = _timeline(ev)
        return ev

    # 4. The payment-instruction cycle.
    if instruction is None:
        return Evaluation(kind="none", phase="none", source="no_instruction", needs_setup=True)

    ended = [p for p in priced if p.end_date < today]
    last_end = max((p.end_date for p in ended), default=None)
    anchor = created_on
    fresh = True  # no trial/discount came before: this is the client's first cycle
    if last_end is not None:
        # Cycles restart from scratch the day after the last trial/discount that
        # ended, whatever the creation date is.
        anchor = last_end + ONE_DAY
        fresh = False
    if anchor > today:  # created "in the future" — treat the first day as today
        anchor = today

    cur = cycle_index(anchor, rule, today)
    start_k = max(0, cur - LOOK_BACK)

    # row = (k, phase, start, end, deadline, ask_open, blocked_from, open_invoice)
    chosen = None
    summary = []
    for k in range(start_k, cur + LOOK_AHEAD + 1):
        s_k, e_k = cycle_bounds(anchor, rule, k)
        in_cycle = [i for i in plain if s_k <= i.due_date <= e_k]
        covered = any(i.status in _PAID for i in in_cycle)
        open_inv = next((i for i in in_cycle if i.status not in _PAID), None)
        floor = None
        if rule.is_prepayment:
            if k == 0:
                if fresh:
                    # A new client's first month: prepaid means already paid.
                    covered = True
                    deadline = s_k
                    floor = anchor
                else:
                    # First cycle after a trial/discount: asked near THAT end.
                    deadline = anchor - ONE_DAY
            else:
                deadline = cycle_bounds(anchor, rule, k - 1)[1]
        else:
            deadline = e_k
            floor = anchor if k == 0 else None
        phase, due, ask_open, blocked_from = phase_for(
            today,
            deadline=deadline,
            ask_days=rule.start_ask_days,
            grace_days=rule.grace_days,
            floor=floor,
            covered=covered,
        )
        row = (k, phase, s_k, e_k, due, ask_open, blocked_from, open_inv)
        summary.append(row)
        # The oldest payment that needs attention wins.
        if chosen is None and phase in ("ask", "grace", "blocked"):
            chosen = row

    next_due = next_ask = None
    if chosen is None:
        # Nothing to pay right now: describe today's cycle — covered (paid) or
        # upcoming (this cycle's request just hasn't opened yet).
        chosen = next((r for r in summary if r[0] == cur), summary[-1])
        after = next((r for r in summary if r[0] == cur + 1), None)
        if after is not None:
            next_due, next_ask = after[4], after[5]

    k, phase, s_c, e_c, due, ask_open, blocked_from, open_inv = chosen
    ev = Evaluation(
        kind="paid",
        phase=phase,
        source="instruction",
        amount=rule.amount,
        cycle_start=s_c,
        cycle_end=e_c,
        due_on=due,
        ask_opens_on=ask_open,
        blocked_from=blocked_from,
        grace_ends_on=blocked_from - ONE_DAY,
        days_left=(e_c - today).days,
        invoice_id=open_inv.id if open_inv else None,
        pay_start=s_c,
        pay_end=e_c,
        awaiting_approval=bool(open_inv and open_inv.id in pending_invoice_ids),
        next_due_on=next_due,
        next_ask_opens_on=next_ask,
    )
    ev.timeline = _timeline(ev)
    return ev


def _timeline(ev: Evaluation) -> dict:
    """JSON-ready snapshot for the apps."""
    return {
        "kind": ev.kind,
        "phase": ev.phase,
        "amount": ev.amount,
        "cycle_start": _iso(ev.cycle_start),
        "cycle_end": _iso(ev.cycle_end),
        "due_on": _iso(ev.due_on),
        "ask_opens_on": _iso(ev.ask_opens_on),
        "blocked_from": _iso(ev.blocked_from),
        "grace_ends_on": _iso(ev.grace_ends_on),
        "next_due_on": _iso(ev.next_due_on),
        "next_ask_opens_on": _iso(ev.next_ask_opens_on),
        "days_left": ev.days_left,
        "awaiting_approval": ev.awaiting_approval,
        "needs_setup": ev.needs_setup,
        "period_id": ev.active_period_id,
    }
