"""Client tracking models: measurements, photos, records, food diary, water
(port of trainer backend `fitness` models, mapped onto the shared tables —
unmanaged, schema owned by trainer backend)."""
from __future__ import annotations

from django.db import models


class Measurement(models.Model):
    client = models.ForeignKey(
        "clients.Client", on_delete=models.CASCADE, related_name="measurements"
    )
    date = models.DateField()
    weight = models.FloatField(null=True, blank=True)
    body_fat = models.FloatField(null=True, blank=True)
    chest = models.FloatField(null=True, blank=True)
    waist = models.FloatField(null=True, blank=True)
    arms = models.FloatField(null=True, blank=True)
    thighs = models.FloatField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "fitness_measurement"
        ordering = ["date"]
        unique_together = [("client", "date")]


class ProgressPhoto(models.Model):
    client = models.ForeignKey(
        "clients.Client", on_delete=models.CASCADE, related_name="photos"
    )
    url = models.URLField()
    date = models.DateField()
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "fitness_progressphoto"
        ordering = ["-date"]


class PersonalRecord(models.Model):
    client = models.ForeignKey(
        "clients.Client", on_delete=models.CASCADE, related_name="records"
    )
    exercise = models.CharField(max_length=150)
    weight = models.FloatField()
    reps = models.PositiveIntegerField(default=1)
    date = models.DateField()

    class Meta:
        managed = False
        db_table = "fitness_personalrecord"
        ordering = ["-date"]
        unique_together = [("client", "exercise")]


class FoodDiaryEntry(models.Model):
    STATUS = [("pending", "Pending review"), ("reviewed", "Reviewed")]

    client = models.ForeignKey(
        "clients.Client", on_delete=models.CASCADE, related_name="food_entries"
    )
    date = models.DateField()
    meal_name = models.CharField(max_length=120)
    description = models.TextField()
    calories = models.PositiveIntegerField(null=True, blank=True)
    photo_url = models.URLField(blank=True)
    # Feature 29: actual (used) vs intended (plans to use).
    intent = models.CharField(max_length=12, default="actual")
    status = models.CharField(max_length=16, choices=STATUS, default="pending")
    trainer_feedback = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "fitness_fooddiaryentry"
        ordering = ["-date", "-created_at"]


class WaterLog(models.Model):
    client = models.ForeignKey(
        "clients.Client", on_delete=models.CASCADE, related_name="water_logs"
    )
    date = models.DateField()
    amount_ml = models.PositiveIntegerField(default=0)
    trainer_feedback = models.TextField(blank=True)
    reviewed = models.BooleanField(default=False)

    class Meta:
        managed = False
        db_table = "fitness_waterlog"
        ordering = ["-date"]
        unique_together = [("client", "date")]
