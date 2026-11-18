"""Billing models (port of trainer backend `fitness` models, mapped onto the
shared tables — unmanaged, schema owned by trainer backend)."""
from __future__ import annotations

from django.db import models


class PaymentInstruction(models.Model):
    """A reusable way for a trainer to collect payment.

    Trainers can create several and assign one to each client at creation time.
    Illustrative (non-payable) clients get none.
    """

    CHANNELS = [("telebirr", "Telebirr"), ("bank", "Bank transfer"), ("cash", "Cash")]
    CYCLES = [
        ("monthly", "Monthly"),
        ("quarterly", "Quarterly"),
        ("half_yearly", "Half a year"),
        ("yearly", "Yearly"),
        ("custom", "Custom range"),
    ]

    trainer = models.ForeignKey(
        "clients.Trainer", on_delete=models.CASCADE, related_name="payment_instructions"
    )
    label = models.CharField(max_length=120)
    channel = models.CharField(max_length=16, choices=CHANNELS, default="telebirr")
    telebirr_number = models.CharField(max_length=32, blank=True)
    bank_name = models.CharField(max_length=120, blank=True)
    bank_account_name = models.CharField(max_length=150, blank=True)
    bank_account_number = models.CharField(max_length=64, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    # When the money is requested: at the start of the period or at the end.
    is_prepayment = models.BooleanField(default=True)
    billing_cycle = models.CharField(max_length=16, choices=CYCLES, default="monthly")
    cycle_from = models.DateField(null=True, blank=True)
    cycle_to = models.DateField(null=True, blank=True)
    # Ask the client to pay & send the transaction id this many days BEFORE the
    # period ends…
    start_ask_days = models.PositiveIntegerField(default=0)
    # …and keep the app open this many days AFTER the final day, then block.
    grace_days = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "fitness_paymentinstruction"
        ordering = ["-created_at"]

    @property
    def cycle_label(self) -> str:
        if self.billing_cycle == "custom" and self.cycle_from and self.cycle_to:
            days = (self.cycle_to - self.cycle_from).days + 1
            return f"{days} day subscription"
        return dict(self.CYCLES).get(self.billing_cycle, self.billing_cycle)

    def __str__(self) -> str:
        return self.label


class SubscriptionPackage(models.Model):
    trainer = models.ForeignKey(
        "clients.Trainer", on_delete=models.CASCADE, related_name="packages"
    )
    name = models.CharField(max_length=120)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    sessions_per_week = models.PositiveIntegerField(default=3)
    duration_days = models.PositiveIntegerField(default=30)
    features = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        managed = False
        db_table = "fitness_subscriptionpackage"
        ordering = ["price"]


class SubscriptionPeriod(models.Model):
    client = models.ForeignKey(
        "clients.Client", on_delete=models.CASCADE, related_name="periods"
    )
    package = models.ForeignKey(
        SubscriptionPackage, null=True, blank=True, on_delete=models.SET_NULL
    )
    start_date = models.DateField()
    end_date = models.DateField()
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_trial = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "fitness_subscriptionperiod"
        ordering = ["-start_date"]

    def overlaps_today(self) -> bool:
        from datetime import date

        today = date.today()
        return self.start_date <= today <= self.end_date


class Invoice(models.Model):
    STATUS = [
        ("pending", "Pending"),
        ("paid", "Paid"),
        ("overdue", "Overdue"),
        ("forgiven", "Forgiven"),
    ]
    client = models.ForeignKey(
        "clients.Client", on_delete=models.CASCADE, related_name="invoices"
    )
    period = models.ForeignKey(
        SubscriptionPeriod, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="invoices",
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    due_date = models.DateField()
    status = models.CharField(max_length=16, choices=STATUS, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "fitness_invoice"
        ordering = ["-created_at"]


class Payment(models.Model):
    # Feature 12: the client's payment channel — exactly these three options.
    CHANNELS = [("telebirr", "Telebirr"), ("cbe", "CBE"), ("others", "Others")]
    STATUS = [
        ("pending", "Pending approval"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("forgiven", "Forgiven"),
    ]
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="payments")
    client = models.ForeignKey(
        "clients.Client", on_delete=models.CASCADE, related_name="payments"
    )
    transaction_id = models.CharField(max_length=120)
    channel = models.CharField(max_length=16, choices=CHANNELS, default="telebirr")
    status = models.CharField(max_length=16, choices=STATUS, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=255, blank=True)

    class Meta:
        managed = False
        db_table = "fitness_payment"
        ordering = ["-created_at"]
