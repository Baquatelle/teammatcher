import csv
import hashlib
import io
import json
import logging
import os
import queue
import threading
import uuid

from django.contrib.admin.views.decorators import staff_member_required
from django.db import connection, transaction, DatabaseError
from django.db.models import Count
from django.db.transaction import non_atomic_requests
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST
import pandas as pd

from teacher_side.matcher.genetic_matcher import match, NUM_GENERATIONS
from teacher_side.matcher.utils import get_weights
from teacher_side.matcher.violations import (
    compute_session_violations,
    VIOLATION_ICONS,
    VIOLATION_LABELS,
)
from .forms import UploadFileForm
from .models import (
    CSVGeneration,
    MatchingSession,
    Team,
    TeamMembership,
    RematchAuditLog,
    WEIGHT_KEYS,
    weights_to_list,
)
from student_side.models import StudentProfile

logger = logging.getLogger(__name__)

# One threading.Lock per session, used to prevent two rematches from starting at once.
# In production with PostgreSQL, a DB advisory lock is also used as a second layer.
_REMATCH_LOCKS = {}
_REMATCH_LOCKS_GUARD = threading.Lock()


def _get_session_lock(session_id):
    with _REMATCH_LOCKS_GUARD:
        lk = _REMATCH_LOCKS.get(session_id)
        if lk is None:
            lk = threading.Lock()
            _REMATCH_LOCKS[session_id] = lk
        return lk


def _try_acquire_db_lock(session_id):
    """Try to acquire a database-level lock for this session.
    Returns True on SQLite (the threading lock is enough) or if PostgreSQL grants the lock."""
    vendor = connection.vendor
    if vendor == "postgresql":
        with connection.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", [session_id])
            return bool(cur.fetchone()[0])
    return True  # SQLite path — threading.Lock is sufficient for single-process dev


def _release_db_lock(session_id):
    if connection.vendor == "postgresql":
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", [session_id])


# Longer than the server timeout so a slow-but-running rematch is not wrongly treated as stale.
REMATCH_STALE_AFTER_SECONDS = 180


def _claim_rematch_token(session):
    """Try to reserve the session for a rematch. Returns a token string, or None if one is already running."""
    new_token = uuid.uuid4().hex
    with transaction.atomic():
        # Lock the row so two simultaneous requests can't both pass the token check.
        s = MatchingSession.objects.select_for_update().get(pk=session.pk)
        now = timezone.now()
        stale = (
            s.rematch_started_at is None
            or (now - s.rematch_started_at).total_seconds()
            > REMATCH_STALE_AFTER_SECONDS
        )
        if s.rematch_token and not stale:
            return None
        s.rematch_token = new_token
        s.rematch_started_at = now
        s.rematch_cancel_requested = False
        s.save(
            update_fields=[
                "rematch_token",
                "rematch_started_at",
                "rematch_cancel_requested",
            ]
        )
    return new_token


def _release_rematch_token(session_id, token):
    """Clear the rematch token, but only if it still matches ours."""
    MatchingSession.objects.filter(pk=session_id, rematch_token=token).update(
        rematch_token=None, rematch_started_at=None, rematch_cancel_requested=False
    )


def _audit_rejected(session, reason):
    logger.info(
        "rematch.reject.already_running",
        extra={"session_id": session.pk, "reason": reason},
    )
    RematchAuditLog.objects.create(
        session=session,
        session_pk_snapshot=session.pk,
        token="",
        outcome=RematchAuditLog.OUTCOME_REJECTED,
        weights_snapshot={},
        db_vendor=connection.vendor,
    )


