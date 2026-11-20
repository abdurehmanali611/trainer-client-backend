"""Scenario tests for the payment timing rules.

Pure Python — no database. The same file lives in the client backend and runs
there against its own copy of the engine, so the two can't drift apart.

Run from the BackEnd folder:
    python -m unittest discover -s apps/fitness/tests -p "test_payment_engine.py" -v

Vocabulary: the DEADLINE is the day "0 days remain". The request opens
`ask_days` before it; the client is blocked from `deadline + grace_days`.
"""
import importlib.util
import os
import sys
import unittest
from datetime import date

# Load the engine by path so the test needs neither Django nor a package layout
# (the file is identical in both backends, only its location differs).
_HERE = os.path.dirname(os.path.abspath(__file__))
for _candidate in (
    os.path.join(_HERE, "..", "payment_engine.py"),
    os.path.join(_HERE, "..", "..", "core", "payment_engine.py"),
):
    if os.path.exists(_candidate):
        _spec = importlib.util.spec_from_file_location("payment_engine", _candidate)
        pe = importlib.util.module_from_spec(_spec)
        sys.modules["payment_engine"] = pe
        _spec.loader.exec_module(pe)
        break
else:  # pragma: no cover
    raise ImportError("payment_engine.py not found next to the tests")

D = date
I = pe.InstructionRule
P = pe.PeriodRow
V = pe.InvoiceRow


def run(today, *, mode="paid", active=True, created=D(2026, 1, 1), instruction=None,
        periods=(), invoices=(), pending=()):
    return pe.evaluate(
        today,
        mode=mode,
        is_active=active,
        created_on=created,
        instruction=instruction,
        periods=list(periods),
        invoices=list(invoices),
        pending_invoice_ids=set(pending),
    )


class DateMaths(unittest.TestCase):
    def test_add_months_clamps_to_month_end(self):
        self.assertEqual(pe.add_months(D(2026, 1, 31), 1), D(2026, 2, 28))
        self.assertEqual(pe.add_months(D(2024, 1, 31), 1), D(2024, 2, 29))
        self.assertEqual(pe.add_months(D(2026, 11, 15), 3), D(2027, 2, 15))

    def test_monthly_cycle_bounds(self):
        rule = I(billing_cycle="monthly")
        self.assertEqual(pe.cycle_bounds(D(2026, 1, 1), rule, 0), (D(2026, 1, 1), D(2026, 1, 31)))
        self.assertEqual(pe.cycle_bounds(D(2026, 1, 1), rule, 1), (D(2026, 2, 1), D(2026, 2, 28)))
        a = D(2026, 1, 15)
        self.assertEqual(pe.cycle_bounds(a, rule, 0), (D(2026, 1, 15), D(2026, 2, 14)))
        self.assertEqual(pe.cycle_bounds(a, rule, 1), (D(2026, 2, 15), D(2026, 3, 14)))

    def test_quarterly_half_yearly_yearly(self):
        a = D(2026, 1, 1)
        self.assertEqual(pe.cycle_bounds(a, I(billing_cycle="quarterly"), 0)[1], D(2026, 3, 31))
        self.assertEqual(pe.cycle_bounds(a, I(billing_cycle="half_yearly"), 0)[1], D(2026, 6, 30))
        self.assertEqual(pe.cycle_bounds(a, I(billing_cycle="yearly"), 0)[1], D(2026, 12, 31))

    def test_custom_range_repeats_with_its_length(self):
        rule = I(billing_cycle="custom", cycle_from=D(2026, 7, 20), cycle_to=D(2026, 7, 26))  # 7 days
        a = D(2026, 3, 1)
        self.assertEqual(pe.cycle_bounds(a, rule, 0), (D(2026, 3, 1), D(2026, 3, 7)))
        self.assertEqual(pe.cycle_bounds(a, rule, 1), (D(2026, 3, 8), D(2026, 3, 14)))

    def test_cycle_index_finds_todays_cycle(self):
        rule = I(billing_cycle="monthly")
        a = D(2026, 1, 1)
        self.assertEqual(pe.cycle_index(a, rule, D(2026, 1, 1)), 0)
        self.assertEqual(pe.cycle_index(a, rule, D(2026, 1, 31)), 0)
        self.assertEqual(pe.cycle_index(a, rule, D(2026, 2, 1)), 1)
        self.assertEqual(pe.cycle_index(a, rule, D(2026, 12, 31)), 11)


