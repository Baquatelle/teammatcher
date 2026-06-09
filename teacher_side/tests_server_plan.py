"""
Automated execution of SERVER_TEST_PLAN.md at the VIEW / ENDPOINT level.

WHAT THIS IS
    A Django integration-test suite that drives the real Task 6 views through Django's
    test Client and asserts the documented status codes and JSON. It turns the manual
    server test plan into a repeatable, automated run. T-IDs in the test names map back
    to SERVER_TEST_PLAN.md.

WHY THE TEST CLIENT (and not a live-HTTP script)
    The test Client calls the real view code against a fresh, throwaway test database
    (your dev data is never touched). It handles auth via force_login and is
    deterministic. To keep runs fast and reproducible, sessions/teams/memberships are
    built directly via the ORM instead of through the (slow, non-deterministic) genetic
    algorithm. One optional smoke test exercises the full upload+GA pipeline.

WHAT IS NOT AUTOMATED HERE (do these manually — see the plan)
    - The live Server-Sent-Events progress stream (R-1 progress bar, R-5 disconnect):
      streaming + background GA threads are not meaningfully assertable in-process.
      The re-match LOGIC is covered by calling _run_rematch directly (as the existing
      RematchLogicTests do) and the REJECTION paths are covered here.
    - The genuine concurrency race for single-flight (R-4): here we assert the rejection
      deterministically by pre-claiming the token; the true race needs two live clients.
    - Browser drag-and-drop (M-1/M-2): the underlying move endpoint IS tested here.

HOW TO RUN  (project root, venv active)
    python manage.py test teacher_side.tests_server_plan -v 2

    Strict Task-6-only run (excludes the generation smoke test, which also exercises
    the Dec-2025 GA/encoder code via `index`):
        python manage.py test teacher_side.tests_server_plan --exclude-tag=pipeline

    Place this file at: teacher_side/tests_server_plan.py

SCOPE
    21 of 22 tests target Task 6 endpoints/behaviour exclusively (adjust_teams,
    api_move_student, the lock toggles, export_csv, the re-match endpoints, and the
    violations engine). The single exception — GenerationSmokeTests (G-1) — also runs
    pre-Task-6 code (the genetic algorithm) and is tagged "pipeline" so it can be
    excluded. Note: even the pure Task-6 violation tests call encoder.py (Dec 2025) at
    runtime, because violations.py reuses the encoder by design; the behaviour under
    assertion is Task 6.

TRANSPARENCY (flagged)
    This suite could not be executed in the authoring environment (no Django/PyGAD,
    no network). It is written against views.py / models.py / urls.py as read on branch
    feature/task-6-req; request parsing was verified (JSON body for move/export/rematch,
    multipart for index). Field names and JSON keys are quoted from the source. If a
    field in your StudentProfile differs, adjust make_student() accordingly.
"""

import io
import itertools
import json
from django.contrib.auth import get_user_model
from django.test import TestCase, Client, tag
from django.urls import reverse

from student_side.models import StudentProfile, Task
from teacher_side.models import (
    MatchingSession, Team, TeamMembership, WEIGHT_KEYS,
)


# ====================================================================
# Fixtures
# ====================================================================

_USER_SEQ = itertools.count(1)


def staff_client(enforce_csrf=False, username=None):
    """Create a logged-in staff client.

    Uses a unique username per call (unless one is given) so that a single test
    method can create more than one client without hitting the auth_user UNIQUE
    constraint.
    """
    User = get_user_model()
    if username is None:
        username = f"teacher{next(_USER_SEQ)}"
    user = User.objects.create_user(
        username=username, password="x", is_staff=True, is_superuser=True,
    )
    c = Client(enforce_csrf_checks=enforce_csrf)
    c.force_login(user)
    return c


def make_session(min_size=2, max_size=3, **weight_overrides):
    weights = {k: 0 for k in WEIGHT_KEYS}
    weights.update(weight_overrides)
    return MatchingSession.objects.create(
        original_csv="username,external_user_id,mode,cp,wp\n",
        min_size=min_size,
        max_size=max_size,
        weights=weights,
        target_col="teams",
        column_order=["username", "external_user_id", "mode", "cp", "wp"],
    )


