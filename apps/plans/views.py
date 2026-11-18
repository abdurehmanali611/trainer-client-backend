"""Plan endpoints: workout plans, diet plan, exercise library, workout logs."""
from datetime import date, datetime

from django.db.models import Q
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.clients.models import Achievement, Streak
from apps.clients.serializers import AchievementSerializer, StreakSerializer
from apps.core.services import notify_trainer
from apps.core.views import ClientAPIView

from .models import DietAdherence, DietPlan, Exercise, SetLog, WorkoutPlan
from .serializers import DietPlanSerializer, ExerciseSerializer, WorkoutPlanSerializer


class PlanListView(ClientAPIView):
    def get(self, request):
        c = self.client(request)
        plans = WorkoutPlan.objects.filter(client=c)
        return Response(WorkoutPlanSerializer(plans, many=True).data)


class PlanDetailView(ClientAPIView):
    def get(self, request, pk):
        c = self.client(request)
        plan = WorkoutPlan.objects.filter(client=c, pk=pk).first()
        if not plan:
            return Response({"detail": "Not found."}, status=404)
        return Response(WorkoutPlanSerializer(plan).data)


class DietView(ClientAPIView):
    def get(self, request):
        c = self.client(request)
        diet = DietPlan.objects.filter(client=c).order_by("-created_at").first()
        if not diet:
            return Response({"detail": "No diet plan assigned."}, status=404)
        data = DietPlanSerializer(diet).data
        # Feature 26: client sees their own adherence self-reports.
        data["my_adherence"] = [
            {
                "id": r.id,
                "status": r.status,
                "reason": r.reason,
                "created_at": r.created_at,
            }
            for r in DietAdherence.objects.filter(client=c, diet_plan=diet)[:10]
        ]
        return Response(data)


class DietAdherenceView(ClientAPIView):
    """Feature 26: client approves/declines diet with a required reason."""

    def post(self, request):
        c = self.client(request)
        status_value = str(request.data.get("status") or "").strip().lower()
        reason = str(request.data.get("reason") or "").strip()
        if status_value not in ("approved", "declined"):
            return Response(
                {"detail": "status must be 'approved' or 'declined'."}, status=400
            )
        if not reason:
            return Response({"detail": "A reason is required."}, status=400)
        diet = DietPlan.objects.filter(
            client=c, pk=request.data.get("diet_plan")
        ).first()
        if not diet:
            diet = DietPlan.objects.filter(client=c).order_by("-created_at").first()
        if not diet:
            return Response({"detail": "No diet plan assigned."}, status=400)
        report = DietAdherence.objects.create(
            client=c, diet_plan=diet, status=status_value, reason=reason
        )
        notify_trainer(
            c.trainer,
            f"Diet {status_value}",
            f"{c.name} marked the diet as {status_value}: {reason[:80]}",
            "diet",
            {"client_id": c.id, "diet_plan_id": diet.id, "adherence_id": report.id},
        )
        return Response(
            {"id": report.id, "status": report.status, "reason": report.reason},
            status=201,
        )


class ExerciseLibraryView(ClientAPIView):
    def get(self, request):
        qs = Exercise.objects.filter(
            Q(trainer__isnull=True) | Q(trainer=self.client(request).trainer)
        )
        group = request.query_params.get("group")
        if group and group != "All":
            qs = qs.filter(muscle_group=group)
        search = request.query_params.get("search")
        if search:
            qs = qs.filter(name__icontains=search)
        return Response(ExerciseSerializer(qs.order_by("name")[:200], many=True).data)


class WorkoutLogView(ClientAPIView):
    """Save set logs for today's workout."""

    def post(self, request):
        c = self.client(request)
        day = request.data.get("date") or date.today().isoformat()
        entries = request.data.get("entries") or []
        now = timezone.now()
        created = []
        for entry in entries:
            log = SetLog.objects.create(
                client=c,
                planned_exercise_id=entry.get("planned_exercise") or None,
                exercise_name=entry.get("exercise_name", ""),
                date=day,
                set_index=entry.get("set_index", 1),
                weight=entry.get("weight", 0),
                reps=entry.get("reps", 0),
                completed=bool(entry.get("completed", True)),
                completed_at=now if entry.get("completed", True) else None,
            )
            created.append(log.id)
        return Response({"logged": len(created)}, status=201)


class WorkoutCompleteView(ClientAPIView):
    """Mark the day's workout complete -> streak + achievements."""

    def post(self, request):
        c = self.client(request)
        day_str = request.data.get("date") or date.today().isoformat()
        day = datetime.strptime(day_str, "%Y-%m-%d").date()
        total_workouts = SetLog.objects.filter(client=c, completed=True).values("date").distinct().count()

        new_achievements = []
        streak, _ = Streak.objects.get_or_create(client=c)
        if not Streak.objects.filter(client=c).exists():
            streak.save()
        new_achievements += streak.register_activity(day)

        for key, needed in (
            ("first_workout", 1),
            ("ten_workouts", 10),
            ("fifty_workouts", 50),
        ):
            if total_workouts >= needed:
                ach, created = Achievement.objects.get_or_create(client=c, key=key)
                if created:
                    new_achievements.append(ach)

        for ach in new_achievements:
            notify_trainer(
                c.trainer,
                "Achievement unlocked",
                f"{c.name} unlocked: {ach.get_key_display()}",
                "achievement",
                {"client_id": c.id},
            )
        streak = Streak.objects.get(client=c)
        return Response(
            {
                "streak": StreakSerializer(streak).data,
                "new_achievements": AchievementSerializer(new_achievements, many=True).data,
                "total_workouts": total_workouts,
            }
        )