class PhaseFor(unittest.TestCase):
    """The deadline rule in isolation: ask 5, grace 0, deadline 31 Jan."""

    def phase(self, today, grace=0, ask=5, covered=False):
        return pe.phase_for(today, deadline=D(2026, 1, 31), ask_days=ask, grace_days=grace,
                            floor=None, covered=covered)[0]

    def test_five_days_remaining_opens_the_request(self):
        self.assertEqual(self.phase(D(2026, 1, 25)), "upcoming")
        self.assertEqual(self.phase(D(2026, 1, 26)), "ask")  # 31 - 26 = 5 days remaining
        self.assertEqual(self.phase(D(2026, 1, 30)), "ask")  # 1 day remaining

    def test_zero_days_remaining_with_no_grace_is_blocked(self):
        self.assertEqual(self.phase(D(2026, 1, 31)), "blocked")
        self.assertEqual(self.phase(D(2026, 2, 5)), "blocked")

    def test_grace_keeps_the_app_open_then_blocks(self):
        self.assertEqual(self.phase(D(2026, 1, 31), grace=2), "grace")
        self.assertEqual(self.phase(D(2026, 2, 1), grace=2), "grace")
        self.assertEqual(self.phase(D(2026, 2, 2), grace=2), "blocked")

    def test_covered_is_never_asked_or_blocked(self):
        for day in (D(2026, 1, 20), D(2026, 1, 28), D(2026, 1, 31), D(2026, 3, 1)):
            self.assertEqual(self.phase(day, covered=True), "covered")

    def test_floor_stops_a_request_opening_before_the_cycle_exists(self):
        ph = pe.phase_for(D(2026, 1, 1), deadline=D(2026, 1, 3), ask_days=30, grace_days=0,
                          floor=D(2026, 1, 1), covered=False)
        self.assertEqual(ph[2], D(2026, 1, 1))  # ask_opens_on clamped to the floor


class Illustration(unittest.TestCase):
    def test_illustration_is_never_asked_or_blocked(self):
        for day in (D(2026, 1, 1), D(2026, 6, 1), D(2030, 1, 1)):
            ev = run(day, mode="illustrative", instruction=I())
            self.assertEqual(ev.kind, "illustration")
            self.assertFalse(ev.required)
            self.assertFalse(ev.blocked)


