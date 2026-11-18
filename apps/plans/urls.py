from django.urls import path

from . import views

urlpatterns = [
    path("plans/", views.PlanListView.as_view()),
    path("plans/<int:pk>/", views.PlanDetailView.as_view()),
    path("diet/", views.DietView.as_view()),
    path("diet/adherence/", views.DietAdherenceView.as_view()),
    path("exercises/", views.ExerciseLibraryView.as_view()),
    path("workout/log/", views.WorkoutLogView.as_view()),
    path("workout/complete/", views.WorkoutCompleteView.as_view()),
]