def _run_rematch(session, weights, on_generation=None, cancel_event=None, stats=None):
    """Run the GA and save the results to the database. No threading.
    Tests can call this directly; the SSE view wraps it in a worker thread.

    cancel_event: if set before or during the GA, changes are not saved.
    stats: dict updated with final counts (students_moved, teams_created) for the audit log.
    """
    if stats is None:
        stats = {}

    # Skip immediately if already cancelled (e.g. browser disconnected before GA started).
    if cancel_event is not None and cancel_event.is_set():
        return

    unlocked = list(
        session.memberships.filter(is_locked=False)
        .exclude(team__is_locked=True)
        .select_related("team")
    )
    if not unlocked:
        stats["skipped_reason"] = "too_few_students"
        return

    cols = session.column_order or list(unlocked[0].original_row.keys())
    rows = [{c: m.original_row.get(c, "") for c in cols} for m in unlocked]
    df_subset = pd.DataFrame(rows, columns=cols)
    if "username" not in df_subset.columns:
        raise ValueError("missing_username_column")

    # Wrap on_generation to check the cancel flag after each generation.
    def _wrapped_on_generation(ga):
        if cancel_event is not None and cancel_event.is_set():
            return "stop"  # PyGAD's documented early-stop sentinel
        if on_generation is not None:
            return on_generation(ga)
        return None

    constraints = {"min_size": session.min_size, "max_size": session.max_size}
    logger.info(
        "rematch.ga.start",
        extra={"session_id": session.pk, "n_students": len(unlocked)},
    )
    result_df, result_col, _ = match(
        df_subset,
        session.template_used,
        weights,
        constraints,
        on_generation=_wrapped_on_generation,
    )
    logger.info("rematch.ga.done", extra={"session_id": session.pk})

    # If the user cancelled, do not commit. The GA result is discarded.
    if cancel_event is not None and cancel_event.is_set():
        logger.info(
            "rematch.cancelled",
            extra={"session_id": session.pk, "reason": "skipping_commit"},
        )
        return

    with transaction.atomic():
        # Re-check which teams are locked now, since locks may have changed while the GA ran.
        currently_locked_team_ids = set(
            session.teams.filter(is_locked=True).values_list("id", flat=True)
        )

        # Map the GA's output groups onto existing unlocked teams by sorted name,
        # so teams keep the names the teacher already knows.
        # New Team objects are only created if the GA produces more groups than there are unlocked teams.
        # Skip any team that became locked while the GA was running.
        unlocked_teams = list(
            session.teams.filter(is_locked=False)
            .exclude(pk__in=currently_locked_team_ids)
            .order_by("name")
        )
        ga_buckets = sorted(result_df[result_col].unique())

        team_map = {}
        for i, bucket in enumerate(ga_buckets):
            if i < len(unlocked_teams):
                team_map[bucket] = unlocked_teams[i]
            else:
                existing_names = set(session.teams.values_list("name", flat=True))
                n = len(unlocked_teams) + 1
                while f"Team {n}" in existing_names:
                    n += 1
                team_map[bucket] = Team.objects.create(
                    session=session, name=f"Team {n}"
                )

        username_to_bucket = dict(
            zip(result_df["username"].astype(str).str.strip(), result_df[result_col])
        )

        # Use bulk_update to avoid one query per student.
        # Note: bulk_update skips Django signals, same as bulk_create.
        to_update = []
        teams_created_count = sum(
            1 for i in range(len(ga_buckets)) if i >= len(unlocked_teams)
        )

        for m in unlocked:
            # Don't move students out of a team that got locked while the GA was running.
            if m.team_id and m.team_id in currently_locked_team_ids:
                continue
            bucket = username_to_bucket.get(m.username)
            if bucket is not None and bucket in team_map:
                m.team = team_map[bucket]
                to_update.append(m)
        if to_update:
            TeamMembership.objects.bulk_update(to_update, ["team"], batch_size=200)

        # Pass final counts back to the caller for the audit log.
        stats["students_moved"] = len(to_update)
        stats["teams_created"] = teams_created_count


_PROGRESS_QUEUES: dict = {}
_PROGRESS_QUEUES_LOCK = threading.Lock()


