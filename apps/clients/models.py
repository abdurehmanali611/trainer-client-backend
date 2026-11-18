"""Client identity & engagement models (port of the trainer backend's
`fitness` models the client portal needs, mapped onto the shared tables).

All models are unmanaged: the trainer backend owns the schema (migrations);
this service only reads/writes the same MySQL tables.
"""
from __future__ import annotations

import secrets
import string

from django.db import models

# ---------------------------------------------------------------------------
# OTP generation (feature 2: global uniqueness, letters + digits + specials)
# ---------------------------------------------------------------------------

# Charset for trainer-generated OTPs: letters + digits + special characters
# (no whitespace — OTPs are compared after .strip() on login).
OTP_CHARSET = string.ascii_letters + string.digits + "!@#$%^&*_-+="
OTP_LENGTH = 10  # ≥ 8 per spec; large keyspace (~74^10) makes collisions negligible


def generate_unique_otp() -> str:
    for _ in range(100):
        otp = "".join(secrets.choice(OTP_CHARSET) for _ in range(OTP_LENGTH))
        if not Client.objects.filter(otp=otp).exists():
            return otp
    # Keyspace is huge; a miss here still gets backed by the DB unique
    # constraint on Client.otp (global across all trainers' clients).
    return "".join(secrets.choice(OTP_CHARSET) for _ in range(OTP_LENGTH))


# ---------------------------------------------------------------------------
# Trainer (the auth user row lives in the trainer backend's accounts app)
# ---------------------------------------------------------------------------


class Trainer(models.Model):
    """Read view of the trainer account row (db_table accounts_trainer)."""

    username = models.CharField(max_length=150)
    full_name = models.CharField(max_length=150, blank=True)
    telebirr_number = models.CharField(max_length=64, blank=True)
    bank_name = models.CharField(max_length=120, blank=True)
    bank_account_name = models.CharField(max_length=120, blank=True)
    bank_account_number = models.CharField(max_length=64, blank=True)
    cash_note = models.CharField(max_length=255, blank=True)

    class Meta:
        managed = False
        db_table = "accounts_trainer"

    @property
    def push_user_type(self) -> str:
        return "trainer"

    def __str__(self) -> str:
        return self.full_name or self.username


class TrainerBilling(models.Model):
    """Shared platform_billing_trainerbilling row — Feature 16 client-wide block."""

    trainer = models.OneToOneField(
        Trainer, on_delete=models.CASCADE, related_name="platform_billing", db_column="trainer_id"
    )
    setup_fee_paid = models.BooleanField(default=False)
    free_trial_ends_at = models.DateField(null=True, blank=True)
    paid_until = models.DateField(null=True, blank=True)
    billing_hold = models.BooleanField(default=False)
    is_illustration = models.BooleanField(default=False)
    account_status = models.CharField(max_length=16, default="active")

    class Meta:
        managed = False
        db_table = "platform_billing_trainerbilling"


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------


class Client(models.Model):
    MODE_CHOICES = [
        ("paid", "Paid subscription"),
        ("trial", "Free trial"),
        ("illustrative", "Illustrative (free forever)"),
    ]

    trainer = models.ForeignKey(Trainer, on_delete=models.CASCADE, related_name="clients")
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=32)
    email = models.EmailField()
    # Globally unique OTP (across ALL trainers' clients), letters + digits +
    # special characters; client logs in with it until they change it to their
    # own credential on first login.
    otp = models.CharField(max_length=16, unique=True, default=generate_unique_otp)
    credential = models.CharField(max_length=128, blank=True)
    must_change_credential = models.BooleanField(default=True)
    # Feature 4: set on first successful login (written by this backend's
    # login endpoint); trainer-side UI hides the OTP once it is set.
    first_login_at = models.DateTimeField(null=True, blank=True)

    mode = models.CharField(max_length=16, choices=MODE_CHOICES, default="paid")
    goals = models.JSONField(default=list, blank=True)
    medical_history = models.TextField(blank=True)
    emergency_contact = models.CharField(max_length=255, blank=True)
    current_weight = models.FloatField(null=True, blank=True)
    target_weight = models.FloatField(null=True, blank=True)
    body_fat = models.FloatField(null=True, blank=True)
    avatar_url = models.URLField(blank=True)
    payment_instruction = models.ForeignKey(
        "billing.PaymentInstruction",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="clients",
    )

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = "fitness_client"
        ordering = ["-created_at"]

    @property
    def push_user_type(self) -> str:
        return "client"

    @property
    def effective_credential(self) -> str:
        return self.credential or self.otp

    def __str__(self) -> str:
        return f"{self.name} ({self.phone})"


# ---------------------------------------------------------------------------
# Engagement: streaks, achievements, notifications, push tokens
# ---------------------------------------------------------------------------


class PushToken(models.Model):
    USER_TYPES = [("trainer", "Trainer"), ("client", "Client")]
    user_type = models.CharField(max_length=16, choices=USER_TYPES)
    user_id = models.PositiveIntegerField()
    token = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "fitness_pushtoken"
        unique_together = [("user_type", "user_id")]


class Achievement(models.Model):
    KEYS = [
        ("first_workout", "First workout completed"),
        ("ten_workouts", "10 workouts completed"),
        ("fifty_workouts", "50 workouts completed"),
        ("streak_7", "7-day streak"),
        ("streak_30", "30-day streak"),
        ("first_weight_log", "First weight logged"),
        ("first_photo", "First progress photo"),
        ("perfect_week", "Completed every planned workout this week"),
    ]
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="achievements")
    key = models.CharField(max_length=32, choices=KEYS)
    unlocked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "fitness_achievement"
        unique_together = [("client", "key")]
        ordering = ["-unlocked_at"]


class Streak(models.Model):
    client = models.OneToOneField(Client, on_delete=models.CASCADE, related_name="streak")
    current = models.PositiveIntegerField(default=0)
    longest = models.PositiveIntegerField(default=0)
    last_activity_date = models.DateField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "fitness_streak"

    def register_activity(self, day) -> list[Achievement]:
        """Register activity for `day`; returns newly unlocked achievements."""
        from datetime import timedelta

        new_achievements = []
        streak, _ = Streak.objects.get_or_create(client=self.client)
        if streak.last_activity_date == day:
            return new_achievements
        if streak.last_activity_date == day - timedelta(days=1):
            streak.current += 1
        else:
            streak.current = 1
        streak.longest = max(streak.longest, streak.current)
        streak.last_activity_date = day
        streak.save()

        for threshold, key in ((7, "streak_7"), (30, "streak_30")):
            if streak.current >= threshold:
                ach, created = Achievement.objects.get_or_create(
                    client=self.client, key=key
                )
                if created:
                    new_achievements.append(ach)
        return new_achievements


class Notification(models.Model):
    TARGETS = [("trainer", "Trainer"), ("client", "Client")]
    TYPES = [
        ("booking", "Booking"), ("payment", "Payment"), ("message", "Message"),
        ("alert", "Alert"), ("achievement", "Achievement"), ("request", "Request"),
    ]
    target = models.CharField(max_length=8, choices=TARGETS)
    # trainer: user id; client: client id
    user_id = models.PositiveIntegerField()
    title = models.CharField(max_length=150)
    body = models.CharField(max_length=255)
    type = models.CharField(max_length=16, choices=TYPES, default="alert")
    data = models.JSONField(default=dict, blank=True)
    read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "fitness_notification"
        ordering = ["-created_at"]