class FreeTrial(unittest.TestCase):
    """Monthly, prepaid, ask 5. Trial 1-14 Mar (nothing was paid during it)."""

    trial = P(1, D(2026, 3, 1), D(2026, 3, 14), 0, True)
    inst = I(billing_cycle="monthly", is_prepayment=True, start_ask_days=5, grace_days=0, amount=800.0)
    c = D(2026, 3, 1)

    def at(self, day, **kw):
        kw.setdefault("instruction", self.inst)
        return run(day, created=self.c, periods=[self.trial], **kw)

    def test_early_in_the_trial_nothing_is_asked(self):
        for day in (D(2026, 3, 1), D(2026, 3, 8)):
            ev = self.at(day)
            self.assertEqual(ev.kind, "trial")
            self.assertEqual(ev.phase, "none", day)
            self.assertFalse(ev.required)
            self.assertFalse(ev.blocked)
        ev = self.at(D(2026, 3, 8))
        self.assertEqual(ev.due_on, D(2026, 3, 14))  # the trial's last day is the deadline
        self.assertEqual(ev.ask_opens_on, D(2026, 3, 9))

    def test_prepaid_the_first_paid_month_is_asked_at_the_finish_of_the_trial(self):
        ev = self.at(D(2026, 3, 9))  # 5 days remain of the trial
        self.assertEqual(ev.kind, "trial")
        self.assertEqual(ev.phase, "ask")
        self.assertTrue(ev.required)
        self.assertEqual(ev.amount, 800.0)
        self.assertEqual(ev.pay_start, D(2026, 3, 15))
        self.assertEqual(ev.pay_end, D(2026, 4, 14))
        self.assertEqual(self.at(D(2026, 3, 13)).phase, "ask")

    def test_blocked_on_the_trials_last_day_when_grace_is_zero(self):
        ev = self.at(D(2026, 3, 14))  # 0 days remain
        self.assertEqual(ev.phase, "blocked")
        self.assertEqual(ev.blocked_from, D(2026, 3, 14))
        # and it stays blocked into the first cycle until the payment is approved
        ev = self.at(D(2026, 3, 15))
        self.assertEqual(ev.kind, "paid")
        self.assertEqual(ev.phase, "blocked")

    def test_grace_extends_it_past_the_end_of_the_trial(self):
        inst = I(billing_cycle="monthly", is_prepayment=True, start_ask_days=5, grace_days=2, amount=800.0)
        self.assertEqual(self.at(D(2026, 3, 14), instruction=inst).phase, "grace")
        self.assertEqual(self.at(D(2026, 3, 15), instruction=inst).phase, "grace")
        self.assertEqual(self.at(D(2026, 3, 16), instruction=inst).phase, "blocked")

    def test_paying_during_the_ask_window_clears_it(self):
        paid = [V(1, None, D(2026, 3, 15), "paid", 800)]
        for day in (D(2026, 3, 10), D(2026, 3, 14)):
            ev = self.at(day, invoices=paid)
            self.assertEqual(ev.phase, "covered", day)
            self.assertFalse(ev.blocked)
        ev = self.at(D(2026, 3, 20), invoices=paid)
        self.assertEqual(ev.kind, "paid")
        self.assertEqual(ev.cycle_start, D(2026, 3, 15))
        self.assertEqual(ev.phase, "covered")
        # the next request: 5 days before the first paid month ends (14 Apr)
        self.assertEqual(self.at(D(2026, 4, 8), invoices=paid).phase, "covered")
        ev = self.at(D(2026, 4, 9), invoices=paid)
        self.assertEqual(ev.phase, "ask")
        self.assertEqual(ev.cycle_start, D(2026, 4, 15))

    def test_pending_payment_during_the_trial_does_not_unblock(self):
        inv = [V(1, None, D(2026, 3, 15), "pending", 800)]
        ev = self.at(D(2026, 3, 14), invoices=inv, pending=[1])
        self.assertTrue(ev.blocked)
        self.assertTrue(ev.awaiting_approval)

    def test_postpaid_a_trial_asks_nothing_and_the_next_cycle_is_paid_at_its_end(self):
        inst = I(billing_cycle="monthly", is_prepayment=False, start_ask_days=5, grace_days=0, amount=800.0)
        for day in (D(2026, 3, 9), D(2026, 3, 14)):
            ev = self.at(day, instruction=inst)
            self.assertEqual(ev.phase, "none", day)
            self.assertFalse(ev.required)
        self.assertEqual(self.at(D(2026, 4, 8), instruction=inst).phase, "upcoming")
        self.assertEqual(self.at(D(2026, 4, 9), instruction=inst).phase, "ask")
        self.assertEqual(self.at(D(2026, 4, 14), instruction=inst).phase, "blocked")

    def test_trial_days_left(self):
        self.assertEqual(self.at(D(2026, 3, 10)).days_left, 4)

    def test_no_instruction_means_no_request_during_a_trial(self):
        ev = self.at(D(2026, 3, 12), instruction=None)
        self.assertEqual(ev.phase, "none")
        self.assertFalse(ev.required)

    def test_a_short_trial_restarts_the_timeline_from_scratch(self):
        # Created 1 Jan, trial 10-12 Jan (shorter than the ask window), prepaid.
        inst = I(billing_cycle="monthly", is_prepayment=True, start_ask_days=5, grace_days=0, amount=800.0)
        trial = P(2, D(2026, 1, 10), D(2026, 1, 12), 0, True)
        ev = run(D(2026, 1, 10), created=D(2026, 1, 1), instruction=inst, periods=[trial])
        self.assertEqual(ev.phase, "ask")  # asked from the trial's first day (never before it)
        self.assertEqual(ev.ask_opens_on, D(2026, 1, 10))
        # after it: the cycles restart on 13 Jan (13 Jan - 12 Feb); the old January
        # timeline is gone. Paying the new first cycle clears it.
        paid = [V(2, None, D(2026, 1, 13), "paid", 800)]
        ev = run(D(2026, 1, 13), created=D(2026, 1, 1), instruction=inst, periods=[trial], invoices=paid)
        self.assertEqual(ev.cycle_start, D(2026, 1, 13))
        self.assertEqual(ev.cycle_end, D(2026, 2, 12))
        self.assertEqual(ev.phase, "covered")
        self.assertEqual(run(D(2026, 1, 31), created=D(2026, 1, 1), instruction=inst,
                             periods=[trial], invoices=paid).phase, "covered")
        ev = run(D(2026, 2, 7), created=D(2026, 1, 1), instruction=inst, periods=[trial], invoices=paid)
        self.assertEqual(ev.phase, "ask")  # 5 days before 12 Feb

    def test_a_trial_followed_straight_by_a_discount_asks_nothing_at_the_boundary(self):
        disc = P(5, D(2026, 3, 15), D(2026, 4, 14), 500.0, False)
        ev = run(D(2026, 3, 12), created=self.c, instruction=self.inst, periods=[self.trial, disc])
        self.assertEqual(ev.kind, "trial")
        self.assertEqual(ev.phase, "none")

    def test_deleted_trial_returns_to_the_instruction_timeline(self):
        ev = run(D(2026, 3, 5), created=D(2026, 3, 1), instruction=self.inst, periods=[])
        self.assertEqual(ev.kind, "paid")
        self.assertEqual(ev.phase, "covered")  # prepaid first month


