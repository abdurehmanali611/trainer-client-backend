from django.urls import path

from . import views

urlpatterns = [
    path("diary/", views.FoodDiaryView.as_view()),
    path("water/", views.WaterView.as_view()),
    path("measurements/", views.MeasurementsView.as_view()),
    path("photos/", views.PhotosView.as_view()),
    path("records/", views.RecordsView.as_view()),
]
