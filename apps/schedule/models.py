"""Session & request models (port of trainer backend `fitness` models,
mapped onto the shared tables — unmanaged, schema owned by trainer backend)."""
from __future__ import annotations

from django.db import models


class Session(models.Model):
    STATUS = [
        ("scheduled", "Scheduled"),
        ("completed", "Completed"),
        ("canceled", "Canceled"),
        ("no_show", "No-show"),
    ]
    trainer = models.ForeignKey(
        "clients.Trainer", on_delete=models.CASCADE, related_name="sessions"
    )
    client = models.ForeignKey(
        "clients.Client", on_delete=models.CASCADE, related_name="sessions"
    )
    date = models.DateField()
    time = models.TimeField()
    # Explicit end of the session (start date/time → end date/time).
    end_date = models.DateField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    duration_minutes = models.PositiveIntegerField(default=60)
    status = models.CharField(max_length=16, choices=STATUS, default="scheduled")
    notes = models.TextField(blank=True)
    is_online = models.BooleanField(default=False)
    recommended_workouts = models.ManyToManyField(
        "plans.WorkoutPlan", blank=True, related_name="session_recommendations"
    )
    recommended_diets = models.ManyToManyField(
        "plans.DietPlan", blank=True, related_name="session_recommendations"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "fitness_session"
        ordering = ["date", "time"]
        unique_together = [("trainer", "date", "time")]


class RescheduleRequest(models.Model):
    STATUS = [("pending", "Pending"), ("approved", "Approved"), ("declined", "Declined")]
    session = models.ForeignKey(
        Session, on_delete=models.CASCADE, related_name="reschedule_requests"
    )
    client = models.ForeignKey(
        "clients.Client", on_delete=models.CASCADE, related_name="reschedule_requests"
    )
    requested_date = models.DateField()
    requested_time = models.TimeField(null=True, blank=True)
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=STATUS, default="pending")
    trainer_note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "fitness_reschedulerequest"


class PermissionRequest(models.Model):
    KIND = [("late", "Late"), ("absent", "Absent")]
    STATUS = [("pending", "Pending"), ("approved", "Approved"), ("declined", "Declined")]
    client = models.ForeignKey(
        "clients.Client", on_delete=models.CASCADE, related_name="permission_requests"
    )
    session = models.ForeignKey(
        Session, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="permission_requests",
    )
    date = models.DateField()
    kind = models.CharField(max_length=8, choices=KIND)
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=STATUS, default="pending")
    trainer_note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "fitness_permissionrequest"