class Discount(unittest.TestCase):
    """Discount 1-30 Apr at 500, instruction price 1000."""

    disc = P(7, D(2026, 4, 1), D(2026, 4, 30), 500.0, False)
    pre = I(billing_cycle="monthly", is_prepayment=True, start_ask_days=5, grace_days=0, amount=1000.0)
    post = I(billing_cycle="monthly", is_prepayment=False, start_ask_days=5, grace_days=0, amount=1000.0)
    c = D(2026, 4, 1)

    def at(self, day, inst, **kw):
        return run(day, created=self.c, instruction=inst, periods=[self.disc], **kw)

    def test_prepaid_the_discounted_price_counts_as_already_paid(self):
        for day in (D(2026, 4, 1), D(2026, 4, 10), D(2026, 4, 24)):
            ev = self.at(day, self.pre)
            self.assertEqual(ev.kind, "discount")
            self.assertEqual(ev.phase, "covered", day)
            self.assertEqual(ev.amount, 500.0)
            self.assertFalse(ev.required)
            self.assertFalse(ev.blocked)

    def test_prepaid_the_next_full_price_month_is_asked_near_the_discounts_end(self):
        ev = self.at(D(2026, 4, 25), self.pre)  # 5 days remain
        self.assertEqual(ev.kind, "discount")
        self.assertEqual(ev.phase, "ask")
        self.assertTrue(ev.required)
        self.assertEqual(ev.amount, 1000.0)  # the normal price, not the discount
        self.assertEqual(ev.pay_start, D(2026, 5, 1))
        self.assertEqual(self.at(D(2026, 4, 30), self.pre).phase, "blocked")

    def test_prepaid_grace_applies_after_the_discount_ends(self):
        inst = I(billing_cycle="monthly", is_prepayment=True, start_ask_days=5, grace_days=3, amount=1000.0)
        self.assertEqual(self.at(D(2026, 4, 30), inst).phase, "grace")
        self.assertEqual(run(D(2026, 5, 2), created=self.c, instruction=inst, periods=[self.disc]).phase, "grace")
        self.assertEqual(run(D(2026, 5, 3), created=self.c, instruction=inst, periods=[self.disc]).phase, "blocked")

    def test_prepaid_paying_the_next_month_ahead_clears_it(self):
        inv = [V(20, None, D(2026, 5, 1), "paid", 1000)]
        for day in (D(2026, 4, 26), D(2026, 4, 30)):
            self.assertEqual(self.at(day, self.pre, invoices=inv).phase, "covered", day)
        ev = run(D(2026, 5, 10), created=self.c, instruction=self.pre, periods=[self.disc], invoices=inv)
        self.assertEqual(ev.kind, "paid")
        self.assertEqual(ev.cycle_start, D(2026, 5, 1))
        self.assertEqual(ev.phase, "covered")

    def test_prepaid_a_pending_discount_invoice_is_ignored(self):
        inv = [V(11, 7, D(2026, 4, 1), "pending", 500.0)]  # created when the discount was assigned
        ev = self.at(D(2026, 4, 10), self.pre, invoices=inv)
        self.assertEqual(ev.phase, "covered")
        self.assertFalse(ev.required)

    def test_postpaid_the_discounted_price_is_asked_near_its_end(self):
        inv = [V(11, 7, D(2026, 4, 1), "pending", 500.0)]
        self.assertEqual(self.at(D(2026, 4, 10), self.post, invoices=inv).phase, "upcoming")
        ev = self.at(D(2026, 4, 25), self.post, invoices=inv)
        self.assertEqual(ev.phase, "ask")
        self.assertEqual(ev.amount, 500.0)
        self.assertEqual(ev.invoice_id, 11)
        self.assertEqual(ev.pay_period_id, 7)
        self.assertEqual(self.at(D(2026, 4, 30), self.post, invoices=inv).phase, "blocked")

    def test_postpaid_discount_paid_is_covered(self):
        inv = [V(11, 7, D(2026, 4, 1), "paid", 500.0)]
        ev = self.at(D(2026, 4, 28), self.post, invoices=inv)
        self.assertEqual(ev.phase, "covered")
        self.assertFalse(ev.blocked)

    def test_legacy_zero_amount_period_is_not_a_discount(self):
        legacy = P(9, D(2026, 4, 1), D(2026, 4, 30), 0.0, False)
        ev = run(D(2026, 4, 10), created=D(2026, 4, 1), instruction=I(), periods=[legacy])
        self.assertEqual(ev.kind, "paid")

    def test_after_the_discount_the_instruction_amount_applies_again(self):
        inv = [V(20, None, D(2026, 5, 1), "paid", 1000)]
        ev = run(D(2026, 5, 1), created=self.c, instruction=self.pre, periods=[self.disc], invoices=inv)
        self.assertEqual(ev.kind, "paid")
        self.assertEqual(ev.amount, 1000.0)
        self.assertEqual(ev.cycle_start, D(2026, 5, 1))