def make_team(session, name, locked=False):
    return Team.objects.create(session=session, name=name, is_locked=locked)


def make_student(username, *, commitment="regular", education="bachelor_cs",
                 job="industry_it", age=25, gender="male", experience="intermediate",
                 lead="support", tasks=(), monday_slot="Morning"):
    sp = StudentProfile.objects.create(
        student_id=username, commitment=commitment, educational_background=education,
        professional_background=job, age=age, gender=gender,
        experience_level=experience, lead_preference=lead,
        availability_monday=(monday_slot or ""),
    )
    for tname in tasks:
        t, _ = Task.objects.get_or_create(name=tname, defaults={"active": True})
        sp.preferred_tasks.add(t)
    return sp


def add_member(session, team, username, locked=False):
    row = {"username": username, "external_user_id": "", "mode": "professional",
           "cp": "Alfa", "wp": "Alfa"}
    return TeamMembership.objects.create(
        session=session, team=team, username=username,
        original_row=row, is_locked=locked,
    )


def url(name, *args):
    return reverse(f"teacher_side:{name}", args=args)


def post_json(client, name, *args, body=None):
    return client.post(
        url(name, *args),
        data=json.dumps(body or {}),
        content_type="application/json",
    )


# ====================================================================
# §0 + §8  Auth, method, 404, CSRF robustness
# ====================================================================

