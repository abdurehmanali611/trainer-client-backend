from rest_framework import serializers

from .models import Invoice, Payment


class PaymentSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.name", read_only=True)
    invoice_info = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = [
            "id", "invoice", "invoice_info", "client", "client_name",
            "transaction_id", "channel", "status", "created_at", "decided_at",
            "decision_note",
        ]
        read_only_fields = [
            "id", "status", "created_at", "decided_at", "decision_note", "invoice_info",
        ]

    def get_invoice_info(self, obj):
        return {
            "id": obj.invoice_id,
            "amount": str(obj.invoice.amount),
            "due_date": obj.invoice.due_date.isoformat(),
            "status": obj.invoice.status,
        }


class InvoiceSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.name", read_only=True)
    period_info = serializers.SerializerMethodField()
    payments = PaymentSerializer(many=True, read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id", "client", "client_name", "period", "period_info", "amount",
            "due_date", "status", "created_at", "paid_at", "payments",
        ]

    def get_period_info(self, obj):
        if not obj.period_id:
            return None
        return {
            "id": obj.period.id,
            "start_date": obj.period.start_date.isoformat(),
            "end_date": obj.period.end_date.isoformat(),
            "is_trial": obj.period.is_trial,
        }
