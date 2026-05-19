from django.db import models
from django.utils import timezone


# === Canonical weights ordering ============================================
# This list MUST match the unpacking order in
# `teacher_side/matcher/fitness_function.py::make_fitness_func` and the
# return order of `teacher_side/matcher/utils.py::get_weights`. Reordering
# here without updating those two sites will silently misapply weights to
# wrong criteria with no exception thrown. `WeightsOrderingTest` guards
# against drift.
#
# NOTE: `job` comes before `education` here. The form field labels read
# left-to-right as Education, Professional, but the GA's tuple unpacking
# is `w_avail, w_commit, w_job, w_edu, ...`.
WEIGHT_KEYS = [
    'availability', 'commitment', 'job', 'education',
    'age', 'gender', 'experience', 'lead', 'tasks',
]


def weights_to_list(d):
    """Convert a {key: weight} dict to the canonical ordered list the GA
    fitness function consumes. Missing keys default to 0."""
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
    """One complete matching run: the original CSV, the GA's settings, and the
    coordination state for in-flight rematches. A `MatchingSession` owns its
    `Team` and `TeamMembership` rows via reverse FKs (cascading delete).

    Per the single-active-session policy, the application keeps at most one
    row in this table at a time; a new Generate clobbers any prior session
    via `MatchingSession.objects.all().delete()`.
    """

    original_csv = models.TextField(
        help_text="Verbatim CSV uploaded for this session (audit + replay)."
    )
    min_size = models.IntegerField(help_text="Minimum allowed team size.")
    max_size = models.IntegerField(help_text="Maximum allowed team size.")

    # `weights` stores the named dict form, e.g. {"availability": 10, ...}.
    # Use `weights_to_list(session.weights)` (defined at module top) to convert
    # to the canonical ordered list the GA's fitness function consumes.
    weights = models.JSONField(
        help_text="Named-dict form of the fitness weights; see WEIGHT_KEYS for canonical order."
    )
    target_col = models.CharField(
        max_length=50,
        default="teams",
        help_text="Column name in the CSV where team labels are written.",
    )

    # PostgreSQL `jsonb` does not preserve key insertion order, so we cannot
    # rely on `TeamMembership.original_row.keys()` for deterministic CSV export.
    # Storing the canonical column list separately keeps export output stable
    # across DB backends (sqlite, postgres jsonb, etc.).
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

    # `rematch_token` is set when a rematch claims the session and cleared in
    # the worker's `finally`. Non-null means "a rematch is in flight"; the
    # claim path uses an atomic `filter(rematch_token=None).update(rematch_token=...)`
    # to reject double-clicks / reconnects with HTTP 409 already_running.
    rematch_token = models.CharField(
        max_length=36, null=True, blank=True, db_index=True
    )
    rematch_started_at = models.DateTimeField(null=True, blank=True)
    # Flipped by the cancel endpoint. The GA's `on_generation` callback re-reads
    # this field each generation and returns 'stop' to halt PyGAD early.
    rematch_cancel_requested = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"MatchingSession #{self.pk} ({self.created_at:%Y-%m-%d %H:%M})"


class Team(models.Model):
    """A single team within a `MatchingSession`.

    Teams are scoped to their session (cascade-deleted with it) and uniquely
    named within that scope. The `is_locked` flag freezes membership: a locked
    team rejects move-in/move-out attempts in the adjustment API and is
    excluded from the unlocked-subset re-match.
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


class RematchAuditLog(models.Model):
    """Persistent audit row for every rematch attempt.

    We will later hook this into the rematch flow (claim, rejection, terminal outcome,
    cleanup) — each in its OWN transaction, so a rolled-back rematch still leaves an
    audit row behind.
    """

    OUTCOME_RUNNING = "running"
    OUTCOME_SUCCESS = "success"
    OUTCOME_CANCELLED = "cancelled"
    OUTCOME_ERROR = "error"
    OUTCOME_WORKER_DIED = "worker_died"
    OUTCOME_REJECTED = "rejected"  # token claim failed (already_running)
    OUTCOME_DISCONNECT = "client_disconnect"  # generator closed before terminal event
    OUTCOME_NOOP = "noop"  # too few unlocked students
    OUTCOME_CHOICES = [
        (OUTCOME_RUNNING, "Running"),
        (OUTCOME_SUCCESS, "Success"),
        (OUTCOME_CANCELLED, "Cancelled"),
        (OUTCOME_ERROR, "Error"),
        (OUTCOME_WORKER_DIED, "Worker died"),
        (OUTCOME_REJECTED, "Rejected (already running)"),
        (OUTCOME_DISCONNECT, "Client disconnect"),
        (OUTCOME_NOOP, "No-op (degraded)"),  # H2
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

    # Snapshot of the input weights used for this rematch (denormalized so the
    # row stays meaningful even after the session's stored weights mutate).
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

    # Fields `mark_completed` is allowed to mutate. Anything outside this set
    # is set at row creation, never on completion — passing one to
    # `mark_completed` is a programmer typo.
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
        """Flip a running row to its terminal state. Computes duration_ms
        from started_at → now. Will be used by the rematch worker's `finally`.

        Raises ValueError if any kwarg is not in `_MARK_COMPLETED_ALLOWED_FIELDS`
        — prevents silent typo footguns where e.g. `students_move=...` would
        be set on the instance but never persisted.
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
