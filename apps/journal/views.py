"""Tracking endpoints: food diary, water, measurements, photos, records."""
from datetime import date

from rest_framework.response import Response

from apps.clients.models import Achievement
from apps.core.views import ClientAPIView

from .models import FoodDiaryEntry, Measurement, PersonalRecord, ProgressPhoto, WaterLog
from .serializers import (
    FoodDiaryEntrySerializer,
    MeasurementSerializer,
    PersonalRecordSerializer,
    ProgressPhotoSerializer,
    WaterLogSerializer,
)


class FoodDiaryView(ClientAPIView):
    def get(self, request):
        c = self.client(request)
        qs = FoodDiaryEntry.objects.filter(client=c)
        day = request.query_params.get("date")
        if day:
            qs = qs.filter(date=day)
        return Response(FoodDiaryEntrySerializer(qs[:100], many=True).data)

    def post(self, request):
        c = self.client(request)
        intent = str(request.data.get("intent") or "actual").strip().lower()
        if intent not in ("actual", "intended"):
            intent = "actual"
        entry = FoodDiaryEntry.objects.create(
            client=c,
            date=request.data.get("date") or date.today().isoformat(),
            meal_name=request.data.get("meal_name", ""),
            description=request.data.get("description", ""),
            calories=request.data.get("calories") or None,
            photo_url=request.data.get("photo_url", ""),
            intent=intent,
        )
        return Response(
            FoodDiaryEntrySerializer(entry).data, status=201
        )


class WaterView(ClientAPIView):
    def get(self, request):
        c = self.client(request)
        qs = WaterLog.objects.filter(client=c)
        day = request.query_params.get("date")
        if day:
            qs = qs.filter(date=day)
        return Response(WaterLogSerializer(qs[:60], many=True).data)

    def post(self, request):
        c = self.client(request)
        day = request.data.get("date") or date.today().isoformat()
        amount = int(request.data.get("amount_ml") or 250)
        log, _ = WaterLog.objects.get_or_create(client=c, date=day)
        log.amount_ml += amount
        log.save(update_fields=["amount_ml"])
        return Response(WaterLogSerializer(log).data)


class MeasurementsView(ClientAPIView):
    def get(self, request):
        c = self.client(request)
        return Response(MeasurementSerializer(c.measurements.all()[:60], many=True).data)

    def post(self, request):
        c = self.client(request)
        day = request.data.get("date") or date.today().isoformat()
        serializer = MeasurementSerializer(data={**request.data, "date": day})
        serializer.is_valid(raise_exception=True)
        existing = Measurement.objects.filter(client=c, date=day).first()
        if existing:
            for attr, value in serializer.validated_data.items():
                setattr(existing, attr, value)
            existing.save()
            serializer.instance = existing
        else:
            serializer.save(client=c)
        m = serializer.instance
        if m.weight is not None:
            c.current_weight = m.weight
        if m.body_fat is not None:
            c.body_fat = m.body_fat
        c.save(update_fields=["current_weight", "body_fat"])
        ach, created = Achievement.objects.get_or_create(client=c, key="first_weight_log")
        return Response(MeasurementSerializer(m).data, status=201)


class PhotosView(ClientAPIView):
    def get(self, request):
        c = self.client(request)
        return Response(ProgressPhotoSerializer(c.photos.all()[:60], many=True).data)

    def post(self, request):
        c = self.client(request)
        url = request.data.get("url", "")
        if not url:
            return Response({"detail": "url required"}, status=400)
        photo = ProgressPhoto.objects.create(
            client=c,
            url=url,
            date=request.data.get("date") or date.today().isoformat(),
            note=request.data.get("note", ""),
        )
        Achievement.objects.get_or_create(client=c, key="first_photo")
        return Response(ProgressPhotoSerializer(photo).data, status=201)


class RecordsView(ClientAPIView):
    def get(self, request):
        c = self.client(request)
        return Response(PersonalRecordSerializer(c.records.all()[:50], many=True).data)
