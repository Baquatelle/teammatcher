# Task 6 — Team Matcher: Manual Team Adjustments

| Document property | Value                      |
| ----------------- | -------------------------- |
| Type              | Requirements specification |
| Author            | Abel GABOR                 |
| Date              | 14. May 2026               |
| Version           | 1.0                        |
| Status            | Draft                      |

## 1. Context

The Team Matcher is a Django application (the Team Matcher Django project) that automates student-to-team assignment for an Open edX course at UDS. Built by a prior cohort, it accepts a roster CSV exported from Open edX, runs a genetic-algorithm matcher (PyGAD) over weighted criteria (availability, commitment, background, age, gender, experience, leadership, task interest), and produces a CSV that the teacher uploads back to Open edX.

The algorithm produces good matches most of the time, but **the teacher routinely needs to make manual adjustments**. The current workaround is:

1. Generate teams in the tool → download CSV.
2. Open in Excel → drag rows between teams by hand.
3. Save as CSV → upload to Open edX.

The goal of this task is to **make manual adjustments first-class inside the Team Matcher itself**, and — as connected improvements — to support partial re-matching, factor in student priorities during re-matches, and replace the CSV round-trip with direct Open edX API integration.

**Intended outcome:** the teacher's full team-finalization workflow happens in the Team Matcher UI, end-to-end, without leaving the tool.

## 2. Goals & Non-Goals

### In scope

- **G1.** Visual, drag-and-drop reassignment of students between teams after generation.
- **G2.** Persistent team representation in the database (teams survive page reloads and are no longer reconstructed from CSV text on every view).
- **G3.** Live editing with an explicit "Apply" step (Push to Open edX in the happy path; CSV export only when API is unavailable). An audit record is created on each apply so the teacher can see what was sent and when.
- **G4.** Partial re-matching: the teacher can lock some teams or students and re-run the matcher on the remainder.
- **G5.** Student priorities (peer nominations + project preferences) collected via the student form and applied **only during re-matching**, not during initial generation.
- **G6.** Full two-way Open edX API integration: pull rosters directly from Open edX, push finalized teams back, eliminating CSV upload/download.
- **G7.** Constraint feedback during editing (min/max team size violations) as **soft warnings the teacher can override**.

### Out of scope (this iteration)

- Changes to the genetic-algorithm fitness function beyond what is required to accept locked-team constraints and priority inputs.
- Real-time multi-teacher collaborative editing (only one teacher is currently expected to edit at a time).
- Editing student profile data from the team-adjustment view (still managed via Django admin and the student form).
- Bulk team operations beyond what drag-and-drop naturally affords (e.g., "shuffle all", "swap two teams whole-cloth").
- Mobile-first experience. Desktop browser is the primary target.

## 3. Stakeholders & Personas

| Persona           | Role                                       | Primary use cases addressed                                                               |
| ----------------- | ------------------------------------------ | ----------------------------------------------------------------------------------------- |
| **Teacher**       | Owns team formation. Staff-authenticated.  | G1–G7. Performs all manual adjustments, triggers re-matches, finalizes exports.           |
| **Student**       | Course participant. Submits their profile. | G5 — provides peer nominations and project preferences via the existing `/student/` form. |
| **Administrator** | Django superuser.                          | Manages `TeamNameTemplate`, `Task`, and (new) Open edX API credentials via Django admin.  |

Access control: manual adjustment views are **teacher-only**, gated by Django's `staff_member_required` decorator, matching the existing `/teacher/` view's access model.

## 4. Functional Requirements

### FR-1. Persistent team representation
- **FR-1.1** The system must persist team composition in the database. Teams must not be derived solely from CSV text.
- **FR-1.2** A team has, at minimum: a name (from `TeamNameTemplate` or auto-generated), a set of student members, a creation timestamp, and a flag indicating whether it is "locked" (excluded from future re-matches).
- **FR-1.3** A student belongs to at most one team within a given matching session. Moving a student to a new team must atomically remove them from any previous team.
- **FR-1.4** The system must continue to support historical `CSVGeneration` records as immutable export snapshots, but the **live working state** is the persistent team data, not a CSV blob.

