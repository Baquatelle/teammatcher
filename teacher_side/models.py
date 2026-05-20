from django.db import models
from django.utils import timezone


# The order here must match what the GA fitness function expects.
# Both get_weights() and weights_to_list() rely on this list to produce
# weights in the correct order. Do not reorder without also updating fitness_function.py.
#
# Note: 'job' comes before 'education' even though the form shows Education first.
WEIGHT_KEYS = [
    'availability', 'commitment', 'job', 'education',
    'age', 'gender', 'experience', 'lead', 'tasks',
]


def weights_to_list(d):
    """Convert a {key: weight} dict to an ordered list for the GA fitness function.
    Missing keys default to 0."""
    return [d.get(k, 0) for k in WEIGHT_KEYS]


class TeamNameTemplate(models.Model):
    name = models.CharField(max_length=100, help_text="Template name (e.g., 'Marvel Heroes')")
    team_names = models.JSONField(
        default=list,
        help_text="List of team names as JSON array"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    is_default = models.BooleanField(
        default=False,
        help_text="Mark this template as the default selection"
    )

    class Meta:
        ordering = ['-is_default', '-created_at']

    def __str__(self):
        return f"{self.name} ({len(self.team_names)} teams)"

    def save(self, *args, **kwargs):
        if self.is_default:
            TeamNameTemplate.objects.filter(is_default=True).update(is_default=False)
        super().save(*args, **kwargs)


class CSVGenerationManager(models.Manager["CSVGeneration"]):
    def create_generation(self, csv_data, team_size, template_used, student_count):
        generation = self.create(
            csv_data=csv_data,
            team_size=team_size,
            template_used=template_used,
            student_count=student_count
        )
        
        all_generations = self.order_by('-generated_at')
        if all_generations.count() > 5:
            old_generations = all_generations[5:]
            old_ids = [gen.id for gen in old_generations]
            self.filter(id__in=old_ids).delete()
        
        return generation


class CSVGeneration(models.Model):
    csv_data = models.TextField(help_text="CSV content as text")
    team_size = models.IntegerField(help_text="People per team")
    template_used = models.ForeignKey(
        TeamNameTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Team name template used (if any)"
    )
    generated_at = models.DateTimeField(default=timezone.now)
    student_count = models.IntegerField(help_text="Total number of students")

    objects: CSVGenerationManager = CSVGenerationManager()

    class Meta:
        ordering = ['-generated_at']
        verbose_name = "CSV Generation"
        verbose_name_plural = "CSV Generations"

    def __str__(self):
        template_name = self.template_used.name if self.template_used else "Default"
        return f"{self.generated_at.strftime('%Y-%m-%d %H:%M')} - {self.student_count} students - {template_name}"


class MatchingSession(models.Model):
    """Stores one complete run of the team matching algorithm.

    Holds the uploaded CSV, the GA settings used, and the current rematch status.
    Deleting a MatchingSession also deletes all its Teams and TeamMemberships.

    Only one MatchingSession exists at a time — creating a new one deletes the old one.
    """

    original_csv = models.TextField(
        help_text="Verbatim CSV uploaded for this session (audit + replay)."
    )
    min_size = models.IntegerField(help_text="Minimum allowed team size.")
    max_size = models.IntegerField(help_text="Maximum allowed team size.")

    # weights is stored as a dict, e.g. {"availability": 10, ...}.
    # Call weights_to_list(session.weights) to convert it to the ordered list the GA needs.
    weights = models.JSONField(
        help_text="Named-dict form of the fitness weights; see WEIGHT_KEYS for canonical order."
    )
    target_col = models.CharField(
        max_length=50,
        default="teams",
        help_text="Column name in the CSV where team labels are written.",
    )

    # PostgreSQL does not guarantee dict key order, so we store the column order
    # separately to ensure CSV export is consistent regardless of the database used.
    column_order = models.JSONField(
        default=list,
        help_text="Canonical CSV column order; jsonb-safe alternative to dict key order.",
    )
    template_used = models.ForeignKey(
        TeamNameTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Team name template used for this session, if any.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # Set when a rematch starts, cleared when it finishes.
    # If not null, a rematch is currently running.
    # Uses an atomic update to prevent two rematches from starting at the same time.
    rematch_token = models.CharField(
        max_length=36, null=True, blank=True, db_index=True
    )
    rematch_started_at = models.DateTimeField(null=True, blank=True)
    # Set to True by the cancel endpoint. The GA checks this after each generation
    # and stops early if it is True.
    rematch_cancel_requested = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"MatchingSession #{self.pk} ({self.created_at:%Y-%m-%d %H:%M})"


class Team(models.Model):
    """A team within a MatchingSession.

    Deleted automatically when its session is deleted.
    When is_locked is True, students cannot be moved in or out,
    and the team is skipped during re-matching.
    """

    session = models.ForeignKey(
        MatchingSession,
        on_delete=models.CASCADE,
        related_name="teams",
    )
    name = models.CharField(max_length=100)
    is_locked = models.BooleanField(
        default=False,
        help_text="When True, membership is frozen: moves in/out are rejected and the team is excluded from re-match.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("session", "name")]
        ordering = ["name"]

    def __str__(self):
        lock = " [locked]" if self.is_locked else ""
        return f"{self.name}{lock} (session #{self.session_id})"


class TeamMembership(models.Model):
    """Records which team a student belongs to within a MatchingSession.

    team=None means the student has not been assigned to a team yet.
    Locking a team does not automatically lock its members — each student has their own lock.

    original_row stores the student's CSV data at the time the session was created.
    The export uses this snapshot to rebuild the CSV, so changes to the student's
    profile after matching don't affect the exported results.

    profile links to the student's profile but is optional — the export never reads it.
    is_locked prevents this student from being moved or included in re-matching.
    """

    session = models.ForeignKey(
        MatchingSession,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    team = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="memberships",
        help_text="Null = unassigned pool.",
    )
    username = models.CharField(max_length=200)
    original_row = models.JSONField(
        help_text="Snapshot of all CSV columns at session creation, string-coerced.",
    )
    profile = models.ForeignKey(
        "student_side.StudentProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="team_memberships",
        help_text="Soft link by username; survives profile deletion.",
    )
    is_locked = models.BooleanField(
        default=False,
        help_text="Locked memberships reject moves and are excluded from re-match.",
    )

    class Meta:
        unique_together = [("session", "username")]
        ordering = ["username"]

    def __str__(self):
        lock = " [locked]" if self.is_locked else ""
        team_name = self.team.name if self.team_id else "(unassigned)"
        return f"{self.username} \u2192 {team_name}{lock}"


class RematchAuditLog(models.Model):
    """Records every rematch attempt for auditing purposes.

    Each row is written by the rematch worker. Rows are never deleted —
    they serve as a permanent log of what happened and when.
    """

    OUTCOME_RUNNING = "running"
    OUTCOME_SUCCESS = "success"
    OUTCOME_CANCELLED = "cancelled"
    OUTCOME_ERROR = "error"
    OUTCOME_WORKER_DIED = "worker_died"
    OUTCOME_REJECTED = "rejected"  # a rematch was already running
    OUTCOME_DISCONNECT = "client_disconnect"  # browser disconnected before rematch finished
    OUTCOME_NOOP = "noop"  # too few unlocked students to run
    OUTCOME_CHOICES = [
        (OUTCOME_RUNNING, "Running"),
        (OUTCOME_SUCCESS, "Success"),
        (OUTCOME_CANCELLED, "Cancelled"),
        (OUTCOME_ERROR, "Error"),
        (OUTCOME_WORKER_DIED, "Worker died"),
        (OUTCOME_REJECTED, "Rejected (already running)"),
        (OUTCOME_DISCONNECT, "Client disconnect"),
        (OUTCOME_NOOP, "No-op (degraded)"),
    ]

    session = models.ForeignKey(
        "MatchingSession",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rematch_audit_logs",
    )
    session_pk_snapshot = models.IntegerField(
        help_text="MatchingSession PK at row creation; survives session deletion."
    )

    token = models.CharField(max_length=36, db_index=True)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.IntegerField(null=True, blank=True)

    outcome = models.CharField(
        max_length=20, choices=OUTCOME_CHOICES, default=OUTCOME_RUNNING, db_index=True
    )
    generations_completed = models.IntegerField(default=0)
    students_moved = models.IntegerField(default=0)
    teams_created = models.IntegerField(default=0)

    # A copy of the weights used for this rematch, saved here so the record
    # stays meaningful even if the session's weights are changed later.
    weights_snapshot = models.JSONField(default=dict)

    error_class = models.CharField(max_length=200, blank=True)
    error_message = models.TextField(blank=True)

    # Operational forensics
    db_vendor = models.CharField(max_length=20, blank=True)
    worker_pid = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            # "Recent runs for this session" — common admin query
            models.Index(fields=["session", "-started_at"]),
            # "All failures since X" — common ops query
            models.Index(fields=["outcome", "-started_at"]),
            # "Find this token's row" — used by cleanup path to update outcome
            models.Index(fields=["token"]),
        ]

    def __str__(self):
        return f"rematch[{self.token[:8]}] session={self.session_pk_snapshot} {self.outcome}"

    # Only these fields may be updated when a rematch finishes.
    # All other fields are set at row creation and must not change.
    _MARK_COMPLETED_ALLOWED_FIELDS = frozenset(
        {
            "generations_completed",
            "students_moved",
            "teams_created",
            "error_class",
            "error_message",
        }
    )

    def mark_completed(self, outcome, **fields):
        """Mark this rematch as finished, recording the outcome and how long it took.

        Raises ValueError for unexpected field names — this catches typos
        like students_move= that would otherwise silently do nothing.
        """
        bad = set(fields) - self._MARK_COMPLETED_ALLOWED_FIELDS
        if bad:
            raise ValueError(
                f"mark_completed got unsupported kwargs: {sorted(bad)}. "
                f"Allowed: {sorted(self._MARK_COMPLETED_ALLOWED_FIELDS)}"
            )
        now = timezone.now()
        self.completed_at = now
        self.duration_ms = int((now - self.started_at).total_seconds() * 1000)
        self.outcome = outcome
        for k, v in fields.items():
            setattr(self, k, v)
        self.save(
            update_fields=[
                "completed_at",
                "duration_ms",
                "outcome",
                "generations_completed",
                "students_moved",
                "teams_created",
                "error_class",
                "error_message",
            ]
        )


# class Student(models.Model):
#     student_id = models.CharField(primary_key=True, max_length=50)
#     vector = models.TextField()
