from django.urls import path
from . import views

app_name = "teacher_side"

urlpatterns = [
    path('', views.index, name='index'),
    path('download/', views.download_csv, name='download_csv'),
    path('download/<int:generation_id>/', views.download_historical_csv, name='download_historical_csv'),

    # Team adjustment view and APIs
    path('adjust/<int:session_id>/', views.adjust_teams, name='adjust_teams'),
    path('adjust/<int:session_id>/api/move/', views.api_move_student, name='api_move_student'),
    path('adjust/<int:session_id>/api/team/<int:team_id>/lock/', views.api_toggle_team_lock, name='api_toggle_team_lock'),
    path('adjust/<int:session_id>/api/student/<int:membership_id>/lock/', views.api_toggle_student_lock, name='api_toggle_student_lock'),
    path('adjust/<int:session_id>/export/', views.export_csv, name='export_csv'),
    path('adjust/<int:session_id>/rematch/start/', views.rematch_start, name='rematch_start'),
    path('adjust/<int:session_id>/rematch/', views.rematch_stream, name='rematch_stream'),
    path('adjust/<int:session_id>/rematch/cancel/', views.rematch_cancel, name='rematch_cancel'),
]
