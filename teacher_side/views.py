import csv
import io

from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse
from django.urls import reverse
import pandas as pd

from teacher_side.matcher.genetic_matcher import match
from teacher_side.matcher.utils import get_weights
from .forms import UploadFileForm
from .models import (
    CSVGeneration,
    MatchingSession,
    Team,
    TeamMembership,
    WEIGHT_KEYS,
)
from student_side.models import StudentProfile


@staff_member_required
def index(request):
    teams = []

    if request.method == 'POST':
        form = UploadFileForm(request.POST, request.FILES)
        if form.is_valid():
            # Read the uploaded file once into a string, then use it for both
            # the CSV snapshot and the dataframe — reading it twice would fail.
            csv_text = request.FILES["file"].read().decode("utf-8")
            df = pd.read_csv(io.StringIO(csv_text))
            df = df.dropna(how='all')
            
            team_template = form.cleaned_data.get('team_template')
            weights = get_weights(form)
            constraints = {
                'min_size': form.cleaned_data['min_team_size'],
                'max_size': form.cleaned_data['max_team_size'],
            }

            # group
            df_result, target_col, best_fitness = match(df, team_template, weights, constraints)
            print("Best fitness:", best_fitness)

            # Save the matching results. Only one session exists at a time:
            # creating a new one deletes the previous one and all its teams.

            # Store weights as a dict keyed by WEIGHT_KEYS (same order as the GA expects).
            weights_dict = {
                k: form.cleaned_data.get(f"weight_{k}", 0) for k in WEIGHT_KEYS
            }

            # Save the column order before matching, because PostgreSQL doesn't preserve dict key order.
            column_order = list(df.columns)

            with transaction.atomic():
                # Delete the previous session (also deletes its teams and memberships).
                MatchingSession.objects.all().delete()

                session = MatchingSession.objects.create(
                    original_csv=csv_text,
                    min_size=constraints["min_size"],
                    max_size=constraints["max_size"],
                    weights=weights_dict,
                    target_col=target_col,
                    column_order=column_order,
                    template_used=team_template,
                )

                # Map username → StudentProfile for the soft display link.
                profile_map = {p.student_id: p for p in StudentProfile.objects.all()}

                # Create one Team per group produced by the GA.
                team_name_set = df_result[target_col].unique()
                team_objects = {}
                for name in team_name_set:
                    team_objects[name] = Team.objects.create(
                        session=session, name=str(name)
                    )

                # Create all memberships in one query.
                # Note: bulk_create skips Django signals — use .save() in a loop if you add signal handlers.
                memberships_to_create = []
                for _, row in df_result.iterrows():
                    original_row = {
                        col: str(val) if pd.notna(val) else ""
                        for col, val in row.items()
                    }
                    username = str(row["username"]).strip()
                    memberships_to_create.append(
                        TeamMembership(
                            session=session,
                            team=team_objects[row[target_col]],
                            username=username,
                            original_row=original_row,
                            profile=profile_map.get(username),
                        )
                    )
                TeamMembership.objects.bulk_create(memberships_to_create)

            # Legacy generation-time audit row.
            csv_content = df_result.to_csv(index=False)
            CSVGeneration.objects.create_generation(
                    csv_data=csv_content,
                    team_size=int((constraints['min_size'] + constraints['max_size']) / 2), # average team size
                    template_used=team_template,
                    student_count=df.shape[0]
            )

            # Redirect to the adjustment view.
            return redirect(reverse("teacher_side:adjust_teams", args=[session.id]))
        else:
            print(form.errors)
    else:
        form = UploadFileForm()
        if 'results' in request.session:
            del request.session['results']

    historical_generations = CSVGeneration.objects.order_by('-id')[:5] # ordered to get the most recent

    return render(request, 'allocator/index.html', {
        'form': form,
        'teams': teams,
        'historical_generations': historical_generations
    })


@staff_member_required
def download_csv(request):
    results = request.session.get('results', [])
    if not results:
        latest_generation = CSVGeneration.objects.first()
        if latest_generation:
            response = HttpResponse(
                content_type='text/csv',
                headers={'Content-Disposition': 'attachment; filename="teams_latest.csv"'},
            )
            response.write(latest_generation.csv_data)
            return response
        return HttpResponse("No results found to download.", content_type='text/plain')

    response = HttpResponse(
        content_type='text/csv',
        headers={'Content-Disposition': 'attachment; filename="teams.csv"'},
    )

    if results:
        fieldnames = list(results[0].keys())
        writer = csv.DictWriter(response, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    return response


@staff_member_required
def download_historical_csv(request, generation_id):
    generation = get_object_or_404(CSVGeneration, id=generation_id)
    response = HttpResponse(
        content_type='text/csv',
        headers={
            'Content-Disposition': 
                f'attachment; filename="teams_{generation.generated_at.strftime("%Y%m%d_%H%M%S")}.csv"'
        },
    )
    response.write(generation.csv_data)
    return response