### FR-2. Drag-and-drop manual reassignment
- **FR-2.1** The teacher view must display all current teams as columns or cards, each listing the students assigned to that team with enough identifying information to be recognizable (at minimum: student ID; ideally also any name field available from the roster CSV).
- **FR-2.2** The teacher must be able to drag a student from one team and drop them onto another team. The change must persist immediately (no separate save step).
- **FR-2.3** The system must support dragging a student into an "unassigned" pool and back into any team, to support reorganizations that temporarily leave a student team-less.
- **FR-2.4** Drag operations must be visually confirmed (highlight target team while hovering; smooth reorder; clear feedback on successful drop or failure).
- **FR-2.5** The view must show, per team, the current member count and a visual indication when the team is below the min size or above the max size set at generation time.

### FR-3. Live editing with explicit apply step
- **FR-3.1** All manual edits modify the live (working) team state. There is no draft/published distinction during editing. Edits are *not* automatically propagated to Open edX.
- **FR-3.2** A clearly labeled **"Apply / Push to Open edX"** action must send the current live state to Open edX via API (see FR-6). This is the primary finalization path.
- **FR-3.3** Each apply must create an immutable **audit record** capturing the team composition at that point in time, who triggered it, and when. The existing `CSVGeneration` model may be reused for this purpose, or a lighter audit-log model introduced — the choice is an implementation detail. The record exists for accountability and rollback inspection.
- **FR-3.4** When Open edX API access is unavailable or the teacher prefers offline export, a **"Download CSV"** action must produce the same artifact format the current tool produces today. This is the fallback path; it is not part of the routine workflow once API integration is in place.
- **FR-3.5** Manual edits performed after an apply do **not** retroactively modify the audit record or the state in Open edX — a new apply must be triggered to propagate them.

### FR-4. Partial re-matching
- **FR-4.1** The teacher must be able to mark individual teams or individual students as **locked**. Locked entities are excluded from any subsequent re-match run.
- **FR-4.2** A "Re-match unlocked" action runs the genetic matcher only over the unlocked students, distributing them across (a) existing unlocked teams with open slots, and/or (b) newly created teams, subject to the same min/max size constraints.
- **FR-4.3** The teacher may adjust the criteria weights for the re-match independently of the initial generation's weights.
- **FR-4.4** Re-matching must use the **same fitness function** as initial matching, with the addition of student-priority inputs (FR-5), and respecting locked-team membership as a hard constraint.

### FR-5. Student priorities (re-match input only)
- **FR-5.1** The student profile form (`/student/`) must allow students to optionally provide:
  - **Peer nominations:** up to N (suggested N = 3) student IDs of preferred teammates.
  - **Project preferences:** a ranked or weighted set of preferred projects/tasks (reusing or extending the existing `Task` model).
- **FR-5.2** Priorities are stored against the student profile and are visible to the teacher (e.g., as tooltips or a side panel in the manual-adjustment view).
- **FR-5.3** Priorities are **not** applied during initial team generation. They are applied only when the teacher explicitly runs a re-match.
- **FR-5.4** Peer nominations are soft preferences: the matcher should prefer placing nominator + nominee together but must not violate hard constraints (team size, locked teams) to do so.
- **FR-5.5** Project preferences must integrate with the existing task-interest similarity criterion, with weighting controllable by the teacher at re-match time.
- **FR-5.6** Nominations must not be reciprocity-required (A nominating B does not require B to nominate A).
- **FR-5.7** The system must handle the case where a nominated student ID does not exist in the current roster (silently ignore, do not error).

