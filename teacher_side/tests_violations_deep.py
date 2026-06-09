"""
Deep unit tests for the Task 6 soft-constraint engine (teacher_side.matcher.violations).

WHY THIS FILE EXISTS
    The existing ViolationComputeTests (tests_regular.py) create memberships WITHOUT a
    StudentProfile. The encoder then treats every such student as "form-less" — full
    availability, all tasks, every other feature 0 — so those tests can only exercise
    `size`, weight-gating, and the empty/unassigned paths. They never drive the
    diversity / std / availability / tasks rules with REAL, varied student data.

    This file fills that gap. Every test below creates real StudentProfile rows (and,
    where needed, Task rows) so the encoder produces genuine values, then asserts the
    exact violation behaviour at the thresholds in violations.py.

    Numeric thresholds were verified independently with NumPy (population std, ddof=0):
        commitment: std >= 1.0   → [minimal, high] = [0, 2] gives std = 1.0 (fires)
        age:        std <  1.0    → [20, 22] gives std = 1.0 (does NOT fire; boundary)
        tasks:      max_agree/n < 0.5 → 3 members each on a different task = 1/3 (fires)

    Scope: violations.py and the encoder column layout it depends on — both
    written/changed in May 2026 for Task 6.

    Place at: teacher_side/tests_violations_deep.py
    Run:      python manage.py test teacher_side.tests_violations_deep
"""

from django.test import TestCase

from student_side.models import StudentProfile, Task
from teacher_side.models import (
    MatchingSession,
    Team,
    TeamMembership,
    WEIGHT_KEYS,
)
from teacher_side.matcher.violations import (
    compute_session_violations,
    VIOLATION_CODES,
    VIOLATION_LABELS,
    VIOLATION_ICONS,
)


# ====================================================================
# Fixtures — REAL profiles so the encoder yields real values
# ====================================================================

def make_session(min_size=2, max_size=3, **weight_overrides):
    """Session with all weights 0 by default; turn on only the criterion under test."""
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


def make_student(
    username,
    *,
    commitment="regular",
    education="bachelor_cs",
    job="industry_it",
    age=25,
    gender="male",
    experience="intermediate",
    lead="support",
    tasks=(),
    monday_slot="Morning",   # gives a default availability slot; override per test
):
    """Create a StudentProfile whose student_id matches the membership username.

    All categorical values use the EXACT keys the encoder's maps expect
    (see encoder.encode_student). Tasks are created active so get_tasks() returns them.
    """
    sp = StudentProfile.objects.create(
        student_id=username,
        commitment=commitment,
        educational_background=education,
        professional_background=job,
        age=age,
        gender=gender,
        experience_level=experience,
        lead_preference=lead,
        availability_monday=(monday_slot or ""),
    )
    for tname in tasks:
        task, _ = Task.objects.get_or_create(name=tname, defaults={"active": True})
        sp.preferred_tasks.add(task)
    return sp


def add_member(session, team, username):
    return TeamMembership.objects.create(
        session=session,
        team=team,
        username=username,
        original_row={"username": username},
    )


def codes_for(session, team):
    return compute_session_violations(session)[team.id]


# ====================================================================
# 1. Module constants are internally consistent
# ====================================================================

class CanonicalConstantsTests(TestCase):
    def test_labels_cover_every_code(self):
        self.assertEqual(set(VIOLATION_LABELS), set(VIOLATION_CODES))

    def test_icons_cover_every_code(self):
        self.assertEqual(set(VIOLATION_ICONS), set(VIOLATION_CODES))

    def test_size_is_first_and_present(self):
        self.assertEqual(VIOLATION_CODES[0], "size")
        # the remaining nine must equal WEIGHT_KEYS (same set)
        self.assertEqual(set(VIOLATION_CODES[1:]), set(WEIGHT_KEYS))


# ====================================================================
# 2. gender / job / education / experience  (unique == 1 → violation)
# ====================================================================

class DiversityViolationTests(TestCase):
    def _two(self, session, team, a_kwargs, b_kwargs):
        make_student("a", **a_kwargs)
        make_student("b", **b_kwargs)
        add_member(session, team, "a")
        add_member(session, team, "b")

    def test_gender_homogeneous_fires(self):
        s = make_session(gender=1)
        t = add_team_(s, "T1")
        self._two(s, t, dict(gender="female"), dict(gender="female"))
        self.assertIn("gender", codes_for(s, t))

    def test_gender_diverse_passes(self):
        s = make_session(gender=1)
        t = add_team_(s, "T1")
        self._two(s, t, dict(gender="female"), dict(gender="male"))
        self.assertNotIn("gender", codes_for(s, t))

    def test_job_homogeneous_fires(self):
        s = make_session(job=1)
        t = add_team_(s, "T1")
        self._two(s, t, dict(job="industry_it"), dict(job="industry_it"))
        self.assertIn("job", codes_for(s, t))

    def test_job_diverse_passes(self):
        s = make_session(job=1)
        t = add_team_(s, "T1")
        self._two(s, t, dict(job="industry_it"), dict(job="industry_business"))
        self.assertNotIn("job", codes_for(s, t))

    def test_education_homogeneous_fires(self):
        s = make_session(education=1)
        t = add_team_(s, "T1")
        self._two(s, t, dict(education="bachelor_cs"), dict(education="bachelor_cs"))
        self.assertIn("education", codes_for(s, t))

    def test_education_diverse_passes(self):
        s = make_session(education=1)
        t = add_team_(s, "T1")
        self._two(s, t, dict(education="bachelor_cs"), dict(education="master_business"))
        self.assertNotIn("education", codes_for(s, t))

    def test_experience_homogeneous_fires(self):
        s = make_session(experience=1)
        t = add_team_(s, "T1")
        self._two(s, t, dict(experience="beginner"), dict(experience="beginner"))
        self.assertIn("experience", codes_for(s, t))

    def test_experience_diverse_passes(self):
        s = make_session(experience=1)
        t = add_team_(s, "T1")
        self._two(s, t, dict(experience="beginner"), dict(experience="advanced"))
        self.assertNotIn("experience", codes_for(s, t))


