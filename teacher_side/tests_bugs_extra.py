"""
NEW bug-demonstrating tests for the Task 6 modules — beyond the four already in
teacher_side/tests.py.

METHODOLOGY (same as the existing bug suite)
    Each test asserts the *correct* behaviour and is marked @unittest.expectedFailure.
    Against the current code it FAILS on purpose — that failure is the proof of the
    defect — so `python manage.py test` stays GREEN and reports them as
    "expected failures". When a bug is fixed, the test becomes an "unexpected success":
    your signal to drop the decorator and promote it to a regression test.

TRANSPARENCY (flagged)
    These tests could not be executed in the authoring environment (Django and PyGAD
    were not installed and the network was offline). They were written against the
    source of teacher_side/matcher/violations.py and encoder.py as read on branch
    feature/task-6-req. Each docstring states precisely why the assertion fails today.
    Run them locally to confirm.

    Place at: teacher_side/tests_bugs_extra.py
    Run:      python manage.py test teacher_side.tests_bugs_extra
"""

import json
import unittest

from django.test import TestCase

from student_side.models import StudentProfile, Task
from teacher_side.models import MatchingSession, Team, TeamMembership, WEIGHT_KEYS
from teacher_side.matcher.violations import compute_session_violations


# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------

def make_session(min_size=2, max_size=3, **weight_overrides):
    weights = {k: 0 for k in WEIGHT_KEYS}
    weights.update(weight_overrides)
    return MatchingSession.objects.create(
        original_csv="username\n",
        min_size=min_size,
        max_size=max_size,
        weights=weights,
        target_col="teams",
        column_order=["username"],
    )


def add_team(session, name):
    return Team.objects.create(session=session, name=name)


def add_member(session, team, username):
    """Membership WITHOUT a StudentProfile is encoded 'form-less' by the encoder
    (full availability + all tasks + every other feature 0)."""
    return TeamMembership.objects.create(
        session=session, team=team, username=username,
        original_row={"username": username},
    )


def make_real_student(username, **fields):
    defaults = dict(
        commitment="regular", educational_background="bachelor_cs",
        professional_background="industry_it", age=25, gender="male",
        experience_level="intermediate", lead_preference="support",
        availability_monday="Morning",
    )
    defaults.update(fields)
    return StudentProfile.objects.create(student_id=username, **defaults)


def codes_for(session, team):
    return compute_session_violations(session)[team.id]


# ====================================================================
# BUG 5 — Form-less students corrupt diversity / std violations
# ====================================================================

class FormlessProfileCorruptsViolationsBugTests(TestCase):
    """
    WHY THIS IS A BUG
        violations.py reuses encoder.prepare_data. For a student who appears in the
        roster but never filled the profile form, prepare_data returns a vector with
        availability and tasks set to 1 and EVERY OTHER FEATURE LEFT AT 0 — i.e.
        gender = 0 (== "male"), age = 0, job = 0 ("none"), education = 0, etc.

        Those zeros are an "unknown" sentinel, but the violation rules treat them as a
        CONCRETE category value. As a result a form-less student silently changes the
        outcome of the diversity / std checks:
          - gender/job/education/experience: the sentinel 0 looks like a distinct
            value, MASKING a real homogeneity violation (fake diversity).
          - age: the sentinel age 0 fabricates a huge standard deviation, suppressing
            the "no age spread" violation even though only one real age is known.

        This was introduced when Task 6's violations module reused an encoder default
        that had only ever been designed for the GA fitness function. The violation
        result should not depend on a fabricated attribute value for a student whose
        attributes are unknown.

    Both tests below assert the conservative correct behaviour: an unknown student
    provides NO evidence of diversity/spread, so a team whose only KNOWN values are
    identical must still be flagged.
    """

    @unittest.expectedFailure
    def test_formless_student_masks_gender_homogeneity(self):
        s = make_session(gender=1)
        t = add_team(s, "T1")
        make_real_student("real", gender="female")  # only known gender = female
        add_member(s, t, "real")
        add_member(s, t, "formless")  # no profile → encoded gender = 0 ("male")
        # CORRECT: the only evidence is "all female" → homogeneity risk should stand.
        self.assertIn(
            "gender", codes_for(s, t),
            "A form-less student (encoded gender=male) silently removed the gender "
            "homogeneity warning, despite providing no real evidence of diversity.",
        )

    def test_formless_student_fabricates_age_diversity(self):
        s = make_session(age=1)
        t = add_team(s, "T1")
        make_real_student("real", age=21)  # only known age = 21
        add_member(s, t, "real")
        add_member(s, t, "formless")  # no profile → encoded age = 0
        # CORRECT: a single known age is "no spread"; the sentinel 0 must not count
        # as a real, much-younger teammate.
        self.assertIn(
            "age", codes_for(s, t),
            "A form-less student (encoded age=0) fabricated a large age spread and "
            "suppressed the 'no age diversity' warning.",
        )


# ====================================================================
# BUG 6 — `tasks` is not evaluated for size-1 teams (spec ↔ code drift)
# ====================================================================

