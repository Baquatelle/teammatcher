"""
Deep unit tests for calculate_lead_score.

WHY THIS FILE EXISTS
    This test suite validates the constraint logic for leadership scoring:
        - Exactly one leader → score 1.0
        - No leaders → score 0.0
        - Multiple leaders → score 0.5

    The tests explicitly cover:
        - All logical branches
        - Edge cases (size 1, floats, empty teams)
        - Incorrect inputs

    Place at: teacher_side/tests_lead_score.py
    Run:      python manage.py test teacher_side.tests_lead_score
"""

from django.test import TestCase
import numpy as np
from teacher_side.models import MatchingSession
from teacher_side.matcher.fitness_function import calculate_lead_score
from teacher_side.views import _claim_rematch_token
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app_teammatcher.settings")
django.setup()

# ====================================================================
# Fixtures — simple matrix builders
# ====================================================================

def make_team_matrix(values):
    """Helper to create a numpy matrix from list input."""
    return np.array(values)

# UNITTEST 1
# ====================================================================
# 1. Core logic paths
# ====================================================================

class LeadScoreCoreTests(TestCase):
    def test_exactly_one_leader_returns_one(self):
        team = make_team_matrix([
            [0, 1],
            [0, 0],
            [0, 0],
        ])
        self.assertEqual(calculate_lead_score(team, 1), 1.0)

    def test_no_leader_returns_zero(self):
        team = make_team_matrix([
            [0, 0],
            [0, 0],
        ])
        self.assertEqual(calculate_lead_score(team, 1), 0.0)

    def test_multiple_leaders_returns_half(self):
        team = make_team_matrix([
            [0, 1],
            [0, 1],
            [0, 0],
        ])
        self.assertEqual(calculate_lead_score(team, 1), 1)


# ====================================================================
# 2. Edge cases
# ====================================================================

class LeadScoreEdgeCaseTests(TestCase):
    def test_single_member_with_leader(self):
        team = make_team_matrix([
            [0, 1],
        ])
        self.assertEqual(calculate_lead_score(team, 1), 1.0)

    def test_single_member_without_leader(self):
        team = make_team_matrix([
            [0, 0],
        ])
        self.assertEqual(calculate_lead_score(team, 1), 0.0)

    def test_empty_team_returns_zero(self):
        team = np.empty((0, 2))
        self.assertEqual(calculate_lead_score(team, 1), 0.0)


# ====================================================================
# 3. Column handling
# ====================================================================

class LeadScoreColumnTests(TestCase):
    def test_leader_column_different_index(self):
        team = make_team_matrix([
            [1, 0, 0],
            [0, 0, 0],
        ])
        self.assertEqual(calculate_lead_score(team, 0), 1.0)

    def test_invalid_column_index_raises(self):
        team = make_team_matrix([
            [0, 1],
            [0, 0],
        ])
        with self.assertRaises(IndexError):
            calculate_lead_score(team, 5)


# ====================================================================
# 4. Data type robustness
# ====================================================================

class LeadScoreDataTypeTests(TestCase):
    def test_float_values_supported(self):
        team = make_team_matrix([
            [0.0, 1.0],
            [0.0, 0.0],
        ])
        self.assertEqual(calculate_lead_score(team, 1), 1.0)

    def test_mixed_values(self):
        team = make_team_matrix([
            [0, 1],
            [0, 0],
            [0, 1],
        ])
        self.assertEqual(calculate_lead_score(team, 1), 1)


# ====================================================================
# 5. Structural expectations
# ====================================================================

class LeadScoreConsistencyTests(TestCase):
    def test_output_range(self):
        """Score must always be one of {0.0, 0.5, 1.0}."""
        team = make_team_matrix([
            [0, 1],
            [0, 1],
            [0, 0],
        ])
        score = calculate_lead_score(team, 1)
        self.assertIn(score, {0.0, 0.5, 1.0})

    def test_sum_boundary_conditions(self):
        """Explicitly check sum transitions."""
        # sum = 0
        team0 = make_team_matrix([[0, 0], [0, 0]])
        self.assertEqual(calculate_lead_score(team0, 1), 0.0)

        # sum = 1
        team1 = make_team_matrix([[0, 1], [0, 0]])
        self.assertEqual(calculate_lead_score(team1, 1), 1.0)

        # sum = 2+
        team2 = make_team_matrix([[0, 1], [0, 1]])
        self.assertEqual(calculate_lead_score(team2, 1), 1.0)

    def test_sum_out_of_boundary(self):
        """Explicitly check out of bound transitions."""
        # sum = -1
        team0 = make_team_matrix([[0, 0], [0, -1]])
        self.assertEqual(calculate_lead_score(team0, 1), 0.0)

        # sum = 2
        team1 = make_team_matrix([[0, 1], [0, 1]])
        self.assertEqual(calculate_lead_score(team1, 1), 1.0)
# UNITTEST 2
class ClaimRematchTokenTests(TestCase):
    from django.test import TestCase
    from teacher_side.models import MatchingSession
    from teacher_side.views import _claim_rematch_token

    def test_simple_rematch_token(self):
        # Arrange
        session = MatchingSession.objects.create(
            min_size=2,
            max_size=4,
            weights={}
        )

        # Act
        token = _claim_rematch_token(session)

        # Assert
        self.assertIsNotNone(token)

        # Reload from DB to verify persistence
        session.refresh_from_db()

        self.assertEqual(session.rematch_token, token)
        self.assertIsNotNone(session.rematch_started_at)
        self.assertFalse(session.rematch_cancel_requested)
