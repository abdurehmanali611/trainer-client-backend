"""Schedule endpoints: sessions, reschedule/permission requests, attendance."""
from datetime import date

from rest_framework import status
from rest_framework.response import Response

from apps.core.services import notify_trainer
from apps.core.views import ClientAPIView

from .models import PermissionRequest, RescheduleRequest, Session
from .serializers import PermissionRequestSerializer, RescheduleRequestSerializer


def session_payload(s, *, with_plans: bool = True) -> dict:
    """One session as the client app renders it.

    Includes the explicit end (when the trainer set one) and — for offline
    sessions only, matching SessionDetailView — the plans the trainer attached,
    so Home and Schedule can show "what this session covers" without a second
    request. Online sessions are announcement-only (no plan payload).
    """
    plans = bool(with_plans and not s.is_online)
    return {
        "id": s.id,
        "date": s.date.isoformat(),
        "time": s.time.isoformat(),
        "end_date": s.end_date.isoformat() if s.end_date else None,
        "end_time": s.end_time.isoformat() if s.end_time else None,
        "duration_minutes": s.duration_minutes,
        "status": s.status,
        "is_online": s.is_online,
        "notes": s.notes,
        "is_today": s.date == date.today(),
        "recommended_workouts": (
            [{"id": p.id, "name": p.name} for p in s.recommended_workouts.all()] if plans else []
        ),
        "recommended_diets": (
            [{"id": p.id, "name": p.name} for p in s.recommended_diets.all()] if plans else []
        ),
    }


class ScheduleView(ClientAPIView):
    def get(self, request):
        c = self.client(request)
        qs = Session.objects.filter(client=c)
        start = request.query_params.get("from")
        end = request.query_params.get("to")
        if start:
            qs = qs.filter(date__gte=start)
        if end:
            qs = qs.filter(date__lte=end)
        rows = [
            session_payload(s)
            for s in qs.prefetch_related("recommended_workouts", "recommended_diets").order_by(
                "date", "time"
            )[:200]
        ]
        return Response(rows)


class RescheduleRequestCreateView(ClientAPIView):
    def post(self, request):
        c = self.client(request)
        session = Session.objects.filter(client=c, pk=request.data.get("session_id")).first()
        if not session:
            return Response({"detail": "Session not found."}, status=404)
        requested_date = request.data.get("requested_date")
        if not requested_date:
            return Response({"detail": "requested_date is required."}, status=400)
        req = RescheduleRequest.objects.create(
            session=session,
            client=c,
            requested_date=requested_date,
            requested_time=request.data.get("requested_time") or None,
            reason=request.data.get("reason", ""),
        )
        notify_trainer(
            c.trainer,
            "Reschedule request",
            f"{c.name} wants to move session {session.date.isoformat()} to {requested_date}.",
            "request",
            {"reschedule_request_id": req.id, "client_id": c.id},
        )
        return Response(
            RescheduleRequestSerializer(req).data, status=status.HTTP_201_CREATED
        )


class PermissionRequestCreateView(ClientAPIView):
    def post(self, request):
        c = self.client(request)
        kind = request.data.get("kind")
        day = request.data.get("date")
        if kind not in ("late", "absent") or not day:
            return Response({"detail": "kind (late|absent) and date required."}, status=400)
        session = Session.objects.filter(client=c, date=day).first()
        req = PermissionRequest.objects.create(
            client=c,
            session=session,
            date=day,
            kind=kind,
            reason=request.data.get("reason", ""),
        )
        notify_trainer(
            c.trainer,
            f"{req.get_kind_display()} request",
            f"{c.name} requests {kind} on {day}: {req.reason or 'no reason given'}",
            "request",
            {"permission_request_id": req.id, "client_id": c.id},
        )
        return Response(
            PermissionRequestSerializer(req).data, status=status.HTTP_201_CREATED
        )


class MyRequestsView(ClientAPIView):
    def get(self, request):
        c = self.client(request)
        reschedules = RescheduleRequest.objects.filter(client=c).order_by("-created_at")[:50]
        permissions = PermissionRequest.objects.filter(client=c).order_by("-created_at")[:50]
        return Response(
            {
                "reschedules": RescheduleRequestSerializer(reschedules, many=True).data,
                "permissions": PermissionRequestSerializer(permissions, many=True).data,
            }
        )


class MyAttendanceView(ClientAPIView):
    def get(self, request):
        c = self.client(request)
        sessions = Session.objects.filter(client=c).order_by("-date")[:200]
        summary = {"completed": 0, "no_show": 0, "canceled": 0, "scheduled": 0}
        rows = []
        for s in sessions:
            summary[s.status] = summary.get(s.status, 0) + 1
            rows.append(
                {
                    "id": s.id,
                    "date": s.date.isoformat(),
                    "time": s.time.isoformat(),
                    "status": s.status,
                }
            )
        return Response({"summary": summary, "sessions": rows})


class SessionDetailView(ClientAPIView):
    """Feature 20: offline sessions open with workout/diet plan detail.
    Online sessions are announcement-only (no plan payload)."""

    def get(self, request, pk: int):
        from apps.plans.serializers import DietPlanSerializer, WorkoutPlanSerializer

        c = self.client(request)
        session = Session.objects.filter(client=c, pk=pk).first()
        if not session:
            return Response({"detail": "Session not found."}, status=404)
        payload = {
            "id": session.id,
            "date": session.date.isoformat(),
            "time": session.time.isoformat(),
            "duration_minutes": session.duration_minutes,
            "status": session.status,
            "is_online": session.is_online,
            "notes": session.notes,
            "recommended_workouts": [],
            "recommended_diets": [],
        }
        if not session.is_online:
            payload["recommended_workouts"] = WorkoutPlanSerializer(
                session.recommended_workouts.all(), many=True
            ).data
            payload["recommended_diets"] = DietPlanSerializer(
                session.recommended_diets.all(), many=True
            ).data
        return Response(payload)


class SessionFinishView(ClientAPIView):
    """Feature 25: client marks an offline session finished."""

    def post(self, request, pk: int):
        c = self.client(request)
        session = Session.objects.filter(client=c, pk=pk).first()
        if not session:
            return Response({"detail": "Session not found."}, status=404)
        if session.is_online:
            return Response(
                {"detail": "Online sessions are finished by the trainer."},
                status=400,
            )
        session.status = "completed"
        session.save(update_fields=["status"])
        notify_trainer(
            c.trainer,
            "Offline session finished",
            f"{c.name} marked the session on {session.date.isoformat()} as finished.",
            "booking",
            {"session_id": session.id, "client_id": c.id},
        )
        return Response({"id": session.id, "status": session.status})
