from django.contrib import admin
from .models import TeamNameTemplate, CSVGeneration, RematchAuditLog


@admin.register(TeamNameTemplate)
class TeamNameTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'team_count', 'is_default', 'created_at')
    list_filter = ('is_default', 'created_at')
    search_fields = ('name',)

    def team_count(self, obj):
        return len(obj.team_names)
    team_count.short_description = 'Number of Teams'


@admin.register(CSVGeneration)
class CSVGenerationAdmin(admin.ModelAdmin):
    list_display = ('generated_at', 'student_count', 'team_size', 'template_used')
    list_filter = ('generated_at', 'template_used')
    readonly_fields = ('generated_at', 'csv_data', 'team_size', 'template_used', 'student_count')

    def has_add_permission(self, request):
        return False


@admin.register(RematchAuditLog)
class RematchAuditLogAdmin(admin.ModelAdmin):
    """Shows the rematch history. All fields are read-only — rows are written by the rematch worker."""

    list_display = (
        "started_at",
        "session_pk_snapshot",
        "outcome",
        "duration_ms",
        "generations_completed",
        "students_moved",
        "error_class",
    )
    list_filter = ("outcome", "db_vendor")
    search_fields = ("token", "error_message")
    date_hierarchy = "started_at"
    readonly_fields = [f.name for f in RematchAuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False  # audit records must not be deleted