@staff_member_required
def index(request):
    teams = []

    if request.method == 'POST':
        form = UploadFileForm(request.POST, request.FILES)
        if form.is_valid():
            # Read the uploaded file once into a string, then use it for both
            # the CSV snapshot and the dataframe — reading it twice would fail.
            csv_text = request.FILES["file"].read().decode("utf-8")
            try:
                df = pd.read_csv(io.StringIO(csv_text))
                df = df.dropna(how='all')
                if 'username' not in df.columns:
                    raise ValueError('missing_username_column')
            except Exception:
                form.add_error('file',
                               'Could not read file. Please upload a valid comma-separated CSV with a username column.')
                historical_generations = CSVGeneration.objects.order_by('-id')[:5]
                latest_session = MatchingSession.objects.first()
                return render(request, 'allocator/index.html', {
                    'form': form,
                    'teams': teams,
                    'historical_generations': historical_generations,
                    'latest_session': latest_session,
                })

            team_template = form.cleaned_data.get('team_template')
            weights = get_weights(form)
            constraints = {
                'min_size': form.cleaned_data['min_team_size'],
                'max_size': form.cleaned_data['max_team_size'],
            }

            # group
            df_result, target_col, best_fitness = match(df, team_template, weights, constraints)
            logger.info("match.complete", extra={"best_fitness": float(best_fitness)})

            # Save the matching results. Only one session exists at a time:
            # creating a new one deletes the previous one and all its teams.

            # Store weights as a dict keyed by WEIGHT_KEYS (same order as the GA expects).
            weights_dict = {
                k: form.cleaned_data.get(f"weight_{k}", 0) for k in WEIGHT_KEYS
            }

            # Save the column order before matching, because PostgreSQL doesn't preserve dict key order.
            column_order = list(df.columns)

            with transaction.atomic():
                # Delete the previous session (also deletes its teams and memberships).
                MatchingSession.objects.all().delete()

                session = MatchingSession.objects.create(
                    original_csv=csv_text,
                    min_size=constraints["min_size"],
                    max_size=constraints["max_size"],
                    weights=weights_dict,
                    target_col=target_col,
                    column_order=column_order,
                    template_used=team_template,
                )

                # Map username → StudentProfile for the soft display link.
                profile_map = {p.student_id: p for p in StudentProfile.objects.all()}

                # Create one Team per group produced by the GA.
                team_name_set = df_result[target_col].unique()
                team_objects = {}
                for name in team_name_set:
                    team_objects[name] = Team.objects.create(
                        session=session, name=str(name)
                    )

                # Create all memberships in one query.
                # Note: bulk_create skips Django signals — use .save() in a loop if you add signal handlers.
                memberships_to_create = []
                for _, row in df_result.iterrows():
                    original_row = {
                        col: str(val) if pd.notna(val) else ""
                        for col, val in row.items()
                    }
                    username = str(row["username"]).strip()
                    memberships_to_create.append(
                        TeamMembership(
                            session=session,
                            team=team_objects[row[target_col]],
                            username=username,
                            original_row=original_row,
                            profile=profile_map.get(username),
                        )
                    )
                TeamMembership.objects.bulk_create(memberships_to_create)

            # Legacy generation-time audit row.
            csv_content = df_result.to_csv(index=False)
            CSVGeneration.objects.create_generation(
                    csv_data=csv_content,
                    team_size=int((constraints['min_size'] + constraints['max_size']) / 2), # average team size
                    template_used=team_template,
                    student_count=df.shape[0]
            )

            # Redirect to the adjustment view.
            return redirect(reverse("teacher_side:adjust_teams", args=[session.id]))
        else:
            logger.warning(
                "upload_form.invalid",
                extra={"errors": form.errors.get_json_data()},
            )
    else:
        form = UploadFileForm()
        if 'results' in request.session:
            del request.session['results']

    historical_generations = CSVGeneration.objects.order_by('-id')[:5] # ordered to get the most recent

    # Get the current session (there is at most one) to show the "Adjust Teams" link.
    latest_session = MatchingSession.objects.first()

    return render(request, 'allocator/index.html', {
        'form': form,
        'teams': teams,
        'historical_generations': historical_generations,
        'latest_session': latest_session
    })


