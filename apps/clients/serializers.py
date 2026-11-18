from rest_framework import serializers

from .models import Achievement, Notification, Streak


class StreakSerializer(serializers.ModelSerializer):
    class Meta:
        model = Streak
        fields = ["current", "longest", "last_activity_date"]


class AchievementSerializer(serializers.ModelSerializer):
    label = serializers.CharField(source="get_key_display", read_only=True)

    class Meta:
        model = Achievement
        fields = ["id", "key", "label", "unlocked_at"]


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = [
            "id", "title", "body", "type", "data", "read", "created_at",
        ]