class MonthlyPrepaidAsk5Grace0(unittest.TestCase):
    """The agreed example: monthly, prepaid, ask 5 days, grace 0."""

    inst = I(billing_cycle="monthly", is_prepayment=True, start_ask_days=5, grace_days=0, amount=800.0)
    c = D(2026, 1, 1)

    def test_the_first_month_is_never_asked_because_prepaid_means_already_paid(self):
        for day in (D(2026, 1, 1), D(2026, 1, 2), D(2026, 1, 15), D(2026, 1, 25)):
            ev = run(day, created=self.c, instruction=self.inst)  # no invoice at all
            self.assertEqual(ev.kind, "paid")
            self.assertEqual(ev.phase, "covered", day)
            self.assertFalse(ev.required)
            self.assertFalse(ev.blocked)

    def test_warning_starts_with_5_days_remaining_and_blocks_at_0(self):
        self.assertEqual(run(D(2026, 1, 25), created=self.c, instruction=self.inst).phase, "covered")
        ev = run(D(2026, 1, 26), created=self.c, instruction=self.inst)  # 5 days remain
        self.assertEqual(ev.phase, "ask")
        self.assertEqual(ev.ask_opens_on, D(2026, 1, 26))
        self.assertEqual(ev.due_on, D(2026, 1, 31))
        self.assertEqual(ev.cycle_start, D(2026, 2, 1))  # it is next month's money
        self.assertEqual(run(D(2026, 1, 30), created=self.c, instruction=self.inst).phase, "ask")
        ev = run(D(2026, 1, 31), created=self.c, instruction=self.inst)  # 0 days remain
        self.assertEqual(ev.phase, "blocked")
        self.assertEqual(ev.blocked_from, D(2026, 1, 31))

    def test_paying_next_month_ahead_clears_the_block(self):
        inv = [V(2, None, D(2026, 2, 1), "paid", 800)]
        for day in (D(2026, 1, 28), D(2026, 1, 31), D(2026, 2, 10)):
            self.assertFalse(run(day, created=self.c, instruction=self.inst, invoices=inv).blocked, day)

    def test_pending_payment_does_not_unblock(self):
        inv = [V(2, None, D(2026, 2, 1), "pending", 800)]
        ev = run(D(2026, 1, 31), created=self.c, instruction=self.inst, invoices=inv, pending=[2])
        self.assertTrue(ev.blocked)
        self.assertTrue(ev.awaiting_approval)

    def test_forgiven_counts_as_covered(self):
        inv = [V(2, None, D(2026, 2, 1), "forgiven", 0)]
        self.assertEqual(run(D(2026, 1, 31), created=self.c, instruction=self.inst, invoices=inv).phase, "covered")

    def test_unpaid_invoice_is_offered_for_payment(self):
        inv = [V(5, None, D(2026, 2, 1), "pending", 800)]
        ev = run(D(2026, 1, 28), created=self.c, instruction=self.inst, invoices=inv)
        self.assertEqual(ev.invoice_id, 5)
        self.assertFalse(ev.awaiting_approval)

    def test_grace_keeps_the_app_open_after_day_zero(self):
        inst = I(billing_cycle="monthly", is_prepayment=True, start_ask_days=5, grace_days=2)
        self.assertEqual(run(D(2026, 1, 31), created=self.c, instruction=inst).phase, "grace")
        self.assertEqual(run(D(2026, 2, 1), created=self.c, instruction=inst).phase, "grace")
        self.assertEqual(run(D(2026, 2, 2), created=self.c, instruction=inst).phase, "blocked")

    def test_old_unpaid_cycles_beyond_the_look_back_do_not_block(self):
        inv = [V(8, None, D(2026, 7, 1), "paid", 800), V(9, None, D(2026, 8, 1), "paid", 800)]
        ev = run(D(2026, 8, 10), created=self.c, instruction=self.inst, invoices=inv)
        self.assertFalse(ev.blocked)