@staff_member_required
def download_csv(request):
    results = request.session.get('results', [])
    if not results:
        latest_generation = CSVGeneration.objects.first()
        if latest_generation:
            response = HttpResponse(
                content_type='text/csv',
                headers={'Content-Disposition': 'attachment; filename="teams_latest.csv"'},
            )
            response.write(latest_generation.csv_data)
            return response
        return HttpResponse("No results found to download.", content_type='text/plain')

    response = HttpResponse(
        content_type='text/csv',
        headers={'Content-Disposition': 'attachment; filename="teams.csv"'},
    )

    if results:
        fieldnames = list(results[0].keys())
        writer = csv.DictWriter(response, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    return response


@staff_member_required
def download_historical_csv(request, generation_id):
    generation = get_object_or_404(CSVGeneration, id=generation_id)
    response = HttpResponse(
        content_type='text/csv',
        headers={
            'Content-Disposition':
                f'attachment; filename="teams_{generation.generated_at.strftime("%Y%m%d_%H%M%S")}.csv"'
        },
    )
    response.write(generation.csv_data)
    return response


@staff_member_required
def adjust_teams(request, session_id):
    session = get_object_or_404(MatchingSession, pk=session_id)
    teams = list(
        session.teams.annotate(member_count=Count("memberships"))
        .prefetch_related("memberships__profile")
        .order_by("name")
    )

    # Sort teams so "Team 2" comes before "Team 10" (not alphabetic order).
    def _natural_key(team):
        last = team.name.rsplit(" ", 1)[-1] if " " in team.name else team.name
        if last.isdigit():
            return (0, int(last), team.name)
        return (1, team.name.lower(), team.name)

    teams.sort(key=_natural_key)
    unassigned = session.memberships.filter(team=None).select_related("profile")

    # Soft-constraint codes per team. Each team object also gets a pre-built
    # list of {code, icon, label} dicts for the chip row in the template,
    # since Django templates don't have a clean dict-by-key lookup syntax.
    team_violations = compute_session_violations(session)
    for t in teams:
        codes = team_violations.get(t.id, [])
        t.violation_codes = codes
        t.violation_chips = [
            {"code": c, "icon": VIOLATION_ICONS[c], "label": VIOLATION_LABELS[c]}
            for c in codes
            if c != "size"
        ]
    violation_count = sum(1 for codes in team_violations.values() if codes)

    return render(
        request,
        "allocator/adjust.html",
        {
            "session": session,
            "teams": teams,
            "unassigned": unassigned,
            "violation_count": violation_count,
            "violation_labels": VIOLATION_LABELS,
            "violation_icons":  VIOLATION_ICONS,
        },
    )


@staff_member_required
@require_POST
def api_move_student(request, session_id):
    session = get_object_or_404(MatchingSession, pk=session_id)
    try:
        body = json.loads(request.body)
        membership_id = int(body["membership_id"])
        team_id = body.get("team_id")  # None means unassigned
        if team_id is not None:
            team_id = int(team_id)
    except (KeyError, ValueError, json.JSONDecodeError):
        return JsonResponse({"ok": False, "error": "invalid_input"}, status=400)

    membership = get_object_or_404(TeamMembership, pk=membership_id, session=session)

    if membership.is_locked:
        return JsonResponse({"ok": False, "error": "student_locked"}, status=403)
    if membership.team and membership.team.is_locked:
        return JsonResponse({"ok": False, "error": "source_team_locked"}, status=403)

    target_team = None
    if team_id is not None:
        target_team = get_object_or_404(Team, pk=team_id, session=session)
        if target_team.is_locked:
            return JsonResponse(
                {"ok": False, "error": "target_team_locked"}, status=403
            )

    old_team = membership.team
    membership.team = target_team
    membership.save(update_fields=["team"])

    from_count = old_team.memberships.count() if old_team else 0
    to_count = target_team.memberships.count() if target_team else 0

    # Recompute soft-constraint violations after the move so the UI can
    # refresh icons on the affected columns without a full reload.
    # TODO(perf): re-encodes every membership on each drop. Fine up to ~200 students;
    #             if lagging, narrow this to the source/target teams only.
    team_violations = compute_session_violations(session)
    from_violations = team_violations.get(old_team.id, []) if old_team else []
    to_violations = team_violations.get(target_team.id, []) if target_team else []

    return JsonResponse({
        "ok": True,
        "from_count": from_count,
        "to_count": to_count,
        "from_violations": from_violations,
        "to_violations": to_violations,
    })


@staff_member_required
@require_POST
def api_toggle_team_lock(request, session_id, team_id):
    session = get_object_or_404(MatchingSession, pk=session_id)
    team = get_object_or_404(Team, pk=team_id, session=session)
    team.is_locked = not team.is_locked
    team.save(update_fields=["is_locked"])
    return JsonResponse({"locked": team.is_locked})


@staff_member_required
@require_POST
def api_toggle_student_lock(request, session_id, membership_id):
    session = get_object_or_404(MatchingSession, pk=session_id)
    membership = get_object_or_404(TeamMembership, pk=membership_id, session=session)
    membership.is_locked = not membership.is_locked
    membership.save(update_fields=["is_locked"])
    return JsonResponse({"locked": membership.is_locked})


@staff_member_required
@require_POST
def export_csv(request, session_id):
    session = get_object_or_404(MatchingSession, pk=session_id)

    # Violation check
    try:
        body = json.loads(request.body)
        confirmed = bool(body.get("confirmed", False))
    except (ValueError, json.JSONDecodeError):
        confirmed = False

    if not confirmed:
        team_violations = compute_session_violations(session)
        teams_qs = session.teams.annotate(member_count=Count("memberships"))
        violations = []
        for t in teams_qs:
            codes = team_violations.get(t.id, [])
            if codes:
                violations.append({
                    "name": t.name,
                    "count": t.member_count,
                    "min": session.min_size,
                    "max": session.max_size,
                    "codes": codes,
                })
        if violations:
            return JsonResponse({"violations": violations}, status=409)

    # Build CSV from original rows
    memberships = session.memberships.select_related("team").order_by("id")
    if not memberships.exists():
        return JsonResponse({"error": "no_students"}, status=400)

    # Use canonical column order from session
    columns = (
        list(session.column_order)
        if session.column_order
        else list(memberships.first().original_row.keys())
    )
    target_col = session.target_col
    if target_col not in columns:
        columns.append(target_col)

    # Prevent CSV injection: if a cell starts with =, +, -, or @, prepend an apostrophe.
    # Excel/Google Sheets then treats it as plain text instead of a formula.
    # Reference: https://owasp.org/www-community/attacks/CSV_Injection
    DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")

    def sanitize_cell(val):
        s = "" if val is None else str(val)
        # Check stripped value for dangerous prefix, but keep original spacing in output.
        stripped = s.lstrip()
        if stripped and stripped[0] in DANGEROUS_PREFIXES:
            return "'" + s
        return s

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for m in memberships:
        row = dict(m.original_row)
        row[target_col] = m.team.name if m.team else ""
        row = {col: sanitize_cell(row.get(col, "")) for col in columns}
        writer.writerow(row)

    csv_text = output.getvalue()

    # Create audit record
    CSVGeneration.objects.create_generation(
        csv_data=csv_text,
        team_size=int((session.min_size + session.max_size) / 2),
        template_used=session.template_used,
        student_count=memberships.count(),
    )

    timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
    response = HttpResponse(
        csv_text,
        content_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="teams_{timestamp}.csv"'
        },
    )
    return response


