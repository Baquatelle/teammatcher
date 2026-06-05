"""Soft-constraint violation checks for the team adjustment view.

Each criterion has a corresponding rule that fires only when its weight in
session.weights is > 0. Size is the exception — it is always evaluated, since
it was the original soft constraint and is not gated by a weight.

The rules correspond to the GA fitness sub-functions in fitness_function.py:
violations are flagged when a criterion is poorly satisfied according to the
thresholds below (often when the GA sub-score would be very low). Column index layout is shared via encoder.col_idx().
Output is deterministic and ordered to match VIOLATION_CODES.
"""

from collections import defaultdict

import numpy as np
import pandas as pd

from teacher_side.matcher.encoder import col_idx, prepare_data


# Canonical ordering for output codes. Mirrors WEIGHT_KEYS with 'size' prepended.
VIOLATION_CODES = (
    'size', 'availability', 'commitment', 'job', 'education',
    'age', 'gender', 'experience', 'lead', 'tasks',
)

VIOLATION_LABELS = {
    'size':         'Team size outside allowed range',
    'availability': 'No time slot when everyone is free',
    'commitment':   'Members have very different commitment levels',
    'job':          'All members from same professional background',
    'education':    'All members from same educational background',
    'age':          'No age diversity',
    'gender':       'All members same gender',
    'experience':   'All members same experience level',
    'lead':         'Need exactly one leader (currently zero or multiple)',
    'tasks':        'No task has majority agreement',
}

# Bootstrap icon names (without the `bi-` prefix).
VIOLATION_ICONS = {
    'size':         'exclamation-triangle-fill',
    'availability': 'calendar-x',
    'commitment':   'battery-half',
    'job':          'briefcase',
    'education':    'mortarboard',
    'age':          'hourglass-split',
    'gender':       'gender-ambiguous',
    'experience':   'award',
    'lead':         'person-badge',
    'tasks':        'list-check',
}


def _size_violates(count, min_size, max_size):
    return not (min_size <= count <= max_size)


def compute_session_violations(session):
    """Return ``{team_id: [codes...]}`` for every team in the session.

    Codes for each team are ordered according to ``VIOLATION_CODES``. Teams
    with no violations map to an empty list. Memberships with ``team=None``
    (the unassigned pool) do not contribute to any team's violations.
    """
    teams = list(session.teams.all())
    if not teams:
        return {}

    min_size = session.min_size
    max_size = session.max_size
    weights = session.weights or {}

    def weight(key):
        return int(weights.get(key, 0) or 0)

    memberships = list(session.memberships.order_by('id'))

    # Empty session shortcut: only size can fire, so we skip the encoder.
    if not memberships:
        size_only = _size_violates(0, min_size, max_size)
        return {t.id: (['size'] if size_only else []) for t in teams}

    # Build a DataFrame in membership order so encoded row i maps to memberships[i].
    cols = (
        list(session.column_order)
        if session.column_order
        else list(memberships[0].original_row.keys())
    )
    if 'username' not in cols:
        cols.append('username')
    rows = []
    for m in memberships:
        row = {c: m.original_row.get(c, '') for c in cols}
        # Use the canonical username so prepare_data joins correctly with StudentProfile.
        row['username'] = m.username
        rows.append(row)
    df = pd.DataFrame(rows, columns=cols)

    encoded = prepare_data(df)

    ci = col_idx(encoded)

    team_to_indices = defaultdict(list)
    for i, m in enumerate(memberships):
        if m.team_id is not None:
            team_to_indices[m.team_id].append(i)

    result = {}
    for team in teams:
        indices = team_to_indices.get(team.id, [])
        count = len(indices)
        codes = []

        # 'size' — always evaluated, no weight gate.
        if _size_violates(count, min_size, max_size):
            codes.append('size')

        if count >= 1:
            team_matrix = encoded[indices]

            # availability needs at least 1 member; "no perfect-overlap slot".
            if weight('availability') > 0:
                avail_matrix = team_matrix[:, 0:21]
                team_overlaps = np.sum(avail_matrix, axis=0)
                perfect_slots = int(np.sum(team_overlaps == count))
                if perfect_slots == 0:
                    codes.append('availability')

            # Diversity / homogeneity rules require at least 2 members.
            if count >= 2 and weight('commitment') > 0:
                if float(np.std(team_matrix[:, ci.idx_commit])) >= 1.0:
                    codes.append('commitment')

            if count >= 2 and weight('job') > 0:
                if len(np.unique(team_matrix[:, ci.idx_job])) == 1:
                    codes.append('job')

            if count >= 2 and weight('education') > 0:
                if len(np.unique(team_matrix[:, ci.idx_edu])) == 1:
                    codes.append('education')

            if count >= 2 and weight('age') > 0:
                known_ages = team_matrix[:, ci.idx_age]
                # Exclude both the form-less sentinel (0) and NaN produced when a
                # real student left the age field blank (encoder writes np.nan for
                # age=None).  np.nan != 0 is True, so the old filter let NaN through;
                # np.std([real_age, nan]) == nan; nan < 1.0 == False, silently
                # suppressing the violation even when only one real age is known.
                known_ages = known_ages[np.isfinite(known_ages) & (known_ages != 0)]
                if len(known_ages) >= 2 and float(np.std(known_ages)) < 1.0:
                    codes.append('age')
                elif len(known_ages) < 2:
                    codes.append('age')

            if count >= 2 and weight('gender') > 0:
                if len(np.unique(team_matrix[:, ci.idx_sex])) == 1:
                    codes.append('gender')

            if count >= 2 and weight('experience') > 0:
                if len(np.unique(team_matrix[:, ci.idx_exp])) == 1:
                    codes.append('experience')

            # 'lead' applies even to single-member teams: 0 or 2+ leaders both violate.
            if weight('lead') > 0:
                if int(np.sum(team_matrix[:, ci.idx_lead])) != 1:
                    codes.append('lead')

            # 'tasks' needs >=2 members and a non-empty task pool.
            if count >= 2 and ci.n_tasks > 0 and weight('tasks') > 0:
                tasks_matrix = team_matrix[:, ci.tasks_start:ci.tasks_start + ci.n_tasks]
                if tasks_matrix.size > 0:
                    max_agreement = int(np.max(np.sum(tasks_matrix, axis=0)))
                    if max_agreement / count < 0.5:
                        codes.append('tasks')

        result[team.id] = codes

    return result