# ====================================================================
# 3. commitment  (std >= 1.0 → violation)  — verified boundary
# ====================================================================

class CommitmentViolationTests(TestCase):
    def test_minimal_and_high_fires_at_std_1(self):
        """[minimal, high] = [0, 2] → std = 1.0 → fires."""
        s = make_session(commitment=1)
        t = add_team_(s, "T1")
        make_student("a", commitment="minimal")
        make_student("b", commitment="high")
        add_member(s, t, "a")
        add_member(s, t, "b")
        self.assertIn("commitment", codes_for(s, t))

    def test_adjacent_levels_pass_below_threshold(self):
        """[minimal, regular] = [0, 1] → std = 0.5 → does not fire."""
        s = make_session(commitment=1)
        t = add_team_(s, "T1")
        make_student("a", commitment="minimal")
        make_student("b", commitment="regular")
        add_member(s, t, "a")
        add_member(s, t, "b")
        self.assertNotIn("commitment", codes_for(s, t))

    def test_identical_commitment_passes(self):
        s = make_session(commitment=1)
        t = add_team_(s, "T1")
        make_student("a", commitment="high")
        make_student("b", commitment="high")
        add_member(s, t, "a")
        add_member(s, t, "b")
        self.assertNotIn("commitment", codes_for(s, t))


# ====================================================================
# 4. age  (std < 1.0 → violation)  — verified boundary
# ====================================================================

class AgeViolationTests(TestCase):
    def test_same_age_fires(self):
        s = make_session(age=1)
        t = add_team_(s, "T1")
        make_student("a", age=21)
        make_student("b", age=21)
        add_member(s, t, "a")
        add_member(s, t, "b")
        self.assertIn("age", codes_for(s, t))

    def test_one_year_apart_fires_std_half(self):
        """[20, 21] → std = 0.5 < 1.0 → fires (no meaningful spread)."""
        s = make_session(age=1)
        t = add_team_(s, "T1")
        make_student("a", age=20)
        make_student("b", age=21)
        add_member(s, t, "a")
        add_member(s, t, "b")
        self.assertIn("age", codes_for(s, t))

    def test_two_years_apart_passes_at_boundary(self):
        """[20, 22] → std = 1.0, and the rule is strict '< 1.0' → does NOT fire."""
        s = make_session(age=1)
        t = add_team_(s, "T1")
        make_student("a", age=20)
        make_student("b", age=22)
        add_member(s, t, "a")
        add_member(s, t, "b")
        self.assertNotIn("age", codes_for(s, t))


# ====================================================================
# 5. lead  (leaders != 1 → violation; evaluated even for size-1)
# ====================================================================

class LeadViolationTests(TestCase):
    def test_zero_leaders_fires(self):
        s = make_session(lead=1)
        t = add_team_(s, "T1")
        make_student("a", lead="support")
        make_student("b", lead="support")
        add_member(s, t, "a")
        add_member(s, t, "b")
        self.assertIn("lead", codes_for(s, t))

    def test_exactly_one_leader_passes(self):
        s = make_session(lead=1)
        t = add_team_(s, "T1")
        make_student("a", lead="lead")
        make_student("b", lead="support")
        add_member(s, t, "a")
        add_member(s, t, "b")
        self.assertNotIn("lead", codes_for(s, t))

    def test_two_leaders_fires(self):
        s = make_session(min_size=2, max_size=3, lead=1)
        t = add_team_(s, "T1")
        make_student("a", lead="lead")
        make_student("b", lead="lead")
        add_member(s, t, "a")
        add_member(s, t, "b")
        self.assertIn("lead", codes_for(s, t))

    def test_single_member_without_leader_fires(self):
        """lead is one of the few rules that applies at size 1."""
        s = make_session(min_size=1, max_size=3, lead=1)
        t = add_team_(s, "T1")
        make_student("a", lead="support")
        add_member(s, t, "a")
        codes = codes_for(s, t)
        self.assertIn("lead", codes)
        self.assertNotIn("size", codes)  # 1 is within [1, 3]


