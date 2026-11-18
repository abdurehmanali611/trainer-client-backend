"""Workout & diet plan models (port of trainer backend `fitness` models,
mapped onto the shared tables — unmanaged, schema owned by trainer backend)."""
from __future__ import annotations

from django.db import models


class Exercise(models.Model):
    MUSCLE_GROUPS = [
        ("Chest", "Chest"), ("Back", "Back"), ("Legs", "Legs"),
        ("Shoulders", "Shoulders"), ("Arms", "Arms"), ("Core", "Core"),
        ("Cardio", "Cardio"),
    ]
    DIFFICULTIES = [
        ("beginner", "Beginner"), ("intermediate", "Intermediate"),
        ("advanced", "Advanced"),
    ]
    TYPES = [
        ("strength", "Strength"), ("cardio", "Cardio"), ("core", "Core"),
        ("flexibility", "Flexibility"), ("mobility", "Mobility"),
        ("plyometrics", "Plyometrics"), ("other", "Other"),
    ]

    trainer = models.ForeignKey(
        "clients.Trainer", null=True, blank=True,
        on_delete=models.CASCADE, related_name="exercises",
    )  # null = shared/base library entry
    name = models.CharField(max_length=150)
    exercise_type = models.CharField(max_length=24, choices=TYPES, default="strength")
    muscle_group = models.CharField(max_length=32, choices=MUSCLE_GROUPS)
    difficulty = models.CharField(max_length=16, choices=DIFFICULTIES, default="beginner")
    equipment = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    sets = models.PositiveIntegerField(default=3)
    reps = models.CharField(max_length=32, default="10-12")
    rest_seconds = models.PositiveIntegerField(default=60)
    duration_minutes = models.PositiveIntegerField(null=True, blank=True)
    video_url = models.URLField(blank=True)

    class Meta:
        managed = False
        db_table = "fitness_exercise"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class WorkoutPlan(models.Model):
    trainer = models.ForeignKey(
        "clients.Trainer", on_delete=models.CASCADE, related_name="workout_plans"
    )
    client = models.ForeignKey(
        "clients.Client", null=True, blank=True, on_delete=models.CASCADE,
        related_name="workout_plans",
    )  # null = template
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = "fitness_workoutplan"
        ordering = ["-updated_at"]

    @property
    def is_template(self) -> bool:
        return self.client_id is None


class WorkoutDay(models.Model):
    plan = models.ForeignKey(WorkoutPlan, on_delete=models.CASCADE, related_name="days")
    day_name = models.CharField(max_length=50)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        managed = False
        db_table = "fitness_workoutday"
        ordering = ["order"]
        unique_together = [("plan", "day_name")]


class PlannedExercise(models.Model):
    day = models.ForeignKey(WorkoutDay, on_delete=models.CASCADE, related_name="exercises")
    exercise = models.ForeignKey(Exercise, on_delete=models.PROTECT)
    sets = models.PositiveIntegerField(default=3)
    reps = models.CharField(max_length=32, default="10")
    target_weight = models.FloatField(default=0)
    rest_seconds = models.PositiveIntegerField(default=60)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        managed = False
        db_table = "fitness_plannedexercise"
        ordering = ["order"]


class SetLog(models.Model):
    client = models.ForeignKey(
        "clients.Client", on_delete=models.CASCADE, related_name="set_logs"
    )
    planned_exercise = models.ForeignKey(
        PlannedExercise, null=True, blank=True, on_delete=models.SET_NULL
    )
    exercise_name = models.CharField(max_length=150)
    date = models.DateField()
    set_index = models.PositiveIntegerField(default=1)
    weight = models.FloatField(default=0)
    reps = models.PositiveIntegerField(default=0)
    completed = models.BooleanField(default=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "fitness_setlog"
        ordering = ["-date", "set_index"]


class DietPlan(models.Model):
    trainer = models.ForeignKey(
        "clients.Trainer", on_delete=models.CASCADE, related_name="diet_plans"
    )
    client = models.ForeignKey(
        "clients.Client", null=True, blank=True, on_delete=models.CASCADE,
        related_name="diet_plans",
    )  # null = template
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True, default="")
    daily_calories = models.PositiveIntegerField(default=2000)
    protein = models.PositiveIntegerField(default=150)
    carbs = models.PositiveIntegerField(default=200)
    fats = models.PositiveIntegerField(default=60)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "fitness_dietplan"
        ordering = ["-created_at"]

    @property
    def is_template(self) -> bool:
        return self.client_id is None


class Meal(models.Model):
    diet_plan = models.ForeignKey(DietPlan, on_delete=models.CASCADE, related_name="meals")
    name = models.CharField(max_length=100)
    time = models.CharField(max_length=32, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        managed = False
        db_table = "fitness_meal"
        ordering = ["order"]


class MealItem(models.Model):
    meal = models.ForeignKey(Meal, on_delete=models.CASCADE, related_name="items")
    name = models.CharField(max_length=120)
    calories = models.PositiveIntegerField(default=0)
    protein = models.FloatField(default=0)
    carbs = models.FloatField(default=0)
    fats = models.FloatField(default=0)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        managed = False
        db_table = "fitness_mealitem"
        ordering = ["order"]


class DietAdherence(models.Model):
    """Feature 26: client self-reports diet adherence (trainer read-only)."""

    client = models.ForeignKey(
        "clients.Client", on_delete=models.CASCADE, related_name="diet_adherence"
    )
    diet_plan = models.ForeignKey(
        DietPlan,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="adherence_reports",
    )
    session = models.ForeignKey(
        "schedule.Session",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="adherence_reports",
    )
    status = models.CharField(max_length=12)
    reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "fitness_dietadherence"
        ordering = ["-created_at"]