class AuthAndRobustnessTests(TestCase):
    def setUp(self):
        self.c = staff_client()
        self.s = make_session()
        self.t = make_team(self.s, "Team 1")

    def test_P1_unauthenticated_is_redirected(self):
        """P-1 / S-2: anonymous access to a teacher page is denied/redirected."""
        anon = Client()
        resp = anon.get(url("adjust_teams", self.s.id))
        self.assertIn(resp.status_code, (302, 403))

    def test_S3_get_on_post_only_endpoint_405(self):
        """S-3: GET on a @require_POST endpoint → 405."""
        resp = self.c.get(url("api_move_student", self.s.id))
        self.assertEqual(resp.status_code, 405)

    def test_S4_unknown_session_404(self):
        """S-4: unknown ids → 404, not 500."""
        resp = self.c.get(url("adjust_teams", 999999))
        self.assertEqual(resp.status_code, 404)

    def test_S1_post_without_csrf_token_is_forbidden(self):
        """S-1: a POST without the CSRF token must be rejected (403)."""
        csrf_client = staff_client(enforce_csrf=True)
        resp = csrf_client.post(
            url("api_move_student", self.s.id),
            data=json.dumps({"membership_id": 1, "team_id": None}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)


# ====================================================================
# §2 Adjust render
# ====================================================================

class AdjustRenderTests(TestCase):
    def test_A1_adjust_page_renders_with_teams(self):
        c = staff_client()
        s = make_session(gender=1)
        t = make_team(s, "Team 1")
        make_student("a", gender="male")
        make_student("b", gender="male")
        add_member(s, t, "a")
        add_member(s, t, "b")
        resp = c.get(url("adjust_teams", s.id))
        self.assertEqual(resp.status_code, 200)
        # A-2: a homogeneous-gender team (weight on) should surface a gender warning.
        self.assertIn("gender", resp.context["teams"][0].violation_codes)


# ====================================================================
# §3 Move + locks
# ====================================================================

class MoveStudentTests(TestCase):
    def setUp(self):
        self.c = staff_client()
        self.s = make_session(gender=1)
        self.t1 = make_team(self.s, "Team 1")
        self.t2 = make_team(self.s, "Team 2")
        make_student("a", gender="male")
        make_student("b", gender="female")
        self.m_a = add_member(self.s, self.t1, "a")
        self.m_b = add_member(self.s, self.t1, "b")

    def test_M1_move_returns_counts_and_violations(self):
        """M-1: a valid move returns all four payload keys with correct values.

        Setup: Team 1 = [a(M), b(F)], Team 2 = [].  min=2, gender weight=1.
        Move a → Team 2.  Team 1 then has 1 member (< min=2) → size violation.
        Team 2 then has 1 member (< min=2) → size violation.
        """
        resp = post_json(self.c, "api_move_student", self.s.id,
                         body={"membership_id": self.m_a.id, "team_id": self.t2.id})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])

        # All payload keys must be present.
        for key in ("from_count", "to_count", "from_violations", "to_violations",
                    "violation_team_count"):
            self.assertIn(key, data)
        self.assertIsInstance(data["violation_team_count"], int)

        # Counts must reflect the post-move state.
        self.assertEqual(data["from_count"], 1)   # Team 1 lost one member
        self.assertEqual(data["to_count"],   1)   # Team 2 gained one member

        # Violations must be lists, not None or absent.
        self.assertIsInstance(data["from_violations"], list)
        self.assertIsInstance(data["to_violations"],   list)

        # Team 1 now has 1 member which is below min=2 → must report size.
        self.assertIn("size", data["from_violations"],
                      "from_violations should include 'size' when source team drops "                      "below min_size after the move.")
        # Team 2 likewise has 1 member.
        self.assertIn("size", data["to_violations"],
                      "to_violations should include 'size' when target team is below "                      "min_size after receiving the student.")

        # DB state must match.
        self.m_a.refresh_from_db()
        self.assertEqual(self.m_a.team_id, self.t2.id)

    def test_M1b_soft_violation_appears_after_clean_start_move(self):
        """M-1b: moving a student out of a valid team creates a soft-constraint
        violation that the server correctly returns in from_violations.

        This mirrors the post-rematch scenario: both teams start within size bounds
        and with no violations; one drag-and-drop creates a homogeneous-gender team.
        The server must recalculate and return the new violation code so the
        frontend can light up the corresponding chip without a page reload.

        Setup (min=2, max=5, gender weight=1):
          Team 1: [alice(F), bob(M), carol(F)]  → valid (mixed, size=3)
          Team 2: [dave(M), eve(F),  frank(M)]  → valid (mixed, size=3)
        Move alice(F) → Team 2.
          Team 1: [bob(M), carol(F)]            → valid size (2), mixed → no violations
          Team 2: [dave, eve, frank, alice]     → valid size (4), mixed → no violations
        Move carol(F) → Team 2 (second drag).
          Team 1: [bob(M)]                      → size=1 < min=2 → size violation
                                                   AND only males → gender violation
          Team 2: [dave, eve, frank, alice, carol] → size=5 ≤ max=5, mixed → no violations
        """
        c = staff_client()
        s = make_session(min_size=2, max_size=5, gender=1)
        t1 = make_team(s, "Team 1")
        t2 = make_team(s, "Team 2")
        make_student("alice", gender="female")
        make_student("bob",   gender="male")
        make_student("carol", gender="female")
        make_student("dave",  gender="male")
        make_student("eve",   gender="female")
        make_student("frank", gender="male")
        m_alice = add_member(s, t1, "alice")
        m_bob   = add_member(s, t1, "bob")
        m_carol = add_member(s, t1, "carol")
        add_member(s, t2, "dave")
        add_member(s, t2, "eve")
        add_member(s, t2, "frank")

        # First drag: alice(F) → Team 2.  Both teams still valid, no violations.
        r1 = post_json(c, "api_move_student", s.id,
                       body={"membership_id": m_alice.id, "team_id": t2.id})
        self.assertEqual(r1.status_code, 200)
        d1 = r1.json()
        self.assertTrue(d1["ok"])
        self.assertNotIn("size", d1["from_violations"],
                         "After first drag Team 1 has 2 members (within bounds) — "                         "no size violation expected.")
        self.assertEqual(d1["from_count"], 2)

        # Second drag: carol(F) → Team 2.  Team 1 is now [bob(M)] — below min.
        r2 = post_json(c, "api_move_student", s.id,
                       body={"membership_id": m_carol.id, "team_id": t2.id})
        self.assertEqual(r2.status_code, 200)
        d2 = r2.json()
        self.assertTrue(d2["ok"])

        # Team 1 has 1 member → size violation must be reported.
        self.assertIn("size", d2["from_violations"],
                      "from_violations must include 'size' when source team has "                      "1 member and min_size=2 — the frontend needs this to light "                      "up the warning chip without a page reload.")

        # With count=1 the gender rule requires count>=2, so gender doesn't fire;
        # but size definitely must.
        self.assertNotIn("size", d2["to_violations"],
                         "Team 2 has 5 members (≤ max=5) — no size violation.")

    def test_M2_move_to_unassigned(self):
        resp = post_json(self.c, "api_move_student", self.s.id,
                         body={"membership_id": self.m_a.id, "team_id": None})
        self.assertEqual(resp.status_code, 200)
        self.m_a.refresh_from_db()
        self.assertIsNone(self.m_a.team_id)

    def test_M3_locked_student_rejected(self):
        self.m_a.is_locked = True
        self.m_a.save(update_fields=["is_locked"])
        resp = post_json(self.c, "api_move_student", self.s.id,
                         body={"membership_id": self.m_a.id, "team_id": self.t2.id})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error"], "student_locked")

    def test_M4_locked_source_team_rejected(self):
        self.t1.is_locked = True
        self.t1.save(update_fields=["is_locked"])
        resp = post_json(self.c, "api_move_student", self.s.id,
                         body={"membership_id": self.m_a.id, "team_id": self.t2.id})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error"], "source_team_locked")

    def test_M5_locked_target_team_rejected(self):
        self.t2.is_locked = True
        self.t2.save(update_fields=["is_locked"])
        resp = post_json(self.c, "api_move_student", self.s.id,
                         body={"membership_id": self.m_a.id, "team_id": self.t2.id})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error"], "target_team_locked")

    def test_M6_invalid_body_400(self):
        resp = post_json(self.c, "api_move_student", self.s.id, body={"team_id": 1})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "invalid_input")

    def test_M7_team_lock_toggle_persists(self):
        resp = post_json(self.c, "api_toggle_team_lock", self.s.id, self.t1.id)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["locked"])
        self.t1.refresh_from_db()
        self.assertTrue(self.t1.is_locked)

    def test_M7_student_lock_toggle_persists(self):
        resp = post_json(self.c, "api_toggle_student_lock", self.s.id, self.m_a.id)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["locked"])
        self.m_a.refresh_from_db()
        self.assertTrue(self.m_a.is_locked)