### FR-6. Open edX API integration (two-way)
- **FR-6.1** The system must support fetching the course roster directly from Open edX via API, replacing the CSV upload step. The teacher selects a configured course; the system retrieves the enrolled student list.
- **FR-6.2** The system must support pushing finalized team assignments back to Open edX via API, replacing the CSV download/re-upload step.
- **FR-6.3** API credentials and the target Open edX instance URL must be configurable via Django admin (new model or settings), not hard-coded.
- **FR-6.4** The CSV upload/download path must remain functional as a fallback for cases where API access is unavailable or fails.
- **FR-6.5** Push operations to Open edX must be idempotent at the team-assignment level: re-pushing the same final state must not duplicate assignments or corrupt existing membership.
- **FR-6.6** Authentication errors, network errors, and partial-failure cases must surface clear, actionable error messages to the teacher (not silent failures).

### FR-7. Validation & constraint feedback
- **FR-7.1** While editing, the system must show — per team and overall — the current min/max size violation status.
- **FR-7.2** Drag-and-drop operations that violate min/max are permitted ("soft warning, allow override"). The system must visually mark violating teams but must not refuse the drop.
- **FR-7.3** On Apply (push or CSV download), if any team violates min/max, the teacher must be shown a summary of violations and asked to confirm before the action proceeds.
- **FR-7.4** The system must prevent a student from appearing on two teams simultaneously (this is a hard constraint, not a soft warning).

## 5. Non-Functional Requirements

- **NFR-1. Performance.** Drag-and-drop must feel instant on a roster of up to 200 students across up to 50 teams (much higher than the typical course size). Persistence after a drop should complete within ~300 ms on a local network; the UI must not block.
- **NFR-2. Browser support.** Latest Chrome, Firefox, Edge, Safari. No IE.
- **NFR-4. Visual consistency.** New views must match the existing dark-themed Bootstrap 5 design system already used in `/teacher/` and `/student/` (gradient buttons, dark slate cards, Inter font, Bootstrap Icons).
- **NFR-5. Security.** All manual-adjustment endpoints are gated by `staff_member_required`. Open edX API credentials are stored encrypted at rest or via Django's existing settings mechanism — never in plain text in the repo. CSRF protection on all state-mutating endpoints.
- **NFR-6. Resilience.** Partial failures during a re-match or an Open edX push must leave the system in a recoverable state (no half-applied edits to live data).
- **NFR-7. Auditability.** Each Apply action creates an immutable audit record (see FR-3.3), providing a built-in trail of what was pushed to Open edX (or exported as CSV) and when.

## 6. Data Requirements

The following data must be representable in the system.

- **Team** — a named group of students, with a lock flag and team-size constraint metadata.
- **Team membership** — the link between `StudentProfile` and `Team`; must enforce one-team-per-student.
- **Unassigned pool** — a logical bucket for students temporarily without a team; not necessarily a distinct DB object, but the UI must surface it.
- **Student priorities** — peer nominations (list of student IDs) and project preferences (ranked or weighted list of `Task`s) attached to `StudentProfile`.
- **Open edX integration config** — Open edX instance URL, course identifier, API credentials, last-sync timestamp.
- **Apply audit record** — immutable snapshot of team composition + timestamp + actor for each Apply action. May reuse the existing `CSVGeneration` model or be a separate lightweight log; either way, the live team state (not a CSV blob) is the canonical store of *current* assignments.
- **CSVGeneration (existing)** — retained for backward compatibility and for the CSV-fallback path. No longer the canonical artifact in the API-enabled flow.

### Migration considerations

- Existing `CSVGeneration` records are pure text and have no `Team` rows. The migration plan (deferred) must decide whether to back-fill or simply treat historical data as legacy CSV-only records.
- The existing matching algorithm currently emits a CSV; it will need to also (or instead) produce persistent `Team` rows. Backward compatibility for the existing index view should be maintained until the new view supersedes it.

## 7. UI / UX Requirements

