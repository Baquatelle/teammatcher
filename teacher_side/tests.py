from unittest.mock import MagicMock

from django.test import TestCase

from teacher_side.matcher.utils import get_weights
from teacher_side.models import WEIGHT_KEYS, weights_to_list


class WeightsOrderingTest(TestCase):
    """This test prevents a hidden bug in team matching.

    The GA (genetic algorithm) expects weights in a specific order:
    [availability, commitment, job, education, ...].

    If that order gets mixed up anywhere in the code, the algorithm won't crash—
    it will just apply weights wrong (e.g., using availability weight for job preference).
    This creates incorrect team assignments with no error message.

    This test catches any ordering mismatch by checking that both ways of building
    the weight list produce identical results.
    """

    def test_weights_to_list_matches_get_weights(self):
        """weights_to_list and get_weights must return values in the same order."""
        # Assign a unique number to each field so any swap is detectable.
        sentinel = {k: i + 1 for i, k in enumerate(WEIGHT_KEYS)}
        form = MagicMock()
        form.cleaned_data = {f'weight_{k}': v for k, v in sentinel.items()}

        from_form = get_weights(form)
        from_dict = weights_to_list(sentinel)
        self.assertEqual(
            from_form,
            from_dict,
            'weights_to_list and get_weights disagree on field order — '
            'this would silently misapply weights during rematch.',
        )

    def test_weight_keys_canonical_order(self):
        """Checks that WEIGHT_KEYS hasn't been reordered.
        If this fails, fitness_function.py must also be updated to match."""
        self.assertEqual(
            WEIGHT_KEYS,
            [
                'availability', 'commitment', 'job', 'education',
                'age', 'gender', 'experience', 'lead', 'tasks',
            ],
        )
