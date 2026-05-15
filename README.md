## Team Matcher Documentation

## 1. Project Overview
**Team Matcher** is a web-based tool designed to automate and optimize the formation of student groups. By leveraging a Genetic Algorithm, the application processes complex student data, such as availability, skills, and diversity metrics, to generate balanced, diverse teams.

* **Goal:** Replace inefficient and time-consuming manual grouping with an optimized data-driven approach.
* **Core Technology:** Django (Web Framework), PyGAD (Genetic Algorithm), NumPy (Data Processing).

---

## 2. Key Features

* **Student app:** Student side app to handle student data input and storage.
* **Teacher Dashboard:**
    * CSV Upload & Validation.
    * Historical tracking of past team generations.
    * Downloadable CSV output of results.
    * Results visualization.

* **Admin Dashboard:**
    * Student profile visualization.
    * Task modification.
    * CSV generations.
    * Team name template customization.

* **Automated Matching Engine:** Uses evolutionary computation to find the best fit for teams based on multiple conflicting constraints.
* **Smart Scheduling:** Calculates time slot overlaps to ensure every team has at least one valid meeting time.
* **Flexible Constraints:**
    * **Team Size:** User-defined Minimum and Maximum team sizes.
    * **Weights:** Adjustable priority sliders (e.g., prioritize "Availability" over "Skill Mix").

* **Modern UI:** Responsive, dark-mode interface built with Bootstrap 5.

---

## 3. How It Works (The Algorithm)

The core logic resides in the Genetic Algorithm (GA), which mimics natural selection to evolve a solution.

### 3.1. Data Encoding (Pre-processing)
Before processing, raw student data is converted into a numerical matrix:
1.  **Availability:** One-hot encoded into a 21-slot binary vector (7 days × 3 slots).
2.  **Categorical Data:** Mapped to integers (e.g., Business=1, CS=2).
3.  **Continuous Data:** Integer conversion if necessary.

### B. The Optimization Cycle
1.  **Initialization:** A population of random team assignments is generated.
2.  **Evaluation (Fitness Function):** Each schedule is scored based on:
    * **Intersection:** Shared time availability and task preferences between team members.
    * **Diversity:** Mix of heterogeneous criteria ( e.g. skills/gender/backgrounds).
    * **Homogeneity:** Alignment of commitment levels.
    * **Penalties:** 
        * Penalizarion of lack of leaders.
        * Penalization of min/max constraint violations.
        * Penalization of large deviation from average team size.
3.  **Evolution:** The best schedules crossover and randomly mutate to produce better schedules.
4.  **Convergence:** The process repeats for ~200 generations until an optimal solution is found.

---

## 4. Technical Architecture

### Tech Stack
* **Backend:** Python 3.12, Django 5.0
* **Algorithm:** PyGAD, NumPy
* **Frontend:** HTML5, Bootstrap 5, Jinja2 Templates
* **Database:** SQLite

## 5. Installation & Set-up

    # clone repository
    git clone https://github.com/s-001069/teammatcher.git
    cd teammatcher

    # create venv
    python -m venv .venv

    # activate venv on linux:
    source venv/bin/activate
    # activate venv on windows:
    .venv\Scripts\activate
    
    # install requirements
    pip install -r requirements.txt

    # apply migrations
    python manage.py makemigrations
    python manage.py migrate

    # collect static libaries (necessary only for production)
    python manage.py collectstatic

    # create admin user (can access admin and teacher)
    python manage.py createsuperuser

    # execute server
    python manage.py runserver

## 6. User guide:

### Teacher:
    - Login: Use the superuser credentials to bypass the security check
    - Upload a CSV file containing student data
    - Set min and max team constraints
    - Adjust criteria weights
    - Click "Generate teams": the algorithm will run in a few seconds
    - Review results on the UI
    - Export CSV output with results

The new teams are added in the first empty column after "mode" column. If such column does not exists, a new 'teams' column is added on the CSV.

So one matching happens per button click. If more than one teams are required, the output of the first matching can be used for the second matching and so on. This decision was taken to separate completely matching, for cases where criteria and constraints change depending on the project (e.g. different tasks, different weights on criteria).

### Admin Interface:
    - StudentProfiles: Model that contains all student data. Possible actions:
        - Visualize data
        - Delete student data
        - Insert student data
    
    - Tasks: Model that contains all tasks. Possible actions:
        - Visualize data
        - Delete task
        - Insert task

    - CSV Generations: Model that contains past CSV generation data.
        - Contains:
            - Generation datetime
            - CSV data string
            - Average team size
            - Template used for names
            - Student count

        - Possible actions:
            - Visualize data
            - Delete CSV Generation data

    - Team name templates: Model that contains templates for team naming
        - Contains:
            - Name of template
            - List of team names as json array
            - is default flag

        - Possible actions:
            - Visualize templates
            - Delete template data
            - Insert new template

### Student:
    - Access student form webpage
    - Input form with data
    - Submit form
    - Answer honestly, it will help achieve the best possible matching
