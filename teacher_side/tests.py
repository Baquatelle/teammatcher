from unittest.mock import MagicMock

from django.test import TestCase

from teacher_side.matcher.utils import get_weights
from teacher_side.models import WEIGHT_KEYS, weights_to_list


class WeightsOrderingTest(TestCase):
    """Guards against silent drift between the form-derived weight list
    (`get_weights`) and the dict-derived weight list (`weights_to_list`).
    Both must agree on the canonical order the GA fitness function unpacks,
    or rematch will misapply weights to the wrong criteria with no error.
    """

    def test_weights_to_list_matches_get_weights(self):
        """weights_to_list and get_weights MUST agree on order."""
        # Build a unique value per field so any swap is detectable.
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
        """Lock the canonical order. If this assertion fails, the GA fitness
        function unpacking in fitness_function.py also needs updating in
        lockstep."""
        self.assertEqual(
            WEIGHT_KEYS,
            [
                'availability', 'commitment', 'job', 'education',
                'age', 'gender', 'experience', 'lead', 'tasks',
            ],
        )
