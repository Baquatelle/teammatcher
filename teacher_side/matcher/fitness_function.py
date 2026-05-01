""" Custom fitness functions for genetic algorithm matcher combining various criteria """


import numpy as np


# Reference: "A Genetic Algorithm for Identifying Overlapping Communities" (Social Networking, 2013).
# Highlights finding intersection sets, similar to time intersections.
def calculate_availability_score(team_matrix, current_n):
    """
    Calculates the availability score for a team based on overlapping free slots.
    5 common slots = perfect score (1.0)

    Args:
        - team_matrix (np.ndarray): matrix of team members' availability (first 21 columns)
        - current_n (int): number of members in the team
    Returns:
        - float: availability score between 0.0 and 1.0
    """
    if current_n == 0:
        return 0.0

    avail_matrix = team_matrix[:, 0:21]
    # Sum columns: if sum == current_n, everyone is free in that slot
    team_overlaps = np.sum(avail_matrix, axis=0)
    perfect_slots = np.sum(team_overlaps == current_n)

    return min(float(perfect_slots / 5.0), 1.0)

def calculate_tasks_score(team_matrix, n_tasks, tasks_start, tasks_end):
    """
    Finds Maximum agreement of preferred tasks within the team
    Args:
        - team_matrix (np.ndarray): matrix of team members' data
        - n_tasks (int): total number of tasks
        - tasks_start (int): starting index of tasks in the matrix
        - tasks_end (int): ending index of tasks in the matrix
    Returns:
        - float: tasks coverage score between 0.0 and 1.0
    """
    current_n = team_matrix.shape[0]
    if current_n <= 1 or n_tasks == 0:
        return 0.0

    tasks_matrix = team_matrix[:, tasks_start:tasks_end]
    votes_per_task = np.sum(tasks_matrix, axis=0)
    max_agreement = np.max(votes_per_task)

    return float(max_agreement / current_n)


# Reference: "Homogeneity versus Heterogeneity in Team Formation" (ResearchGate, 2018).
# Similarity Theory argues homogeneous groups are more productive due to mutual attraction.
def calculate_commitment_score(team_matrix, idx_commit):
    """
    Calculates the commitment score for a team based on homogeneity (low std dev).
    Low standard deviation = high homogeneity
    Args:
        - team_matrix (np.ndarray): matrix of team members' data
        - idx_commit (int): index of the commitment column in the matrix
    Returns:
        - float: commitment score between 0.0 and 1.0
    """
    commit_col = team_matrix[:, idx_commit]
    return 1.0 - min(np.std(commit_col), 1.0)



# Reference: "Augmenting Team Diversity... using the Blau index" (arXiv:2410.00346).
# Uses Blau index proxy (unique count) for categorical, or Std Dev for numerical.
def calculate_diversity_score(team_matrix, idx, current_n, is_categorical=True):
    """
    Calculates the diversity score for a team based on Blau Index proxy or Std Dev
    Assumes a standard deviation of 5.0 is diverse enough for numerical features
    Args:
        - team_matrix (np.ndarray): matrix of team members' data
        - idx (int): index of the feature column in the matrix
        - current_n (int): number of members in the team
        - is_categorical (bool): whether the feature is categorical or numerical
    """
    col_data = team_matrix[:, idx]

    if is_categorical:
        # Maximize unique types (Blau Index proxy)
        return len(np.unique(col_data)) / current_n
    return min(np.std(col_data) / 5.0, 1.0)


def calculate_lead_score(team_matrix, idx_lead):
    """
    Constraint-based scoring for leadership
    Preference: exactly ONE leader per team
    Args:
        - team_matrix (np.ndarray): matrix of team members' data
        - idx_lead (int): index of the lead preference column in the matrix
    Returns:
        - float: lead preference score between 0.0 and 1.0
    """
    leads_sum = np.sum(team_matrix[:, idx_lead])
    if leads_sum == 1:
        return 1.0
    if leads_sum == 0:
        return 0.0
    return 0.5