class TasksSizeOneDocDriftBugTests(TestCase):
    """
    WHY THIS IS A BUG
        Appendix C.2 of req/task6.md states, verbatim:
            "... only `size`, `lead`, `availability`, `tasks` apply to size-1 teams."
        But violations.py gates the tasks rule behind `count >= 2`:
            if count >= 2 and ci.n_tasks > 0 and weight('tasks') > 0:
                ...
        So for a single-member team the tasks rule is never evaluated — contradicting
        the documented contract. For a lone member who prefers NO task, the rule (if it
        ran) would compute max_agreement / count = 0 / 1 = 0 < 0.5 and fire.

        Either the documentation or the code is wrong; they must agree. This test
        encodes the DOCUMENTED behaviour, so it fails against the current code.
        (See C2_RECONCILIATION.md for the proposed one-line fix on either side.)

    NOTE (flagged): this is a specification-vs-implementation inconsistency, not a
    crash. The "correct" side is a team decision — the test simply pins the documented
    contract so the drift is visible.
    """

    @unittest.expectedFailure
    def test_tasks_evaluated_for_single_member_team(self):
        s = make_session(min_size=1, max_size=3, tasks=1)
        t = add_team(s, "T1")
        Task.objects.create(name="t1", active=True)  # n_tasks > 0
        # A real lone student who prefers no task at all.
        make_real_student("solo", lead="lead")  # lead=lead so 'lead' won't appear
        add_member(s, t, "solo")
        # CORRECT per C.2: tasks applies to size-1 teams → empty preferences violate.
        self.assertIn(
            "tasks", codes_for(s, t),
            "C.2 says `tasks` applies to size-1 teams, but the code requires count>=2, "
            "so the rule is silently skipped.",
        )


# ====================================================================
# BUG 7 — A blank age (age=None) is encoded as 0, corrupting the age rule
#         even for students who DID fill in their profile
# ====================================================================

class BlankAgeSentinelBugTests(TestCase):
    """
    WHY THIS IS A BUG
        StudentProfile.age is nullable (`null=True, blank=True`). The encoder does:
            if student.age is not None:
                student_encoded[offset + 3] = student.age
        so a student who filled in everything EXCEPT age is encoded with age = 0.

        This is broader than the form-less bug (BUG 5): it hits real, active students
        who simply left the age field blank. In the age rule, that sentinel 0 behaves
        like a real teammate aged 0, fabricating a large standard deviation and
        SUPPRESSING the "no age spread" warning for a team that, on the known data,
        has no spread at all.

        Correct behaviour: an unknown age provides no evidence of spread; a team whose
        only KNOWN age is shared should still be flagged when age weight > 0.

    NOTE (flagged): root cause is shared with BUG 5 (sentinel-0 encoding reused by the
    violation checker). Fixing it well means representing "unknown" distinctly from a
    real 0 — a team decision, since it also affects GA scoring. This test pins the
    conservative contract so the defect is visible.
    """

    def test_blank_age_fabricates_spread_and_suppresses_warning(self):
        s = make_session(age=1)
        t = add_team(s, "T1")
        # Two students who BOTH filled the form; one left age blank.
        make_real_student("known", age=21)
        make_real_student("blank", age=None)  # encoded as 0
        add_member(s, t, "known")
        add_member(s, t, "blank")
        # CORRECT: only one known age (21) → no spread → age warning should fire.
        self.assertIn(
            "age", codes_for(s, t),
            "A blank age (encoded 0) fabricated an age spread and suppressed the "
            "'no age diversity' warning, despite the student having a profile.",
        )


# ====================================================================
# BUG 10 — api_move_student returns HTTP 500 on a non-numeric team_id
# ====================================================================

class MoveTeamIdValidationBugTests(TestCase):
    """
    WHY THIS IS A BUG
        In api_move_student, `membership_id` is validated inside a try/except that
        returns 400 'invalid_input'. But `team_id` is only parsed later, OUTSIDE that
        guard:
            target_team = get_object_or_404(Team, pk=int(team_id), session=session)
        A body like {"membership_id": <id>, "team_id": "abc"} reaches int("abc"),
        which raises an uncaught ValueError → HTTP 500 instead of a clean 400.

        Malformed client input should never produce a 500. This test asserts the
        correct behaviour (400), so it fails against the current code.

    NOTE: needs auth + a real membership; no genetic algorithm involved, so this runs
    fast and deterministically.
    """

    def test_non_numeric_team_id_returns_400_not_500(self):
        from django.contrib.auth import get_user_model
        from django.test import Client
        from django.urls import reverse

        User = get_user_model()
        User.objects.create_user("t", password="x", is_staff=True, is_superuser=True)
        c = Client()
        c.force_login(User.objects.get(username="t"))

        s = make_session()
        t = add_team(s, "Team 1")
        m = add_member(s, t, "a")

        resp = c.post(
            reverse("teacher_side:api_move_student", args=[s.id]),
            data=json.dumps({"membership_id": m.id, "team_id": "abc"}),
            content_type="application/json",
        )
        self.assertEqual(
            resp.status_code, 400,
            "A non-numeric team_id raised an uncaught ValueError → HTTP 500 instead "
            "of a clean 400 invalid_input.",
        )
