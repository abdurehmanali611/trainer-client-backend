"""Chat endpoints: message history, send, mark-read."""
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from apps.core.services import notify_trainer
from apps.core.views import ClientAPIView

from .models import Message
from .serializers import MessageSerializer


class ChatMessagesView(ClientAPIView):
    def get(self, request):
        c = self.client(request)
        qs = c.messages.order_by("-created_at")
        before = request.query_params.get("before")
        if before:
            qs = qs.filter(created_at__lt=before)
        rows = list(qs[:100])
        rows.reverse()
        return Response(MessageSerializer(rows, many=True).data)

    def post(self, request):
        c = self.client(request)
        kind = request.data.get("kind", "text")
        content = request.data.get("content", "")
        media_urls = request.data.get("media_urls", []) or []
        if kind not in ("text", "image", "voice"):
            return Response({"detail": "Invalid kind."}, status=400)
        if not content and not media_urls:
            return Response({"detail": "Empty message."}, status=400)
        if kind == "image" and len(media_urls) > 5:
            return Response({"detail": "Max 5 images per message."}, status=400)
        msg = Message.objects.create(
            sender_type="client",
            client=c,
            content=content,
            kind=kind,
            media_urls=media_urls[:5],
            client_read_at=timezone.now(),
        )
        notify_trainer(
            c.trainer,
            f"Message from {c.name}",
            content[:120] or ("📷 Photo" if kind == "image" else "🎤 Voice note"),
            "message",
            {"client_id": c.id},
        )
        return Response(MessageSerializer(msg).data, status=status.HTTP_201_CREATED)


class ChatReadView(ClientAPIView):
    def post(self, request):
        c = self.client(request)
        updated = Message.objects.filter(
            client=c, sender_type="trainer", client_read_at__isnull=True
        ).update(client_read_at=timezone.now())
        return Response({"updated": updated})
