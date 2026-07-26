from django.urls import path
from .views import (
    TaskListView,
    TaskDetailView
)
from . import views

urlpatterns = [
    path('', TaskListView.as_view(), name='task-list'),
    path('<pk>/', TaskDetailView.as_view(), name='task-detail'),
    path('show-hint/<str:pk>/', views.showHint, name="show-hint"),
    path('bomb-detonate/<str:pk>/', views.bombDetonate, name="bomb-detonate"),
    path('bomb-verify-open/<str:pk>/', views.bombVerifyOpen, name="bomb-verify-open"),
    path('cursorless-solved/<str:pk>/', views.cursorlessSolved, name="cursorless-solved"),
]