class MonthlyPostpaidAsk5Grace0(unittest.TestCase):
    inst = I(billing_cycle="monthly", is_prepayment=False, start_ask_days=5, grace_days=0, amount=900.0)
    c = D(2026, 1, 1)

    def test_nothing_is_asked_early_in_the_month(self):
        ev = run(D(2026, 1, 10), created=self.c, instruction=self.inst)
        self.assertEqual(ev.phase, "upcoming")
        self.assertFalse(ev.required)
        self.assertEqual(ev.due_on, D(2026, 1, 31))
        self.assertEqual(ev.ask_opens_on, D(2026, 1, 26))

    def test_warning_5_days_before_the_month_ends_blocked_at_0(self):
        self.assertEqual(run(D(2026, 1, 25), created=self.c, instruction=self.inst).phase, "upcoming")
        self.assertEqual(run(D(2026, 1, 26), created=self.c, instruction=self.inst).phase, "ask")
        self.assertEqual(run(D(2026, 1, 30), created=self.c, instruction=self.inst).phase, "ask")
        self.assertEqual(run(D(2026, 1, 31), created=self.c, instruction=self.inst).phase, "blocked")

    def test_paying_the_month_clears_it(self):
        inv = [V(1, None, D(2026, 1, 1), "paid", 900)]
        self.assertFalse(run(D(2026, 1, 31), created=self.c, instruction=self.inst, invoices=inv).blocked)
        # February's own payment is requested 5 days before February ends (23 Feb)
        feb = lambda d: run(d, created=self.c, instruction=self.inst, invoices=inv)
        self.assertEqual(feb(D(2026, 2, 22)).phase, "upcoming")
        self.assertEqual(feb(D(2026, 2, 23)).phase, "ask")
        self.assertEqual(feb(D(2026, 2, 28)).phase, "blocked")

    def test_grace_extends_the_open_period(self):
        inst = I(billing_cycle="monthly", is_prepayment=False, start_ask_days=5, grace_days=2)
        self.assertEqual(run(D(2026, 1, 31), created=self.c, instruction=inst).phase, "grace")
        self.assertEqual(run(D(2026, 2, 1), created=self.c, instruction=inst).phase, "grace")
        self.assertEqual(run(D(2026, 2, 2), created=self.c, instruction=inst).phase, "blocked")

    def test_zero_ask_and_grace_blocks_on_the_last_day_without_warning(self):
        inst = I(billing_cycle="monthly", is_prepayment=False, start_ask_days=0, grace_days=0)
        self.assertEqual(run(D(2026, 1, 30), created=self.c, instruction=inst).phase, "upcoming")
        self.assertEqual(run(D(2026, 1, 31), created=self.c, instruction=inst).phase, "blocked")