# rematch_start: starts the rematch (POST, writes to DB).
# rematch_stream: streams progress to the browser (GET, read-only).
@staff_member_required
@require_POST
def rematch_start(request, session_id):
    """Acquire locks, start the rematch worker thread, and return the token the client needs to open the progress stream."""
    session = get_object_or_404(MatchingSession, pk=session_id)

    # Parse weights (POST body, JSON). Garbage values fall back to saved.
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        body = {}
    weights_dict = {}
    for k in WEIGHT_KEYS:
        try:
            weights_dict[k] = int(body.get(k, session.weights.get(k, 0)))
        except (TypeError, ValueError):
            weights_dict[k] = int(session.weights.get(k, 0))
    weights = weights_to_list(weights_dict)

    # Acquire all three locks (thread, DB advisory, DB token) before starting.
    proc_lock = _get_session_lock(session.id)
    if not proc_lock.acquire(blocking=False):
        _audit_rejected(session, "proc_lock")
        return JsonResponse({"ok": False, "error": "already_running"}, status=409)

    cancel_event = threading.Event()
    progress_q: queue.Queue = queue.Queue()

    db_lock_acquired = False
    try:
        if not _try_acquire_db_lock(session.id):
            _audit_rejected(session, "db_lock")
            proc_lock.release()
            return JsonResponse({"ok": False, "error": "already_running"}, status=409)
        db_lock_acquired = True
        rematch_token = _claim_rematch_token(session)
        if rematch_token is None:
            _release_db_lock(session.id)
            _audit_rejected(session, "token_held")
            proc_lock.release()
            return JsonResponse({"ok": False, "error": "already_running"}, status=409)
    except Exception:
        if db_lock_acquired:
            _release_db_lock(session.id)
        proc_lock.release()
        raise

    audit_row = RematchAuditLog.objects.create(
        session=session,
        session_pk_snapshot=session.pk,
        token=rematch_token,
        outcome=RematchAuditLog.OUTCOME_RUNNING,
        started_at=timezone.now(),
        weights_snapshot=weights_dict,
        db_vendor=connection.vendor,
        worker_pid=os.getpid(),
    )

    with _PROGRESS_QUEUES_LOCK:
        _PROGRESS_QUEUES[(session.id, rematch_token)] = (progress_q, cancel_event)

    stats = {
        "generations_completed": 0,
        "students_moved": 0,
        "teams_created": 0,
        "error_class": "",
        "error_message": "",
    }

    def on_generation(ga):
        stats["generations_completed"] = ga.generations_completed
        progress_q.put(("progress", ga.generations_completed, NUM_GENERATIONS))
        if cancel_event.is_set():
            return "stop"
        return None

    def worker():
        outcome = RematchAuditLog.OUTCOME_SUCCESS
        err = None
        try:
            _run_rematch(
                session,
                weights,
                on_generation=on_generation,
                cancel_event=cancel_event,
                stats=stats,
            )
            if cancel_event.is_set():
                outcome = RematchAuditLog.OUTCOME_CANCELLED
            elif stats.get("skipped_reason") == "too_few_students":
                outcome = RematchAuditLog.OUTCOME_NOOP  # Codex H2
        except Exception as exc:
            outcome = RematchAuditLog.OUTCOME_ERROR
            stats["error_class"] = type(exc).__name__
            stats["error_message"] = str(exc)
            err = str(exc)[:200]
            logger.exception("rematch.thread.error", extra={"session_id": session.pk})
        finally:
            try:
                progress_q.put(("done", outcome, err, stats.copy()))
            except Exception:
                pass
            try:
                connection.close()
            except Exception:
                pass
            audit_row.mark_completed(
                outcome=outcome,
                generations_completed=stats.get("generations_completed", 0),
                students_moved=stats.get("students_moved", 0),
                teams_created=stats.get("teams_created", 0),
                error_class=stats.get("error_class", ""),
                error_message=err or stats.get("error_message", ""),
            )
            _release_rematch_token(session.id, rematch_token)
            _release_db_lock(session.id)
            try:
                proc_lock.release()
            except RuntimeError:
                pass

            # Remove the progress queue from the registry after the stream has had time to finish.
            def _drop_queue():
                with _PROGRESS_QUEUES_LOCK:
                    _PROGRESS_QUEUES.pop((session.id, rematch_token), None)

            threading.Timer(60.0, _drop_queue).start()

    t = threading.Thread(
        target=worker, daemon=True, name=f"rematch-{session.id}-{rematch_token[:6]}"
    )
    t.start()

    return JsonResponse(
        {
            "ok": True,
            "rematch_token": rematch_token,
            "stream_url": reverse("teacher_side:rematch_stream", args=[session.id])
            + f"?t={rematch_token}",
        }
    )


