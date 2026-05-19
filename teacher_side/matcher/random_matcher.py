""" Random student matcher """

import random


# this is not used anymore, it is not compatible with the newest csv reading functionality

def random_match(all_students, team_size, team_template):
    """ 
        Randomly assigns students to teams
        Args:
            - all_students: list of StudentProfile objects
            - team_size: desired size of each team
            - team_template: TeamTemplate object (optional)
        Returns:
            - list of team assignments
            - list of dicts for CSV generation
    """

    random.shuffle(all_students)

    team_names = []
    teams = []

    if team_template:
        team_names = team_template.team_names

    results_for_csv = []

    for i in range(0, len(all_students), team_size):
        chunk = all_students[i:i + team_size]
        team_index = i // team_size

        if team_index < len(team_names):
            team_name = team_names[team_index]
        else:
            team_name = f'Team {team_index + 1}'

        for student in chunk:
            student_with_team = student.copy()
            student_with_team['team'] = team_name
            results_for_csv.append(student_with_team)

        teams.append({'name': team_name, 'members': chunk})

    return teams, results_for_csv