class OtherCycleLengths(unittest.TestCase):
    """The same rules hold for every cycle length, not only monthly."""

    def test_quarterly_prepaid_first_quarter_assumed_paid_then_ask_7_days_before_it_ends(self):
        inst = I(billing_cycle="quarterly", is_prepayment=True, start_ask_days=7, grace_days=0, amount=2400.0)
        c = D(2026, 1, 1)
        self.assertEqual(run(D(2026, 1, 1), created=c, instruction=inst).phase, "covered")
        self.assertEqual(run(D(2026, 3, 23), created=c, instruction=inst).phase, "covered")
        ev = run(D(2026, 3, 24), created=c, instruction=inst)  # 7 days remain of Q1
        self.assertEqual(ev.phase, "ask")
        self.assertEqual(ev.cycle_start, D(2026, 4, 1))
        self.assertEqual(run(D(2026, 3, 31), created=c, instruction=inst).phase, "blocked")

    def test_quarterly_prepaid_after_a_trial(self):
        inst = I(billing_cycle="quarterly", is_prepayment=True, start_ask_days=7, grace_days=0, amount=2400.0)
        trial = P(1, D(2026, 1, 1), D(2026, 1, 31), 0, True)
        c = D(2026, 1, 1)
        self.assertEqual(run(D(2026, 1, 23), created=c, instruction=inst, periods=[trial]).phase, "none")
        ev = run(D(2026, 1, 24), created=c, instruction=inst, periods=[trial])  # 7 days remain
        self.assertEqual(ev.phase, "ask")
        self.assertEqual(ev.pay_start, D(2026, 2, 1))
        self.assertEqual(ev.pay_end, D(2026, 4, 30))
        paid = [V(1, None, D(2026, 2, 1), "paid", 2400)]
        self.assertEqual(run(D(2026, 2, 15), created=c, instruction=inst, periods=[trial], invoices=paid).phase, "covered")
        # next quarter's request: 7 days before 30 Apr
        self.assertEqual(run(D(2026, 4, 22), created=c, instruction=inst, periods=[trial], invoices=paid).phase, "covered")
        self.assertEqual(run(D(2026, 4, 23), created=c, instruction=inst, periods=[trial], invoices=paid).phase, "ask")

    def test_half_yearly_prepaid_discount_counts_as_paid_then_the_next_half_is_asked(self):
        inst = I(billing_cycle="half_yearly", is_prepayment=True, start_ask_days=10, grace_days=0, amount=5000.0)
        disc = P(1, D(2026, 1, 1), D(2026, 6, 30), 3000.0, False)
        c = D(2026, 1, 1)
        ev = run(D(2026, 3, 1), created=c, instruction=inst, periods=[disc])
        self.assertEqual((ev.kind, ev.phase, ev.amount), ("discount", "covered", 3000.0))
        ev = run(D(2026, 6, 20), created=c, instruction=inst, periods=[disc])  # 10 days remain
        self.assertEqual((ev.phase, ev.amount), ("ask", 5000.0))
        self.assertEqual(ev.pay_start, D(2026, 7, 1))
        self.assertEqual(run(D(2026, 6, 30), created=c, instruction=inst, periods=[disc]).phase, "blocked")

    def test_yearly_postpaid(self):
        inst = I(billing_cycle="yearly", is_prepayment=False, start_ask_days=30, grace_days=5, amount=9000.0)
        c = D(2026, 1, 1)
        self.assertEqual(run(D(2026, 11, 30), created=c, instruction=inst).phase, "upcoming")
        self.assertEqual(run(D(2026, 12, 1), created=c, instruction=inst).phase, "ask")  # 30 days remain
        self.assertEqual(run(D(2026, 12, 31), created=c, instruction=inst).phase, "grace")
        self.assertEqual(run(D(2027, 1, 5), created=c, instruction=inst).phase, "blocked")

    def test_custom_cycle_prepaid(self):
        inst = I(billing_cycle="custom", cycle_from=D(2026, 7, 20), cycle_to=D(2026, 7, 26),
                 is_prepayment=True, start_ask_days=2, grace_days=0)  # 7-day cycle
        c = D(2026, 3, 1)  # first cycle 1-7 Mar (assumed paid)
        self.assertEqual(run(D(2026, 3, 4), created=c, instruction=inst).phase, "covered")
        ev = run(D(2026, 3, 5), created=c, instruction=inst)  # 2 days remain
        self.assertEqual(ev.phase, "ask")
        self.assertEqual(ev.cycle_start, D(2026, 3, 8))
        self.assertEqual(run(D(2026, 3, 7), created=c, instruction=inst).phase, "blocked")

    def test_custom_cycle_after_a_trial(self):
        inst = I(billing_cycle="custom", cycle_from=D(2026, 7, 20), cycle_to=D(2026, 7, 26),
                 is_prepayment=True, start_ask_days=2, grace_days=1, amount=300.0)
        trial = P(1, D(2026, 3, 1), D(2026, 3, 10), 0, True)
        c = D(2026, 3, 1)
        self.assertEqual(run(D(2026, 3, 7), created=c, instruction=inst, periods=[trial]).phase, "none")
        self.assertEqual(run(D(2026, 3, 8), created=c, instruction=inst, periods=[trial]).phase, "ask")
        self.assertEqual(run(D(2026, 3, 10), created=c, instruction=inst, periods=[trial]).phase, "grace")
        self.assertEqual(run(D(2026, 3, 11), created=c, instruction=inst, periods=[trial]).phase, "blocked")