- **UX-1.** The manual-adjustment view is reachable from the existing teacher dashboard (`/teacher/`) immediately after a generation completes, and accessible later from the historical-generations list.
- **UX-2.** Each team is rendered as a card or column. Cards display: team name, member count, size-violation indicator, lock toggle, and the list of member students (draggable items).
- **UX-3.** Each student item shows the information the teacher needs to recognize them (at minimum: student ID; preferably also availability summary, commitment level, and any priority nominations they made) and includes a **lock toggle** so individual students can be excluded from re-matching independently of their team's lock state.
- **UX-4.** A persistent "Unassigned" zone is always visible.
- **UX-5.** A toolbar exposes: **Re-match unlocked**, **Apply (Push to Open edX)** as the primary action, and **Download CSV** as a secondary/fallback action.
- **UX-6.** The criteria-weight form is available again in the re-match dialog, pre-populated with the values from the originating generation but editable per re-match.
- **UX-7.** Constraint warnings are visually subtle but unmissable: an icon + color on the violating team card, plus a summary count in the toolbar.
- **UX-8.** Destructive actions (e.g., delete a team, clear all assignments) require confirmation.

## 8. Constraints & Assumptions

- The existing genetic-algorithm matcher (`teacher_side/matcher/`) is the source of truth for generating assignments and will be reused for re-matching. Its fitness function will be extended, not replaced.
- Only one teacher is expected to edit the live team state at a time. No multi-user merge logic is required.
- Open edX API access details (endpoints, auth flow, scopes) must be discovered as part of implementation; this document assumes the teacher can supply the necessary credentials and that the relevant Open edX endpoints exist for roster reads and team writes. If they don't, FR-6 reverts to "document requirements only" and the CSV path remains the canonical workflow.
- Students do not log in to provide priorities — they continue to identify themselves via `student_id` on the public student form, as today. This is a known limitation inherited from the existing tool.

## 9. Risks & Open Questions

| #   | Risk / Question                                                           | Notes                                                                                                            |
| --- | ------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| R1  | Peer nominations could be gamed (cliques excluding others).               | Soft-preference weighting (FR-5.4) limits but does not eliminate this. Acceptable for the teacher-overseen flow. |
| R2  | A locked team that violates min/max size after lock can't be re-balanced. | Documented as expected behavior; locking is the teacher's explicit choice.                                       |
| R3  | Drag-and-drop libraries vary in accessibility.                            | NFR-2 requires keyboard fallback — library choice (deferred) must support this.                                  |
| R4  | Existing `CSVGeneration` history has no associated `Team` rows.           | Decide during implementation: back-fill, ignore, or treat as read-only legacy.                                   |

## 10. Acceptance Criteria

The feature is complete when **all** of the following are true:

1. After generating teams, the teacher can open a view that displays all teams and all student-team memberships, sourced from the database (not parsed from CSV text on every load).
2. The teacher can drag a student from one team to another, and the change persists across a page reload.
3. The teacher can drag a student into an "Unassigned" pool and back into any team.
4. Min/max size violations are visible per team during editing and summarized before Apply, but do not block edits.
5. The teacher can lock specific teams or students and run a re-match that respects those locks and leaves locked entities untouched.
6. Student peer nominations and project preferences, collected via the student form, are visible to the teacher in the adjustment view and measurably influence re-match outcomes (verifiable via a test case where strong mutual nominations produce co-assignment).
7. The teacher can pull a roster directly from Open edX (without uploading a CSV) and push the finalized teams back to Open edX (without downloading a CSV), assuming valid API credentials are configured. Each Apply produces an immutable audit record.
8. If Open edX API access is unavailable, the teacher can fall back to CSV download and the existing CSV upload-to-Open-edX flow still works end-to-end.
9. All new endpoints are gated by `staff_member_required`. CSRF protection is enforced on all mutating endpoints.
10. The new views match the existing dark-themed visual design of the tool.

## 11. Verification

Verification has two layers: **automated unit/integration tests** (run on every change) and **end-to-end walkthroughs** (run before declaring the feature done).

### 11.1 Automated tests

