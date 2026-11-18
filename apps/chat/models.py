"""Chat message model (port of trainer backend `fitness` model, mapped onto
the shared table — unmanaged, schema owned by trainer backend)."""
from __future__ import annotations

from django.db import models


class Message(models.Model):
    KINDS = [("text", "Text"), ("image", "Image"), ("voice", "Voice note")]
    sender_type = models.CharField(max_length=8)  # trainer | client
    client = models.ForeignKey(
        "clients.Client", on_delete=models.CASCADE, related_name="messages"
    )  # thread owner
    content = models.TextField(blank=True)
    kind = models.CharField(max_length=8, choices=KINDS, default="text")
    media_urls = models.JSONField(default=list, blank=True)
    is_broadcast = models.BooleanField(default=False)
    broadcast_id = models.CharField(max_length=40, blank=True, db_index=True)
    trainer_read_at = models.DateTimeField(null=True, blank=True)
    client_read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "fitness_message"
        ordering = ["created_at"]