class EdgeCases(unittest.TestCase):
    def test_not_illustration_and_no_instruction_needs_setup(self):
        ev = run(D(2026, 2, 1), instruction=None)
        self.assertTrue(ev.needs_setup)
        self.assertFalse(ev.required)
        self.assertFalse(ev.blocked)

    def test_quarterly_prepaid(self):
        inst = I(billing_cycle="quarterly", is_prepayment=True, start_ask_days=7, grace_days=0)
        inv = [V(1, None, D(2026, 1, 1), "paid", 1)]
        self.assertEqual(run(D(2026, 3, 23), created=D(2026, 1, 1), instruction=inst, invoices=inv).phase, "covered")
        ev = run(D(2026, 3, 24), created=D(2026, 1, 1), instruction=inst, invoices=inv)  # 7 days remain
        self.assertEqual(ev.phase, "ask")
        self.assertEqual(ev.cycle_start, D(2026, 4, 1))
        self.assertEqual(run(D(2026, 3, 31), created=D(2026, 1, 1), instruction=inst, invoices=inv).phase, "blocked")

    def test_custom_cycle(self):
        inst = I(billing_cycle="custom", cycle_from=D(2026, 7, 20), cycle_to=D(2026, 7, 26),
                 is_prepayment=True, grace_days=1)
        c = D(2026, 3, 1)  # first 7-day cycle: 1-7 Mar, prepaid => already paid
        self.assertEqual(run(D(2026, 3, 1), created=c, instruction=inst).phase, "covered")
        self.assertEqual(run(D(2026, 3, 6), created=c, instruction=inst).phase, "covered")
        self.assertEqual(run(D(2026, 3, 7), created=c, instruction=inst).phase, "grace")  # 0 days remain
        self.assertEqual(run(D(2026, 3, 8), created=c, instruction=inst).phase, "blocked")

    def test_timeline_is_json_ready(self):
        ev = run(D(2026, 1, 1), created=D(2026, 1, 1), instruction=I())
        tl = ev.timeline
        self.assertEqual(tl["kind"], "paid")
        self.assertIsInstance(tl["due_on"], str)
        self.assertIsInstance(tl["blocked_from"], str)

    def test_future_period_is_ignored_today(self):
        later = P(3, D(2026, 6, 1), D(2026, 6, 30), 0, True)
        ev = run(D(2026, 5, 10), created=D(2026, 1, 1), instruction=I(), periods=[later])
        self.assertEqual(ev.kind, "paid")


if __name__ == "__main__":
    unittest.main()
