import logging

from teacher_side.models import TeamNameTemplate

logger = logging.getLogger("teacher_side.bootstrap")

marvel_template = TeamNameTemplate.objects.create(
    name="Marvel Heroes",
    team_names=[
        "Avengers",
        "X-Men",
        "Guardians of the Galaxy",
        "Fantastic Four",
        "Defenders"
    ],
    is_default=True
)
logger.info(
    "bootstrap.template.created",
    extra={"template_name": str(marvel_template), "is_default": True},
)

greek_template = TeamNameTemplate.objects.create(
    name="Greek Gods",
    team_names=[
        "Zeus",
        "Athena",
        "Poseidon",
        "Apollo",
        "Artemis"
    ]
)
logger.info(
    "bootstrap.template.created",
    extra={"template_name": str(greek_template), "is_default": False},
)

color_template = TeamNameTemplate.objects.create(
    name="Color Teams",
    team_names=[
        "Red Dragons",
        "Blue Phoenixes",
        "Green Titans",
        "Yellow Lightning",
        "Purple Warriors"
    ]
)
logger.info(
    "bootstrap.template.created",
    extra={"template_name": str(color_template), "is_default": False},
)

logger.info(
    "bootstrap.complete",
    extra={"total_templates": TeamNameTemplate.objects.count()},
)