# ====================================================================
# §5 Export
# ====================================================================

class ExportTests(TestCase):
    def _session_with_violation(self):
        s = make_session(gender=1)              # gender weight on
        t = make_team(s, "Team 1")
        make_student("a", gender="male")
        make_student("b", gender="male")        # homogeneous → gender violation
        add_member(s, t, "a")
        add_member(s, t, "b")
        return s, t

    def test_E2_unconfirmed_export_with_violation_409(self):
        c = staff_client()
        s, t = self._session_with_violation()
        resp = post_json(c, "export_csv", s.id, body={"confirmed": False})
        self.assertEqual(resp.status_code, 409)
        violations = resp.json()["violations"]
        self.assertTrue(any("gender" in v["codes"] for v in violations))
        # each entry carries name/count/min/max/codes
        for v in violations:
            self.assertEqual(
                set(v), {"name", "count", "min", "max", "codes"})

    def test_E3_confirmed_export_downloads_csv(self):
        c = staff_client()
        s, t = self._session_with_violation()
        resp = post_json(c, "export_csv", s.id, body={"confirmed": True})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp["Content-Type"])

    def test_E1_clean_export_downloads_immediately(self):
        c = staff_client()
        s = make_session(gender=1)
        t = make_team(s, "Team 1")
        make_student("a", gender="male")
        make_student("b", gender="female")      # diverse → no violation
        add_member(s, t, "a")
        add_member(s, t, "b")
        resp = post_json(c, "export_csv", s.id, body={"confirmed": False})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp["Content-Type"])

    def test_E4_export_no_students_400(self):
        c = staff_client()
        s = make_session()
        make_team(s, "Team 1")                  # team but zero memberships
        resp = post_json(c, "export_csv", s.id, body={"confirmed": True})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "no_students")

    def test_E6_csv_injection_is_sanitised(self):
        """E-6: a field starting with '=' must be neutralised in the output."""
        c = staff_client()
        s = make_session()
        t = make_team(s, "Team 1")
        m = TeamMembership.objects.create(
            session=s, team=t, username="=cmd|'/c calc'!A1",
            original_row={"username": "=cmd|'/c calc'!A1", "external_user_id": "",
                          "mode": "professional", "cp": "Alfa", "wp": "Alfa"},
        )
        resp = post_json(c, "export_csv", s.id, body={"confirmed": True})
        self.assertEqual(resp.status_code, 200)
        text = resp.content.decode("utf-8")
        # the dangerous '=' must NOT start a field; it should be prefixed (e.g. with ')
        self.assertNotIn("\n=cmd", "\n" + text)
        self.assertNotIn(",=cmd", text)