# Streams progress from an ongoing rematch.
# If the token is not found, sends a single "done" frame and closes — safe to call twice.
@staff_member_required
@non_atomic_requests  # do NOT wrap the whole stream in a transaction
def rematch_stream(request, session_id):
    token = (request.GET.get("t") or "").strip()
    if not token:
        return _sse_done_response()

    with _PROGRESS_QUEUES_LOCK:
        entry = _PROGRESS_QUEUES.get((session_id, token))
    if entry is None:
        return _sse_done_response()

    progress_q, cancel_event = entry

    def event_stream():
        try:
            yield f'data: {json.dumps({"progress": 0, "total": NUM_GENERATIONS})}\n\n'
            while True:
                try:
                    item = progress_q.get(timeout=15)
                except queue.Empty:
                    yield ": keep-alive\n\n"  # SSE comment frame, ignored by client
                    continue

                if isinstance(item, tuple) and item[0] == "progress":
                    _, gen, total = item
                    yield f'data: {json.dumps({"progress": gen, "total": total})}\n\n'
                elif isinstance(item, tuple) and item[0] == "done":
                    _, outcome, err, worker_stats = item
                    if outcome == RematchAuditLog.OUTCOME_CANCELLED:
                        yield 'data: {"cancelled":true}\n\n'
                    elif outcome == RematchAuditLog.OUTCOME_ERROR:
                        yield f'data: {json.dumps({"error": err or "rematch_failed"})}\n\n'
                    elif outcome == RematchAuditLog.OUTCOME_NOOP:
                        yield 'data: {"done":true,"noop":true}\n\n'
                    else:
                        yield 'data: {"done":true}\n\n'
                    return
        finally:
            # Signal cancellation when the client disconnects; the worker handles cleanup.
            cancel_event.set()

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache, no-transform"
    response["X-Accel-Buffering"] = "no"
    return response


