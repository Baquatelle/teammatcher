"""
Optional seed script — creates StudentProfile + Task rows that MATCH the usernames in
teammatcher_test_roster.csv, so the matcher and the soft-constraint violations work on
REAL data (not the "form-less" full-availability default).

WHY YOU NEED THIS
    The uploaded CSV only carries the roster (username + LMS columns). All the data the
    GA actually optimises over — availability, gender, age, background, tasks — lives in
    StudentProfile rows in the database, normally filled via the student form. Without
    them, every student is encoded as "fully available + all tasks + everything else 0",
    so diversity/violation checks stay trivial.

HOW TO RUN  (from the project root, venv active)
    python manage.py shell < seed_test_profiles.py

    Idempotent: re-running updates the same rows (keyed by student_id). To start clean:
    StudentProfile.objects.all().delete()  and  Task.objects.all().delete()

NOTES (flagged)
    - All categorical values below use the EXACT keys the encoder expects
      (see teacher_side/matcher/encoder.py). Any other string is silently encoded as 0.
    - Availability is matched by substring ("Morning"/"Afternoon"/"Evening"); the exact
      surrounding format does not matter to the encoder.
    - Values are deliberately mixed so that, depending on how the GA groups them, you can
      see some teams trigger gender/age/job/lead/availability violations and others not.
"""

from student_side.models import StudentProfile, Task

# --- task catalogue ---------------------------------------------------------
TASK_NAMES = ["Frontend", "Backend", "Testing", "Documentation", "Design"]
tasks = {}
for name in TASK_NAMES:
    t, _ = Task.objects.get_or_create(name=name, defaults={"active": True})
    if not t.active:
        t.active = True
        t.save(update_fields=["active"])
    tasks[name] = t

# --- 18 students matching teammatcher_test_roster.csv -----------------------
# fields: id, commitment, education, job(professional), age, gender, experience,
#         lead, availability(list of weekday->slots), preferred task names
STUDENTS = [
    ("s-001001", "high",    "bachelor_cs",          "industry_it",       24, "male",   "advanced",     "lead",    {"monday": "Morning"},                  ["Backend", "Testing"]),
    ("s-001002", "regular", "bachelor_business",    "industry_business", 27, "female", "intermediate", "support", {"monday": "Morning", "wednesday": "Afternoon"}, ["Frontend", "Design"]),
    ("s-001003", "minimal", "bachelor_engineering", "internship",        22, "male",   "beginner",     "support", {"tuesday": "Evening"},                 ["Documentation"]),
    ("s-001004", "regular", "master_business",      "industry_other",    31, "female", "advanced",     "support", {"monday": "Morning"},                  ["Backend", "Frontend"]),
    ("s-001005", "high",    "bachelor_cs",          "industry_it",       23, "male",   "intermediate", "lead",    {"monday": "Morning", "friday": "Morning"}, ["Testing"]),
    ("s-001006", "regular", "bachelor_social",      "none",              20, "female", "beginner",     "support", {"thursday": "Afternoon"},              ["Design", "Documentation"]),
    ("s-001007", "minimal", "bachelor_cs",          "working_student",   25, "other",  "intermediate", "support", {"monday": "Morning"},                  ["Backend"]),
    ("s-001008", "high",    "master_other",         "industry_it",       29, "male",   "advanced",     "lead",    {"wednesday": "Evening"},               ["Frontend", "Backend"]),
    ("s-001009", "regular", "bachelor_engineering", "industry_business", 26, "female", "intermediate", "support", {"monday": "Morning", "tuesday": "Morning"}, ["Testing", "Documentation"]),
    ("s-001010", "regular", "bachelor_cs",          "internship",        21, "male",   "beginner",     "support", {"monday": "Morning"},                  ["Frontend"]),
    ("s-001011", "high",    "bachelor_business",    "industry_other",    33, "female", "advanced",     "lead",    {"friday": "Afternoon"},                ["Design"]),
    ("s-001012", "minimal", "other",                "none",              19, "male",   "beginner",     "support", {"monday": "Morning"},                  ["Documentation", "Testing"]),
    ("s-001013", "regular", "bachelor_cs",          "industry_it",       28, "female", "intermediate", "support", {"monday": "Morning", "thursday": "Evening"}, ["Backend"]),
    ("s-001014", "high",    "master_business",      "industry_business", 30, "male",   "advanced",     "lead",    {"tuesday": "Afternoon"},               ["Frontend", "Design"]),
    ("s-001015", "regular", "bachelor_social",      "working_student",   22, "female", "beginner",     "support", {"monday": "Morning"},                  ["Documentation"]),
    ("s-001016", "minimal", "bachelor_engineering", "internship",        23, "male",   "intermediate", "support", {"wednesday": "Morning"},               ["Testing", "Backend"]),
    ("s-001017", "regular", "bachelor_cs",          "industry_it",       26, "other",  "advanced",     "support", {"monday": "Morning"},                  ["Frontend", "Backend"]),
    ("s-001018", "high",    "master_other",         "industry_other",    35, "female", "advanced",     "lead",    {"monday": "Morning", "friday": "Evening"}, ["Design", "Documentation"]),
]

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

created, updated = 0, 0
for (sid, commit, edu, job, age, gender, exp, lead, avail, task_names) in STUDENTS:
    availability = {f"availability_{d}": avail.get(d, "") for d in WEEKDAYS}
    obj, was_created = StudentProfile.objects.update_or_create(
        student_id=sid,
        defaults=dict(
            commitment=commit,
            educational_background=edu,
            professional_background=job,
            age=age,
            gender=gender,
            experience_level=exp,
            lead_preference=lead,
            **availability,
        ),
    )
    obj.preferred_tasks.set([tasks[n] for n in task_names])
    created += int(was_created)
    updated += int(not was_created)

print(f"Tasks ready: {len(tasks)}  ->  {', '.join(tasks)}")
print(f"StudentProfiles: created={created}, updated={updated}, total={StudentProfile.objects.count()}")
print("Now upload teammatcher_test_roster.csv in the teacher view and click Generate.")
