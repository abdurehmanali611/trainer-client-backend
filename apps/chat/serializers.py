from rest_framework import serializers

from .models import Message


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = [
            "id", "sender_type", "client", "content", "kind", "media_urls",
            "is_broadcast", "broadcast_id", "trainer_read_at", "client_read_at",
            "created_at",
        ]
