""" helper functions for matcher module """

from student_side.models import StudentProfile, Task

def get_weights(form):
    """
        Extracts weights from the form
        Args:
            - form: UploadFileForm instance
        Returns:
            - list of weights (int)
    """
    return [
        form.cleaned_data['weight_availability'],
        form.cleaned_data['weight_commitment'],
        form.cleaned_data['weight_job'],
        form.cleaned_data['weight_education'],
        form.cleaned_data['weight_age'],
        form.cleaned_data['weight_gender'],
        form.cleaned_data['weight_experience'],
        form.cleaned_data['weight_lead'],
        form.cleaned_data['weight_tasks']
    ]


def get_student_data():
    """ 
        Gets student data from DB 
        Returns:
            - list of StudentProfile objects
    """
    return StudentProfile.objects.all()


def get_tasks():
    """ 
        Gets tasks from DB
        Returns:
            - list of task names
    """
    return list(Task.objects.filter(active=True).values_list("name", flat=True))
