"""Client-scoped endpoints: profile, home screen, achievements,
notifications, device push registration."""
from datetime import date, timedelta

from django.utils import timezone
from rest_framework.response import Response

from apps.core.services import client_payment_payload
from apps.core.views import ClientAPIView
from apps.journal.models import FoodDiaryEntry, Measurement, ProgressPhoto, PersonalRecord, WaterLog
from apps.journal.serializers import (
    MeasurementSerializer,
    PersonalRecordSerializer,
    ProgressPhotoSerializer,
    WaterLogSerializer,
)
from apps.schedule.models import Session
from apps.schedule.views import session_payload

from .models import Achievement, Notification, PushToken, Streak
from .serializers import AchievementSerializer, NotificationSerializer, StreakSerializer


class MeView(ClientAPIView):
    """Feature 7: the client's profile — the trainer's client-detail payload,
    client-facing.

    Returns every client-detail field the client may see (name, contact, goals,
    medical history, emergency contact, weights/body-fat, avatar, last session,
    measurements, progress photos, personal records, streak, achievements).
    Trainer-only internals are deliberately NOT included: OTP, first-login flag,
    must-change-credential flag, payment instructions, access/billing state.
    """

    def get(self, request):
        c = self.client(request)
        last = (
            c.sessions.filter(status="completed")
            .order_by("-date")
            .values_list("date", flat=True)
            .first()
        )
        streak = Streak.objects.filter(client=c).first()
        return Response(
            {
                "id": c.id,
                "name": c.name,
                "phone": c.phone,
                "email": c.email,
                "mode": c.mode,
                "avatar_url": c.avatar_url,
                "goals": c.goals,
                "goal_label": ", ".join(c.goals) if c.goals else "—",
                "medical_history": c.medical_history,
                "emergency_contact": c.emergency_contact,
                "current_weight": c.current_weight,
                "target_weight": c.target_weight,
                "body_fat": c.body_fat,
                # localtime() matches DRF's DateTimeField rendering in the
                # trainer's client-detail payload (TIME_ZONE, +03:00).
                "created_at": timezone.localtime(c.created_at).isoformat(),
                "last_session_date": last.isoformat() if last else None,
                "trainer_name": c.trainer.full_name or c.trainer.username,
                # null when no streak row exists — identical to the trainer's
                # client-detail serializer (field-by-field parity).
                "streak": StreakSerializer(streak).data if streak else None,
                "measurements": MeasurementSerializer(
                    c.measurements.all(), many=True
                ).data,
                "photos": ProgressPhotoSerializer(c.photos.all(), many=True).data,
                "records": PersonalRecordSerializer(c.records.all(), many=True).data,
                "achievements": AchievementSerializer(
                    c.achievements.all(), many=True
                ).data,
            }
        )