The existing `student_side/tests.py` and `teacher_side/tests.py` are empty placeholders. This iteration must add Django `TestCase`-based tests covering at least:

**Models & data integrity**
- A student can belong to at most one team at any time (FR-1.3). Assigning to a second team removes the previous membership atomically.
- Locking a team or student is persisted and survives reload (FR-4.1).
- Peer-nomination references to non-existent student IDs are accepted and silently ignored (FR-5.7).

**Team-edit endpoints**
- Moving a student between teams via the reassignment endpoint returns success and the database reflects the change.
- Moving a non-existent student or to a non-existent team returns a clear error and does not corrupt state.
- All edit endpoints are gated by `staff_member_required`: a request without staff credentials returns 302/403 (NFR-5).
- All edit endpoints reject requests without a valid CSRF token (NFR-5).

**Apply / audit (FR-3)**
- Apply creates exactly one immutable audit record per call, capturing the team state at that moment.
- Subsequent edits do not modify a prior audit record (FR-3.5).
- A second Apply with no intervening edits produces a new record that is content-equivalent to the previous one (idempotent from Open edX's perspective per FR-6.5).

**Re-matching (FR-4)**
- Locked teams' membership is byte-for-byte identical before and after a re-match run.
- Re-match operates only over unlocked students; unlocked students missing from any team end up assigned.
- A re-match where every team is locked is a no-op and produces no error.

**Priorities in re-matching (FR-5)**
- With strong mutual peer nominations between two unlocked students and no conflicting constraints, the re-matcher places them on the same team (probabilistic check with a fixed RNG seed).
- Priorities are *not* applied during initial generation: an initial run with the same input and seed produces the same output regardless of priority data on profiles.

**Open edX API client (FR-6)**
- API client tests use mocked HTTP responses (`responses` or `httpx.MockTransport`) — no real network calls.
- The CSV produced by the Apply path round-trips losslessly through the `team_membership_csv` format expected by Open edX.
- Authentication failure (invalid JWT) surfaces an actionable error message, not a 500.
- Network failure during push leaves local team state unchanged (FR-6.6, NFR-6).
- With the API integration explicitly disabled, the CSV download path still produces a valid file (FR-6.4).

**Constraint validation (FR-7)**
- Teams below min size and above max size are correctly flagged.
- A drag operation that would create a violation succeeds (soft warning, not hard block).
- A drag operation that would place a student on two teams simultaneously fails (hard constraint).

**Regression**
- The existing genetic-matcher fitness function, given the same input and RNG seed, produces identical output to the current `main` branch. A small fixture-based test pinning the current behavior should be added so any future fitness changes are deliberate.

Target: tests run in under 30 seconds locally, executable via `python manage.py test`. CI integration is out of scope for this iteration but the test suite must be CI-ready.

### 11.2 End-to-end walkthroughs

- **Manual UX walkthrough.** Pull roster from Open edX → generate → manually adjust via drag-and-drop → lock a team → re-match the rest → Apply to Open edX. Confirm no Excel detour is needed and no CSV files are produced or consumed in the happy path.
- **Constraint-violation walkthrough.** Drag students until a team is below min and another is above max; confirm warnings appear, edits persist, and Apply prompts for confirmation.
- **Priority walkthrough.** Have two students mutually nominate each other; run a re-match; confirm they end up on the same team where size constraints permit.
- **Lock-respect walkthrough.** Lock a team; run a re-match; confirm the locked team's membership is byte-for-byte identical before and after.
- **API fallback walkthrough.** With Open edX API disabled, confirm the CSV upload/download path still works end-to-end.

## Appendix A — Open edX Teams API

### A.1 Authentication

- **Method:** OAuth2 with JWT bearer tokens (primary). Session auth also accepted.
- **Setup:** Create an OAuth2 Application via Django Admin at `/admin/oauth2_provider/application/` on the target Open edX instance. Grant type: `Client Credentials`. Client type: `Confidential`. The user the application is associated with must have staff privileges on the target course for any write/admin operation.
- **Token exchange:** `POST /oauth2/access_token` with Basic auth (Base64 `client_id:client_secret`) → returns a JWT.
- **Request header:** `Authorization: JWT <access_token>` on every subsequent API call.
- **Configuration storage:** instance URL + course ID + `client_id` + `client_secret` go in a new admin-managed config model (per FR-6.3). Secrets should be stored using Django's secret-handling primitives (env vars or the equivalent), never committed.

### A.2 Endpoints we will use

All paths are under the LMS host. The `team_id` is a slugified string assigned by Open edX on creation; we will store it on our local `Team` model for round-tripping.

| Operation                                   | Method   | Path                                                                         | Notes / Permission                                                                                                                                                                                    |
| ------------------------------------------- | -------- | ---------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| List teams in course                        | `GET`    | `/api/team/v0/teams/?course_id={course_id}`                                  | Paginated. Supports `topic_id`, `text_search`, `order_by`, `username`, `expand`.                                                                                                                      |
| Create team                                 | `POST`   | `/api/team/v0/teams/`                                                        | Body: `name`, `course_id`, `description`, `topic_id`, (optional) `country`, `language`, `organization_protected`. Any verified user can create in an open teamset; staff can always create.           |
| Get team                                    | `GET`    | `/api/team/v0/teams/{team_id}`                                               | —                                                                                                                                                                                                     |
| Update team                                 | `PATCH`  | `/api/team/v0/teams/{team_id}`                                               | **Staff only.** `Content-Type: application/merge-patch+json`.                                                                                                                                         |
| Delete team                                 | `DELETE` | `/api/team/v0/teams/{team_id}`                                               | **Staff only.** Cascades to memberships.                                                                                                                                                              |
| List memberships                            | `GET`    | `/api/team/v0/team_membership?team_id={team_id}` *or* `?username={username}` | At least one filter required.                                                                                                                                                                         |
| Add member to team                          | `POST`   | `/api/team/v0/team_membership`                                               | Body: `username`, `team` (= team_id). Staff can add others; learners can only add themselves.                                                                                                         |
| Remove member from team                     | `DELETE` | `/api/team/v0/team_membership/{team_id},{username}`                          | Staff can remove others.                                                                                                                                                                              |
| List topics                                 | `GET`    | `/api/team/v0/topics/?course_id={course_id}`                                 | Topics group teams within a course.                                                                                                                                                                   |
| **Bulk membership upload (killer feature)** | `POST`   | `/api/team/v0/teams/{course_id}/team_membership_csv`                         | **Staff only.** Accepts a CSV upload; Open edX applies the entire team-membership state in one operation. This is essentially the same path the teacher uses manually today, exposed as a programmatic call. |
| **Bulk membership download**                | `GET`    | `/api/team/v0/teams/{course_id}/team_membership_csv`                         | **Staff only.** Returns the current team-membership state as CSV. Useful for syncing our local `Team` state on first load.                                                                            |
| Course roster (enrollments)                 | `GET`    | `/api/enrollment/v1/enrollments?course_id={course_id}`                       | Lists enrolled users for the course. Use for FR-6.1 (replace CSV roster upload). May be capped by an `active_enrollments` threshold on some instances — verify against the target.                    |


### A.3 Sources

- LMS API: https://docs.openedx.org/projects/edx-platform/en/latest/references/lms_apis.html
- Teams package docstrings (definitive reference for `/api/team/v0/`): https://docs.openedx.org/projects/edx-platform/en/latest/references/docstrings/lms/lms.djangoapps.teams.html
- API authentication how-to: https://docs.openedx.org/projects/edx-platform/en/latest/how-tos/use_the_api.html
- eduNEXT teams plugin (corroborates core endpoint shape and exposes a bulk-add convenience we may or may not adopt): https://github.com/eduNEXT/platform-plugin-teams
- Open edX discussion on REST API docs status: https://discuss.openedx.org/t/edx-platform-rest-api-documentation/10746