def _sse_done_response():
    # Set a 24-hour retry delay so the browser doesn't reconnect after we close the stream.
    body = 'retry: 86400000\ndata: {"done":true}\n\n'
    response = StreamingHttpResponse(iter([body]), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache, no-transform"
    response["X-Accel-Buffering"] = "no"
    return response


def _sse_error_response(error_code):
    """Send a single SSE error frame and close the stream.
    The 24-hour retry delay prevents the browser from auto-reconnecting."""
    body = f'retry: 86400000\ndata: {json.dumps({"error": error_code})}\n\n'
    response = StreamingHttpResponse(iter([body]), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache, no-transform"
    response["X-Accel-Buffering"] = "no"
    return response


# Cancel endpoint
@staff_member_required
@require_POST
def rematch_cancel(request, session_id):
    """Set the cancel flag. The worker stops after its current GA generation.
    Returns 200 even if no rematch is currently running."""
    updated = MatchingSession.objects.filter(pk=session_id).update(
        rematch_cancel_requested=True
    )
    if updated == 0:
        return JsonResponse({"ok": False, "error": "not_found"}, status=404)
    # Signal the in-process cancel event so the running GA stops at the next generation
    # without waiting for the SSE stream to disconnect.
    with _PROGRESS_QUEUES_LOCK:
        for (sid, _tok), (_q, ev) in list(_PROGRESS_QUEUES.items()):
            if sid == session_id:
                ev.set()
    return JsonResponse({"ok": True})