class TodayView(ClientAPIView):
    """Home screen payload: today's workout, meals, next session, billing."""

    def get(self, request):
        c = self.client(request)
        today = date.today()
        weekday = today.strftime("%A")

        plan = (
            c.workout_plans.order_by("-updated_at").select_related("client").first()
        )
        today_workout = None
        other_days = []
        if plan:
            days = list(plan.days.prefetch_related("exercises__exercise"))
            for day in days:
                payload = {
                    "id": day.id,
                    "day_name": day.day_name,
                    "order": day.order,
                    "is_today": day.day_name.lower().startswith(weekday[:3].lower())
                    or day.day_name.lower() == weekday.lower(),
                    "exercises": [
                        {
                            "id": pe.id,
                            "name": pe.exercise.name,
                            "sets": pe.sets,
                            "reps": pe.reps,
                            "target_weight": pe.target_weight,
                            "rest_seconds": pe.rest_seconds,
                            "video_url": pe.exercise.video_url,
                            "description": pe.exercise.description,
                            "muscle_group": pe.exercise.muscle_group,
                        }
                        for pe in day.exercises.all()
                    ],
                }
                if payload["is_today"] and today_workout is None:
                    today_workout = payload
                other_days.append(payload)
            if today_workout is None and other_days:
                today_workout = other_days[0]

        diet = c.diet_plans.order_by("-created_at").first()
        meals = []
        macros = None
        if diet:
            macros = {
                "daily_calories": diet.daily_calories,
                "protein": diet.protein,
                "carbs": diet.carbs,
                "fats": diet.fats,
                "name": diet.name,
            }
            for meal in diet.meals.all():
                meals.append(
                    {
                        "name": meal.name,
                        "time": meal.time,
                        "items": [
                            {
                                "name": item.name,
                                "calories": item.calories,
                                "protein": item.protein,
                                "carbs": item.carbs,
                                "fats": item.fats,
                            }
                            for item in meal.items.all()
                        ],
                    }
                )

        upcoming = list(
            Session.objects.filter(client=c, date__gte=today)
            .exclude(status__in=["canceled", "completed", "no_show"])
            .prefetch_related("recommended_workouts", "recommended_diets")
            .order_by("date", "time")[:4]
        )
        next_session = upcoming[0] if upcoming else None
        todays_sessions = Session.objects.filter(client=c, date=today)

        water = WaterLog.objects.filter(client=c, date=today).first()

        # --- diary (what was actually eaten today) -------------------------
        diary_rows = list(
            FoodDiaryEntry.objects.filter(client=c, date=today).order_by("created_at")
        )
        actual = [e for e in diary_rows if getattr(e, "intent", "actual") != "intended"]
        diary = {
            "calories": sum((e.calories or 0) for e in actual),
            "entries": [
                {
                    "id": e.id,
                    "meal_name": e.meal_name,
                    "calories": e.calories,
                    "status": e.status,
                }
                for e in diary_rows
            ],
        }

        # --- body progress snapshot ----------------------------------------
        weights = [
            (m.date, m.weight)
            for m in c.measurements.order_by("date")
            if m.weight is not None
        ]
        start_weight = weights[0][1] if weights else c.current_weight
        current_weight = c.current_weight if c.current_weight is not None else (
            weights[-1][1] if weights else None
        )
        progress = {
            "start_weight": start_weight,
            "current_weight": current_weight,
            "target_weight": c.target_weight,
            "body_fat": c.body_fat,
            "last_measured": weights[-1][0].isoformat() if weights else None,
            "trend": [w for _, w in weights][-8:],
        }

        # --- attendance over the last 30 days ------------------------------
        window = Session.objects.filter(
            client=c, date__gte=today - timedelta(days=30), date__lte=today
        ).exclude(status__in=["canceled", "scheduled", "rescheduling"])
        total = window.count()
        attended = window.filter(status__in=["completed", "approved"]).count()
        attendance = {"attended": attended, "total": total}

        streak_row = Streak.objects.filter(client=c).first()

        return Response(
            {
                "today_workout": today_workout,
                "other_days": other_days,
                "diet": {"macros": macros, "meals": meals},
                "next_session": session_payload(next_session) if next_session else None,
                "upcoming_sessions": [session_payload(s) for s in upcoming],
                "today_sessions": [session_payload(s) for s in todays_sessions],
                "water": WaterLogSerializer(water).data if water else None,
                "diary": diary,
                "progress": progress,
                "attendance": attendance,
                "recent_achievements": AchievementSerializer(
                    c.achievements.order_by("-unlocked_at")[:3], many=True
                ).data,
                "counts": {
                    "workout_plans": c.workout_plans.count(),
                    "diet_plans": c.diet_plans.count(),
                },
                "billing": client_payment_payload(c),
                "streak": StreakSerializer(streak_row).data
                if streak_row
                else {"current": 0, "longest": 0},
            }
        )


class AchievementsView(ClientAPIView):
    def get(self, request):
        c = self.client(request)
        streak, _ = Streak.objects.get_or_create(client=c)
        return Response(
            {
                "streak": StreakSerializer(streak).data,
                "achievements": AchievementSerializer(
                    c.achievements.all()[:50], many=True
                ).data,
                "all_keys": [{"key": k, "label": l} for k, l in Achievement.KEYS],
            }
        )


class NotificationListView(ClientAPIView):
    def get(self, request):
        c = self.client(request)
        qs = Notification.objects.filter(target="client", user_id=c.id)
        rows = list(qs[:100])
        return Response(
            {
                "unread": qs.filter(read=False).count(),
                "results": NotificationSerializer(rows, many=True).data,
            }
        )

    def post(self, request):
        c = self.client(request)
        if request.data.get("all"):
            Notification.objects.filter(target="client", user_id=c.id).update(read=True)
        elif request.data.get("id"):
            Notification.objects.filter(target="client", user_id=c.id, id=request.data["id"]).update(read=True)
        return Response({"detail": "ok"})


class DeviceRegisterView(ClientAPIView):
    def post(self, request):
        c = self.client(request)
        token = request.data.get("token", "")
        if token:
            PushToken.objects.update_or_create(
                user_type="client", user_id=c.id, defaults={"token": token}
            )
        return Response({"detail": "registered"})