# Reference: "Penalty Function Methods for Constrained Optimization with Genetic Algorithms" (Gen & Cheng).
# Quadratic penalty encourages convergence to valid constraints.
def calculate_size_penalty(current_n, min_size, max_size):
    """
    Calculates size penalty based on team size constraints.
    Returns 0.0 if size is within [min, max].
    Otherwise, returns quadratic penalty based on distance to nearest bound.
    Args:
        - current_n (int): current team size
        - min_size (int): minimum team size
        - max_size (int): maximum team size
    Returns:
        - float: size penalty
    """
    if min_size <= current_n <= max_size:
        return 0.0

    # distance to nearest valid bound
    diff = min(abs(current_n - min_size), abs(current_n - max_size))

    # scale penalty
    return (diff ** 2) * 0.5


# added this to avoid weird behavior where constraint values differ a lot
def calculate_size_deviation_penalty(current_n, avg_team_size):
    """
    Calculates the size penalty for a team based on deviation from average team size
    Args:
        - current_n (int): number of members in the team
        - team_size (int): desired team size
    Returns:
        - float: size penalty (higher is worse)
    """
    size_diff = abs(current_n - avg_team_size)
    return (size_diff ** 2) / (avg_team_size * 2)


def make_fitness_func(students_encoded, min_size,max_size, weights):
    """
    Creates the fitness function with specific weights for every criteria.
    Args:
        - students_encoded (np.ndarray): encoded student data
        - min_size (int): minimum team size
        - max_size (int): maximum team size
        - weights (list of float): weights for each criteria
    Returns:
        - function: fitness function for GA
    """
    w_avail, w_commit, w_job, w_edu, w_age, w_gender, w_exp, w_lead, w_tasks = weights
    total_cols = students_encoded.shape[1]
    n_tasks = total_cols - 28

    tasks_start = 21
    idx_commit  = 21 + n_tasks
    idx_edu     = idx_commit + 1
    idx_job     = idx_commit + 2
    idx_age     = idx_commit + 3
    idx_sex     = idx_commit + 4
    idx_exp     = idx_commit + 5
    idx_lead    = idx_commit + 6

    def fitness_func(ga_instance, solution, solution_idx):
        solution_int = np.asarray(solution, dtype=int)
        if solution_int.size == 0: # empty
            return 0.0

        num_teams = solution_int.max() + 1
        total_fitness = 0.0
        valid_teams = 0
        total_weight = sum(weights)

        for t in range(num_teams):
            idx = np.where(solution_int == t)[0]
            if len(idx) == 0:
                continue

            team_matrix = students_encoded[idx]
            current_n = len(idx)

            idx_commit = 21 + n_tasks

            score_avail  = calculate_availability_score(team_matrix, current_n)
            score_tasks  = calculate_tasks_score(team_matrix, n_tasks, tasks_start, idx_commit)
            score_commit = calculate_commitment_score(team_matrix, idx_commit)

            score_edu    = calculate_diversity_score(team_matrix, idx_edu, current_n, is_categorical=True)
            score_job    = calculate_diversity_score(team_matrix, idx_job, current_n, is_categorical=True)
            score_exp    = calculate_diversity_score(team_matrix, idx_exp, current_n, is_categorical=True)

            score_age    = calculate_diversity_score(team_matrix, idx_age, current_n, is_categorical=False)
            score_sex    = calculate_diversity_score(team_matrix, idx_sex, current_n, is_categorical=True)
            score_lead   = calculate_lead_score(team_matrix, idx_lead)

            size_penalty = calculate_size_penalty(current_n, min_size, max_size)
            size_deviation_penalty = calculate_size_deviation_penalty(current_n, int((min_size + max_size) / 2))

            team_score = (
                (score_avail  * w_avail) +
                (score_tasks  * w_tasks) +
                (score_commit * w_commit) +
                (score_edu    * w_edu) +
                (score_job    * w_job) +
                (score_age    * w_age) +
                (score_sex    * w_gender) +
                (score_exp    * w_exp) +
                (score_lead   * w_lead)
            )

            # normalize by sum of weights to keep score in range [0, 1]
            total_weight = sum(weights)
            if total_weight > 0:
                team_score /= total_weight

            # apply penalties
            team_score = team_score - size_penalty - size_deviation_penalty

            total_fitness += max(team_score, 0.0)
            valid_teams += 1

        if valid_teams == 0:
            return 0.0

        return float(total_fitness / valid_teams)

    return fitness_func