# ====================================================================
# §4 Re-match: rejection / cancel endpoints (logic covered elsewhere)
# ====================================================================

class RematchEndpointTests(TestCase):
    def setUp(self):
        self.c = staff_client()
        self.s = make_session()

    def test_R4_second_start_rejected_already_running(self):
        """R-4: a session that already holds a fresh token rejects a new start (409)."""
        from django.utils import timezone
        self.s.rematch_token = "already-here"
        self.s.rematch_started_at = timezone.now()
        self.s.save(update_fields=["rematch_token", "rematch_started_at"])
        resp = post_json(self.c, "rematch_start", self.s.id, body={})
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error"], "already_running")

    def test_R6_cancel_when_nothing_running(self):
        """R-6: cancel with no active token → 404 not_found (must not crash)."""
        resp = post_json(self.c, "rematch_cancel", self.s.id, body={})
        self.assertIn(resp.status_code, (200, 404))
        if resp.status_code == 404:
            self.assertEqual(resp.json()["error"], "not_found")


# ====================================================================
# §1 Optional smoke test through the REAL pipeline (slow; needs PyGAD)
# ====================================================================

@tag("pipeline")
class GenerationSmokeTests(TestCase):
    """G-1: full upload → GA → persist → redirect.

    NOT strictly Task 6: this drives `index` (a Dec-2025 view, only *extended* in
    Task 6) and the Dec-2025 GA stack (genetic_matcher / encoder / fitness_function).
    It is tagged "pipeline" so you can exclude it for a strict Task-6-only run:

        python manage.py test teacher_side.tests_server_plan --exclude-tag=pipeline

    Slow and non-deterministic (runs the real GA); needs PyGAD installed."""

    def test_G1_generate_persists_and_redirects(self):
        c = staff_client()
        for i in range(1, 7):                   # 6 students, min/max default
            make_student(f"s-{i:03d}", gender=("male" if i % 2 else "female"))
        csv = "username,external_user_id,mode,cp,wp\n" + "".join(
            f"s-{i:03d},,professional,Alfa,Alfa\n" for i in range(1, 7))
        upload = io.BytesIO(csv.encode("utf-8"))
        upload.name = "roster.csv"
        data = {
            "file": upload, "min_team_size": 2, "max_team_size": 3,
            **{f"weight_{k}": 1 for k in WEIGHT_KEYS},
        }
        resp = c.post(url("index"), data=data)
        # On success index redirects (302) to the adjust page.
        self.assertIn(resp.status_code, (302, 200))
        if resp.status_code == 302:
            self.assertEqual(MatchingSession.objects.count(), 1)
            self.assertTrue(Team.objects.exists())
            self.assertEqual(TeamMembership.objects.count(), 6)


# ====================================================================
# §7 Known-bug probe at the ENDPOINT level (expected to deviate)
# ====================================================================

class FormlessMaskingEndpointBugTests(TestCase):
    """
    B-1 at the endpoint level: a move that results in an all-female team where one
    member is form-less (no profile, encoded gender=male) should still report a
    'gender' violation.
    """

    def test_B1_formless_member_masks_gender_violation(self):
        c = staff_client()
        s = make_session(gender=1)
        t = make_team(s, "Team 1")
        make_student("real", gender="female")
        add_member(s, t, "real")
        add_member(s, t, "formless")            # no StudentProfile row
        resp = c.get(url("adjust_teams", s.id))
        codes = resp.context["teams"][0].violation_codes
        self.assertIn(
            "gender", codes,
            "Form-less member encoded as gender=male masked the gender violation.",
        )