# ====================================================================
# 6. availability  (no common free slot → violation)
# ====================================================================

class AvailabilityViolationTests(TestCase):
    def test_no_common_slot_fires(self):
        s = make_session(availability=1)
        t = add_team_(s, "T1")
        # disjoint availability: A free Monday morning, B free nowhere on Monday
        a = make_student("a", monday_slot="Morning")
        b = make_student("b", monday_slot="")  # no slot at all
        add_member(s, t, "a")
        add_member(s, t, "b")
        self.assertIn("availability", codes_for(s, t))

    def test_shared_slot_passes(self):
        s = make_session(availability=1)
        t = add_team_(s, "T1")
        make_student("a", monday_slot="Morning")
        make_student("b", monday_slot="Morning")
        add_member(s, t, "a")
        add_member(s, t, "b")
        self.assertNotIn("availability", codes_for(s, t))


# ====================================================================
# 7. tasks  (max agreement < 50% → violation)  — verified boundary
# ====================================================================

class TasksViolationTests(TestCase):
    def test_three_members_all_different_tasks_fires(self):
        """votes per task = 1 each, max=1, 1/3 < 0.5 → fires."""
        s = make_session(min_size=2, max_size=3, tasks=1)
        t = add_team_(s, "T1")
        make_student("a", tasks=["t1"])
        make_student("b", tasks=["t2"])
        make_student("c", tasks=["t3"])
        for u in ("a", "b", "c"):
            add_member(s, t, u)
        self.assertIn("tasks", codes_for(s, t))

    def test_majority_agreement_passes(self):
        """two of three share t1 → max=2, 2/3 >= 0.5 → does not fire."""
        s = make_session(min_size=2, max_size=3, tasks=1)
        t = add_team_(s, "T1")
        make_student("a", tasks=["t1"])
        make_student("b", tasks=["t1"])
        make_student("c", tasks=["t2"])
        for u in ("a", "b", "c"):
            add_member(s, t, u)
        self.assertNotIn("tasks", codes_for(s, t))


# ====================================================================
# 8. Weight-gating with REAL data: weight 0 suppresses each code
# ====================================================================

class WeightGatingTests(TestCase):
    def test_gender_suppressed_when_weight_zero(self):
        s = make_session(gender=0)  # off
        t = add_team_(s, "T1")
        make_student("a", gender="female")
        make_student("b", gender="female")
        add_member(s, t, "a")
        add_member(s, t, "b")
        self.assertNotIn("gender", codes_for(s, t))

    def test_age_suppressed_when_weight_zero(self):
        s = make_session(age=0)
        t = add_team_(s, "T1")
        make_student("a", age=21)
        make_student("b", age=21)
        add_member(s, t, "a")
        add_member(s, t, "b")
        self.assertNotIn("age", codes_for(s, t))


# ====================================================================
# 9. Structural: which codes can appear at size 1 vs size 2
# ====================================================================

class SizeOneEvaluationMatrixTests(TestCase):
    def test_size_one_skips_diversity_and_std_rules(self):
        """With every weight on, a size=1 team skips diversity/std rules.

        The documented size=1 rules are size, availability, lead, and tasks.
        """
        s = make_session(min_size=1, max_size=3, **{k: 1 for k in WEIGHT_KEYS})
        t = add_team_(s, "T1")
        # one member, leader (so 'lead' won't fire), with a free slot (so 'availability'
        # won't fire). With no preferred tasks, only the tasks rule should fire.
        Task.objects.create(name="t1", active=True)
        make_student("solo", lead="lead", monday_slot="Morning")
        add_member(s, t, "solo")
        codes = codes_for(s, t)
        for c in ("commitment", "job", "education", "age", "gender", "experience"):
            self.assertNotIn(c, codes, f"{c} must not be evaluated for a size=1 team")
        self.assertIn("tasks", codes, "tasks is evaluated for size=1 teams")


# ====================================================================
# 10. Output ordering: codes come back in VIOLATION_CODES order
# ====================================================================

class CodeOrderingTests(TestCase):
    def test_multiple_violations_are_ordered_canonically(self):
        # All-same job + all-same gender + zero leaders → job, gender, lead all fire.
        s = make_session(min_size=2, max_size=3, job=1, gender=1, lead=1)
        t = add_team_(s, "T1")
        make_student("a", job="industry_it", gender="male", lead="support")
        make_student("b", job="industry_it", gender="male", lead="support")
        add_member(s, t, "a")
        add_member(s, t, "b")
        codes = codes_for(s, t)
        # The returned codes must be a subsequence of VIOLATION_CODES (canonical order).
        positions = [VIOLATION_CODES.index(c) for c in codes]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(set(codes), {"job", "gender", "lead"})


# --------------------------------------------------------------------
# Local team helper (kept separate so the fixture name does not collide
# with the `add_team` in tests_regular.py if both are imported).
# --------------------------------------------------------------------

def add_team_(session, name, locked=False):
    return Team.objects.create(session=session, name=name, is_locked=locked)
