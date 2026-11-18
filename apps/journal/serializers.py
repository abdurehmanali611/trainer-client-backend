from rest_framework import serializers

from .models import FoodDiaryEntry, Measurement, PersonalRecord, ProgressPhoto, WaterLog


class MeasurementSerializer(serializers.ModelSerializer):
    class Meta:
        model = Measurement
        fields = [
            "id", "date", "weight", "body_fat", "chest", "waist", "arms", "thighs",
        ]


class ProgressPhotoSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProgressPhoto
        fields = ["id", "url", "date", "note", "created_at"]


class PersonalRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = PersonalRecord
        fields = ["id", "exercise", "weight", "reps", "date"]


class FoodDiaryEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = FoodDiaryEntry
        fields = [
            "id", "date", "meal_name", "description", "calories", "photo_url",
            "intent", "status", "trainer_feedback", "created_at",
        ]
        read_only_fields = ["id", "status", "trainer_feedback", "created_at"]


class WaterLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = WaterLog
        fields = ["id", "date", "amount_ml", "trainer_feedback", "reviewed"]
