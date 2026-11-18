"""Base view for client-scoped endpoints: client JWT via Authorization header."""
from rest_framework.views import APIView

from apps.clients.models import Client

from .auth import require_client


class ClientAPIView(APIView):
    authentication_classes = []
    permission_classes = []

    def client(self, request, *, allow_blocked_writes: bool = False) -> Client:
        return require_client(request, allow_blocked_writes=allow_blocked_writes)
