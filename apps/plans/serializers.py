from rest_framework import serializers

from .models import (
    DietPlan,
    Exercise,
    Meal,
    MealItem,
    PlannedExercise,
    WorkoutDay,
    WorkoutPlan,
)


class ExerciseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Exercise
        fields = [
            "id", "name", "exercise_type", "muscle_group", "difficulty", "equipment",
            "description", "sets", "reps", "rest_seconds", "duration_minutes",
            "video_url", "trainer",
        ]
        read_only_fields = ["id", "trainer"]


class PlannedExerciseSerializer(serializers.ModelSerializer):
    exercise_detail = ExerciseSerializer(source="exercise", read_only=True)

    class Meta:
        model = PlannedExercise
        fields = [
            "id", "exercise", "exercise_detail", "sets", "reps", "target_weight",
            "rest_seconds", "order",
        ]


class WorkoutDaySerializer(serializers.ModelSerializer):
    exercises = PlannedExerciseSerializer(many=True)

    class Meta:
        model = WorkoutDay
        fields = ["id", "day_name", "order", "exercises"]


class WorkoutPlanSerializer(serializers.ModelSerializer):
    days = WorkoutDaySerializer(many=True, required=False)
    client_name = serializers.CharField(source="client.name", read_only=True, default=None)

    class Meta:
        model = WorkoutPlan
        fields = ["id", "name", "description", "client", "client_name", "days", "is_template", "created_at"]
        read_only_fields = ["id", "is_template", "created_at"]

    def create(self, validated_data):
        days_data = validated_data.pop("days", [])
        plan = WorkoutPlan.objects.create(**validated_data)
        self._write_days(plan, days_data)
        return plan

    def update(self, instance, validated_data):
        days_data = validated_data.pop("days", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if days_data is not None:
            instance.days.all().delete()
            self._write_days(instance, days_data)
        return instance

    def _write_days(self, plan, days_data):
        for i, day_data in enumerate(days_data):
            exercises = day_data.pop("exercises", [])
            day = WorkoutDay.objects.create(
                plan=plan, day_name=day_data.get("day_name", f"Day {i + 1}"),
                order=day_data.get("order", i),
            )
            for j, ex in enumerate(exercises):
                PlannedExercise.objects.create(
                    day=day,
                    exercise_id=ex["exercise"],
                    sets=ex.get("sets", 3),
                    reps=ex.get("reps", "10"),
                    target_weight=ex.get("target_weight", 0),
                    rest_seconds=ex.get("rest_seconds", 60),
                    order=ex.get("order", j),
                )


class MealItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = MealItem
        fields = ["id", "name", "calories", "protein", "carbs", "fats", "order"]


class MealSerializer(serializers.ModelSerializer):
    items = MealItemSerializer(many=True)

    class Meta:
        model = Meal
        fields = ["id", "name", "time", "order", "items"]


class DietPlanSerializer(serializers.ModelSerializer):
    meals = MealSerializer(many=True, required=False)
    client_name = serializers.CharField(source="client.name", read_only=True, default=None)

    class Meta:
        model = DietPlan
        fields = [
            "id", "name", "description", "client", "client_name", "daily_calories", "protein",
            "carbs", "fats", "meals", "is_template", "created_at",
        ]
        read_only_fields = ["id", "is_template", "created_at"]

    def create(self, validated_data):
        meals_data = validated_data.pop("meals", [])
        plan = DietPlan.objects.create(**validated_data)
        self._write_meals(plan, meals_data)
        return plan

    def update(self, instance, validated_data):
        meals_data = validated_data.pop("meals", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if meals_data is not None:
            instance.meals.all().delete()
            self._write_meals(instance, meals_data)
        return instance

    def _write_meals(self, plan, meals_data):
        for i, meal_data in enumerate(meals_data):
            items = meal_data.pop("items", [])
            meal = Meal.objects.create(
                diet_plan=plan, name=meal_data.get("name", f"Meal {i + 1}"),
                time=meal_data.get("time", ""), order=meal_data.get("order", i),
            )
            for j, item in enumerate(items):
                MealItem.objects.create(
                    meal=meal, name=item.get("name", ""),
                    calories=item.get("calories", 0), protein=item.get("protein", 0),
                    carbs=item.get("carbs", 0), fats=item.get("fats", 0),
                    order=item.get("order", j),
                )
