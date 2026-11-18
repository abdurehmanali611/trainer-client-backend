from rest_framework import serializers

from .models import PermissionRequest, RescheduleRequest


class RescheduleRequestSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.name", read_only=True)
    session_info = serializers.SerializerMethodField()

    class Meta:
        model = RescheduleRequest
        fields = [
            "id", "session", "session_info", "client", "client_name",
            "requested_date", "requested_time", "reason", "status",
            "trainer_note", "created_at", "decided_at",
        ]
        read_only_fields = ["id", "status", "trainer_note", "created_at", "decided_at"]

    def get_session_info(self, obj):
        if not obj.session_id:
            return None
        return {
            "id": obj.session.id,
            "date": obj.session.date.isoformat(),
            "time": obj.session.time.isoformat(),
            "duration_minutes": obj.session.duration_minutes,
        }


class PermissionRequestSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.name", read_only=True)

    class Meta:
        model = PermissionRequest
        fields = [
            "id", "session", "client", "client_name", "date", "kind", "reason",
            "status", "trainer_note", "created_at", "decided_at",
        ]
        read_only_fields = ["id", "status", "trainer_note", "created_at", "decided_at"]
