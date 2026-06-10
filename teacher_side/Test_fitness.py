import numpy as np
from django.test import TestCase

from teacher_side.matcher.fitness_function import calculate_size_penalty
from teacher_side.matcher.fitness_function import make_fitness_func

from teacher_side.matcher.encoder import col_idx

class FitnessTest(TestCase):
    def test_size_penalty_logic(self):
        # Test Perfect Range
        self.assertEqual(calculate_size_penalty(3), 0)
        self.assertEqual(calculate_size_penalty(4), 0)
        self.assertEqual(calculate_size_penalty(5), 0)

        # Test Warning Range
        self.assertEqual(calculate_size_penalty(2), 0.1)
        self.assertEqual(calculate_size_penalty(6), 0.1)

        # Test Violation Range
        self.assertEqual(calculate_size_penalty(1), 0.5)
        self.assertEqual(calculate_size_penalty(7), 0.5)
        self.assertEqual(calculate_size_penalty(10), 0.5)

    def test_weighting_hierarchy_behavior(self):
        mock_matrix = np.zeros((4, 29))
        ci = col_idx(mock_matrix)
        # Scenario: Diversity is high priority (10), size is a soft warning (2)
        weights = [1, 1, 1, 10, 1, 1, 1, 1, 1]  # w_avail, w_commit, w_job, w_edu, w_age, w_gender, w_exp, w_lead, w_tasks

        fitness_func = make_fitness_func(mock_matrix, weights=weights)

        n_tasks = 0  # Assuming no tasks for this test
        idx_commit = 21 + n_tasks  # = 21
        idx_edu = idx_commit + 1  # = 22
        idx_job = idx_commit + 2  # = 23
        idx_age = idx_commit + 3  # = 24
        idx_sex = idx_commit + 4  # = 25
        idx_exp = idx_commit + 5  # = 26
        idx_lead = idx_commit + 6  # = 27

        # 3. Define mock student scenarios
        # Team of 2: DIVERSE (different education, job, etc.)
        mock_matrix[0, idx_edu] = 1
        mock_matrix[0, idx_job] = 1
        mock_matrix[1, idx_edu] = 2 # Different education
        mock_matrix[1, idx_job] = 2 # Different job

        # Team of 4: UNIFORM (all same education/job)
        mock_matrix[2, idx_edu] = 1
        mock_matrix[2, idx_job] = 1
        mock_matrix[3, idx_edu] = 1
        mock_matrix[3, idx_job] = 1

        # Replace these with real student indices from your test profiles
        team_of_2_perfect_diversity = np.array([0, 0], dtype=int)  # only students 0,1
        team_of_4_poor_diversity = np.array([0, 0, 0, 0], dtype=int)  # only students 0,1,2,3

        # Calculate fitness for both scenarios
        score_small_but_perfect = fitness_func(None, team_of_2_perfect_diversity, 0)
        score_normal_but_poor = fitness_func(None, team_of_4_poor_diversity, 0)
        print(f"Score team of 2: {score_small_but_perfect}")
        print(f"Score team of 4: {score_normal_but_poor }")

        # If the hierarchy works, the high-weight diversity should outweigh the size penalty
        self.assertTrue(
            score_small_but_perfect > score_normal_but_poor,
            "High-priority diversity should outweigh the soft size penalty (0.1)"
        )
