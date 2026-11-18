"""Billing endpoints: payment snapshot + payment submission + invoices."""
from rest_framework import status
from rest_framework.response import Response

from apps.core.services import client_payment_payload, notify_trainer
from apps.core.views import ClientAPIView

from .models import Invoice, Payment
from .serializers import InvoiceSerializer


class MyBillingView(ClientAPIView):
    def get(self, request):
        c = self.client(request)
        return Response(client_payment_payload(c))

    def post(self, request):
        """Submit payment: transaction id only (amount fixed by trainer).

        Feature 15: exempt from the read-only write gate — submitting the
        payment is exactly what unblocks a client past the deadline.
        """
        c = self.client(request, allow_blocked_writes=True)
        transaction_id = (request.data.get("transaction_id") or "").strip()
        channel = request.data.get("channel", "telebirr")
        if not transaction_id:
            return Response({"detail": "transaction_id is required."}, status=400)
        billing = client_payment_payload(c)
        invoice_id = request.data.get("invoice_id") or (billing.get("invoice") or {}).get("id")
        if not invoice_id:
            return Response(
                {"detail": "No pending invoice right now."}, status=400
            )
        invoice = Invoice.objects.filter(pk=invoice_id, client=c).first()
        if not invoice:
            return Response({"detail": "Invoice not found."}, status=404)
        payment = Payment.objects.create(
            invoice=invoice,
            client=c,
            transaction_id=transaction_id,
            channel=channel if channel in ("telebirr", "cbe", "others") else "telebirr",
        )
        notify_trainer(
            c.trainer,
            "Payment submitted",
            f"{c.name} submitted {payment.transaction_id} ({payment.channel}).",
            "payment",
            {"payment_id": payment.id, "client_id": c.id},
        )
        return Response(
            {
                "detail": "Payment submitted. Waiting for your trainer's approval.",
                "payment_id": payment.id,
            },
            status=status.HTTP_201_CREATED,
        )


class InvoiceListView(ClientAPIView):
    def get(self, request):
        c = self.client(request)
        invoices = Invoice.objects.filter(client=c).prefetch_related("payments", "period")
        return Response(InvoiceSerializer(invoices, many=True).data)
