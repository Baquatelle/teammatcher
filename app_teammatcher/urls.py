from django.views.generic import RedirectView
from django.urls import path, include
from django.contrib import admin

urlpatterns = [
    path('', RedirectView.as_view(pattern_name='teacher_side:index', permanent=False), name='root_redirect'),
    path("admin/", admin.site.urls),
    path("student/", include(("student_side.urls", "student_side"), namespace="student_side")),
    path("teacher/", include(("teacher_side.urls", "teacher_side"), namespace="teacher_side")),
]