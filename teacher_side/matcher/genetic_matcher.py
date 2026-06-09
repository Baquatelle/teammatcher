""" Genetic algorithm student matcher """

import logging
import math
import pygad

NUM_GENERATIONS = 200

from teacher_side.matcher.encoder import prepare_data
from teacher_side.matcher.fitness_function import make_fitness_func

logger = logging.getLogger(__name__)


def match(df, team_template, weights, constraints, on_generation=None, random_seed=None):
    """
        Matches students into teams using genetic algorithm
        Args:
            - df: pandas Dataframe containing student data from uploaded CSV
            - team_template: TeamTemplate object
            - weights: list of weights for fitness function
            - constraints: dict with team size constraints
            - on_generation: optional callback invoked by pygad after each
              generation (used for SSE progress streaming during rematch).
            - random_seed: optional int for deterministic runs (used for
              GA regression tests). Same input + same seed => same output.
        Returns:
            - df with team assignments
            - target column name (str) where assignments were added
            - best fitness score
    """
    students_encoded = prepare_data(df)
    n_students = students_encoded.shape[0]

    min_size = constraints['min_size']
    max_size = constraints['max_size']

    target_avg_size = (min_size + max_size) / 2
    n_teams = math.ceil(n_students / target_avg_size)

    # case one extra student
    if (n_teams * max_size) < n_students:
        n_teams = math.ceil(n_students / max_size)

    # case not enough students
    if (n_teams * min_size) > n_students:
        n_teams = math.floor(n_students / min_size)

    fitness_func = make_fitness_func(
        students_encoded,
        weights=weights,
        constraints=constraints
    )
    gene_space = list(range(n_teams))

    ga_instance = pygad.GA(
        num_generations=NUM_GENERATIONS,
        num_parents_mating=20,
        fitness_func=fitness_func,
        sol_per_pop=40,
        num_genes=students_encoded.shape[0],
        gene_space=gene_space,
        mutation_probability=0.1,
        crossover_type="single_point",
        mutation_type="random",
        keep_parents=2,
        stop_criteria=["saturate_50"],
        on_generation=on_generation,
        random_seed=random_seed,
    )

    ga_instance.run()
    best_solution, best_fitness, _ = ga_instance.best_solution()

    # builds team names from template or default names 'Team X'
    template_names = team_template.team_names if team_template else []
    n_teams_used = int(max(best_solution)) + 1

    team_names = []
    for team_index in range(n_teams_used):
        if team_index < len(template_names):
            team_names.append(template_names[team_index])
        else:
            team_names.append(f"Team {team_index + 1}")
    team_assignments = [team_names[int(t)] for t in best_solution]

    target_col = None

    if 'mode' in df.columns:
        mode_idx = df.columns.get_loc('mode')
        cols_after_mode = df.columns[mode_idx+1:]

        # finds the first empty column in slice (after 'mode' column)
        for col in cols_after_mode:
            if df[col].isnull().all():
                target_col = col
                break

    if target_col: # adds results to first empty column after 'mode'
        df[target_col] = team_assignments
    else: # creates new column 'teams'
        logger.info("ga.no_empty_column_after_mode")
        df['teams'] = team_assignments
        target_col = 'teams'

    return df, target_col, best_fitness
