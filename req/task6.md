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

The goal of this task is to **make manual adjustments first-class inside the Team Matcher itself**, and — as a connected improvement — to support partial re-matching.

**Intended outcome:** the teacher can perform all team adjustments inside the Team Matcher UI and export a ready-to-upload CSV without ever opening Excel.

## 2. Goals & Non-Goals

### In scope

- **G1.** Visual, drag-and-drop reassignment of students between teams after generation.
- **G2.** Persistent team representation in the database (teams survive page reloads and are no longer reconstructed from CSV text on every view).
- **G3.** Live editing with an explicit "Download CSV" step that snapshots the current state for upload to Open edX.
- **G4.** Partial re-matching: the teacher can lock some teams or students and re-run the matcher on the remainder.
- **G5.** Constraint feedback during editing (min/max team size violations) as **soft warnings the teacher can override**.

### Out of scope (this iteration)

- Direct Open edX API integration (pull roster, push teams) — see §12 Nice-to-Have.
- Changes to the genetic-algorithm fitness function beyond what is required to accept locked-team constraints and priority inputs.
- Real-time multi-teacher collaborative editing (only one teacher is currently expected to edit at a time).
- Editing student profile data from the team-adjustment view (still managed via Django admin and the student form).
- Bulk team operations beyond what drag-and-drop naturally affords (e.g., "shuffle all", "swap two teams whole-cloth").
- Mobile-first experience. Desktop browser is the primary target.

## 3. Stakeholders & Personas

| Persona           | Role                                       | Primary use cases addressed                                                               |
| ----------------- | ------------------------------------------ | ----------------------------------------------------------------------------------------- |
| **Teacher**       | Owns team formation. Staff-authenticated.  | G1–G5. Performs all manual adjustments, triggers re-matches, exports CSV.                 |
| **Administrator** | Django superuser.                          | Manages `TeamNameTemplate` and `Task` via Django admin.                                   |

Access control: manual adjustment views are **teacher-only**, gated by Django's `staff_member_required` decorator, matching the existing `/teacher/` view's access model.

## 4. Functional Requirements

### FR-1. Persistent team representation
- **FR-1.1** The system must persist team composition in the database. Teams must not be derived solely from CSV text.
- **FR-1.2** A team has, at minimum: a name (from `TeamNameTemplate` or auto-generated), a set of student members, a creation timestamp, and a flag indicating whether it is "locked" (excluded from future re-matches).
- **FR-1.3** A student belongs to at most one team within a given matching session. Moving a student to a new team must atomically remove them from any previous team.
- **FR-1.4** The system must continue to support historical `CSVGeneration` records as immutable export snapshots, and the **live working state** is the persistent team data, not a CSV blob.

### FR-2. Drag-and-drop manual reassignment
- **FR-2.1** The teacher view must display all current teams as columns or cards, each listing the students assigned to that team with enough identifying information to be recognizable (at minimum: student ID; ideally also any name field available from the roster CSV).
- **FR-2.2** The teacher must be able to drag a student from one team and drop them onto another team. The change must persist immediately (no separate save step).
- **FR-2.3** The system must support dragging a student into an "unassigned" pool and back into any team, to support reorganizations that temporarily leave a student team-less.
- **FR-2.4** Drag operations must be visually confirmed (highlight target team while hovering; smooth reorder; clear feedback on successful drop or failure).
- **FR-2.5** The view must show, per team, the current member count and a visual indication when the team is below the min size or above the max size set at generation time.

### FR-3. Live editing with explicit export step
- **FR-3.1** All manual edits modify the live (working) team state. There is no draft/published distinction during editing.
- **FR-3.2** A clearly labeled **"Download CSV"** action must produce a fresh `CSVGeneration` record reflecting the current live state and trigger a file download, ready for upload to Open edX.
- **FR-3.3** Each export must create an immutable **audit record** capturing the team composition at that point in time and when it was exported. The existing `CSVGeneration` model is the natural fit for this.
- **FR-3.4** Manual edits performed after an export do **not** retroactively modify the snapshot — a new export must be triggered to produce an updated CSV.

### FR-4. Partial re-matching
- **FR-4.1** The teacher must be able to mark individual teams or individual students as **locked**. Locked entities are excluded from any subsequent re-match run.
- **FR-4.2** A "Re-match unlocked" action runs the genetic matcher only over the unlocked students, distributing them across (a) existing unlocked teams with open slots, and/or (b) newly created teams, subject to the same min/max size constraints.
- **FR-4.3** The teacher may adjust the criteria weights for the re-match independently of the initial generation's weights.
- **FR-4.4** Re-matching must use the **same fitness function** as initial matching, respecting locked-team membership as a hard constraint.

### FR-5. Validation & constraint feedback
- **FR-5.1** While editing, the system must show — per team and overall — the current min/max size violation status.
- **FR-5.2** Drag-and-drop operations that violate min/max are permitted ("soft warning, allow override"). The system must visually mark violating teams but must not refuse the drop.
- **FR-5.3** On Download CSV, if any team violates min/max, the teacher must be shown a summary of violations and asked to confirm before the download proceeds.
- **FR-5.4** The system must prevent a student from appearing on two teams simultaneously (this is a hard constraint, not a soft warning).

## 5. Non-Functional Requirements

- **NFR-1. Performance.** Drag-and-drop must feel instant on a roster of up to 200 students across up to 50 teams (much higher than the typical course size). Persistence after a drop should complete within ~300 ms on a local network; the UI must not block.
- **NFR-2. Browser support.** Latest Chrome, Firefox, Edge, Safari. No IE.
- **NFR-3. Visual consistency.** New views must match the existing dark-themed Bootstrap 5 design system already used in `/teacher/` and `/student/` (gradient buttons, dark slate cards, Inter font, Bootstrap Icons).
- **NFR-4. Security.** All manual-adjustment endpoints are gated by `staff_member_required`. CSRF protection on all state-mutating endpoints.
- **NFR-5. Resilience.** Partial failures during a re-match must leave the system in a recoverable state (no half-applied edits to live data).
- **NFR-6. Auditability.** Each Download CSV action creates an immutable `CSVGeneration` record, providing a built-in trail of what was exported and when.

## 6. Data Requirements

The following data must be representable in the system.

- **Team** — a named group of students, with a lock flag and team-size constraint metadata.
- **Team membership** — the link between `StudentProfile` and `Team`; must enforce one-team-per-student.
- **Unassigned pool** — a logical bucket for students temporarily without a team; not necessarily a distinct DB object, but the UI must surface it.
- **CSVGeneration (existing)** — reused as the export snapshot and audit record. The live team state (not a CSV blob) is the canonical store of *current* assignments; `CSVGeneration` is written only on explicit export.

### Migration considerations

- Existing `CSVGeneration` records are pure text and have no `Team` rows. Treat historical records as read-only legacy; no back-fill required.
- The existing matching algorithm currently emits a CSV; it will need to also produce persistent `Team` rows after generation. Backward compatibility for the existing index view should be maintained until the new view supersedes it.

## 7. UI / UX Requirements

- **UX-1.** The manual-adjustment view is reachable from the existing teacher dashboard (`/teacher/`) immediately after a generation completes, and accessible later from the historical-generations list.
- **UX-2.** Each team is rendered as a card or column. Cards display: team name, member count, size-violation indicator, lock toggle, and the list of member students (draggable items).
- **UX-3.** Each student item shows the information the teacher needs to recognize them (at minimum: student ID; preferably also availability summary and commitment level) and includes a **lock toggle** so individual students can be excluded from re-matching independently of their team's lock state.
- **UX-4.** A persistent "Unassigned" zone is always visible.
- **UX-5.** A toolbar exposes: **Re-match unlocked** and **Download CSV**.
- **UX-6.** The criteria-weight form is available again in the re-match dialog, pre-populated with the values from the originating generation but editable per re-match.
- **UX-7.** Constraint warnings are visually subtle but unmissable: an icon + color on the violating team card, plus a summary count in the toolbar.
- **UX-8.** Destructive actions (e.g., delete a team, clear all assignments) require confirmation.

## 8. Constraints & Assumptions

- The existing genetic-algorithm matcher (`teacher_side/matcher/`) is the source of truth for generating assignments and will be reused for re-matching. Its fitness function will be extended, not replaced.
- Only one teacher is expected to edit the live team state at a time. No multi-user merge logic is required.
- Students identify themselves via `student_id` (= the `username` column in the LMS CSV) on the public student form. No login is required.

## 9. Risks & Open Questions

| #  | Risk / Question                                                           | Notes                                                                                                            |
| -- | ------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| R1 | A locked team that violates min/max size after lock can't be re-balanced. | Documented as expected behavior; locking is the teacher's explicit choice.                                       |
| R2 | Existing `CSVGeneration` history has no associated `Team` rows.           | Treat as read-only legacy; no back-fill required.                                                                |

## 10. Acceptance Criteria

The feature is complete when **all** of the following are true:

1. After generating teams, the teacher can open a view that displays all teams and all student-team memberships, sourced from the database (not parsed from CSV text on every load).
2. The teacher can drag a student from one team to another, and the change persists across a page reload.
3. The teacher can drag a student into an "Unassigned" pool and back into any team.
4. Min/max size violations are visible per team during editing and summarized before CSV download, but do not block edits.
5. The teacher can lock specific teams or students and run a re-match that respects those locks and leaves locked entities untouched.
6. Download CSV produces a valid file that can be uploaded directly to Open edX without further editing. The file preserves all original columns (`username`, `external_user_id`, `mode`, `cp`, `wp`) and adds the team name. Each download creates an immutable `CSVGeneration` record.
7. All new endpoints are gated by `staff_member_required`. CSRF protection is enforced on all mutating endpoints.
8. The new views match the existing dark-themed visual design of the tool.

## 11. Verification

Verification has two layers: **automated unit/integration tests** (run on every change) and **end-to-end walkthroughs** (run before declaring the feature done).

### 11.1 Automated tests

The existing `student_side/tests.py` and `teacher_side/tests.py` are empty placeholders. This iteration must add Django `TestCase`-based tests covering at least:

**Models & data integrity**
- A student can belong to at most one team at any time (FR-1.3). Assigning to a second team removes the previous membership atomically.
- Locking a team or student is persisted and survives reload (FR-4.1).
**Team-edit endpoints**
- Moving a student between teams via the reassignment endpoint returns success and the database reflects the change.
- Moving a non-existent student or to a non-existent team returns a clear error and does not corrupt state.
- All edit endpoints are gated by `staff_member_required`: a request without staff credentials returns 302/403 (NFR-4).
- All edit endpoints reject requests without a valid CSRF token (NFR-4).

**Export / audit (FR-3)**
- Download CSV creates exactly one `CSVGeneration` record per call, capturing the team state at that moment.
- Subsequent edits do not modify a prior `CSVGeneration` record (FR-3.4).

**Re-matching (FR-4)**
- Locked teams' membership is byte-for-byte identical before and after a re-match run.
- Re-match operates only over unlocked students; unlocked students missing from any team end up assigned.
- A re-match where every team is locked is a no-op and produces no error.

**Constraint validation (FR-5)**
- Teams below min size and above max size are correctly flagged.
- A drag operation that would create a violation succeeds (soft warning, not hard block).
- A drag operation that would place a student on two teams simultaneously fails (hard constraint).

**Regression**
- The existing genetic-matcher fitness function, given the same input and RNG seed, produces identical output to the current `main` branch. A small fixture-based test pinning the current behavior should be added so any future fitness changes are deliberate.

Target: tests run in under 30 seconds locally, executable via `python manage.py test`. CI integration is out of scope for this iteration but the test suite must be CI-ready.

### 11.2 End-to-end walkthroughs

- **Manual UX walkthrough.** Upload roster CSV → generate → manually adjust via drag-and-drop → lock a team → re-match the rest → Download CSV → upload to Open edX. Confirm no Excel detour is needed.
- **Constraint-violation walkthrough.** Drag students until a team is below min and another is above max; confirm warnings appear, edits persist, and Download CSV prompts for confirmation.
- **Lock-respect walkthrough.** Lock a team; run a re-match; confirm the locked team's membership is byte-for-byte identical before and after.
- **CSV format walkthrough.** Open the downloaded CSV and verify it matches the LMS template format: all original columns preserved, team name added, file uploads to Open edX without error.

---

## 12. Nice-to-Have (Future Work)

The following features are explicitly **out of scope for this iteration** but are documented here for future planning.

### 12.1 Student priorities in re-matching

Allow students to express preferences that influence the re-match, without affecting initial generation.

- **Peer nominations:** students name up to 3 preferred teammates. The re-matcher treats these as soft constraints (prefer co-assignment, never violate hard constraints to enforce it).
- **Project preferences:** students rank preferred tasks; integrated with the existing task-interest similarity criterion with teacher-adjustable weight.
- Priorities collected via the existing `/student/` form; stored against `StudentProfile`; visible to the teacher in the adjustment view.
- Not reciprocity-required: A nominating B does not require B to nominate A.
- Invalid nominations (student ID not in roster) are silently ignored.

### 12.2 Open edX API integration (two-way)

Replace the CSV upload/download round-trip with direct API calls to Open edX, eliminating the manual upload step entirely.

- Pull the course roster directly from Open edX via API, replacing CSV upload.
- Push finalized team assignments back to Open edX via API, replacing CSV download + manual upload.
- API credentials and the target Open edX instance URL configurable via Django admin.
- Idempotent push: re-pushing the same final state must not duplicate assignments.
- Clear error messages for auth failures, network errors, and partial failures.

**Key finding from API investigation (Appendix A):** the `POST /api/team/v0/teams/{course_id}/team_membership_csv` endpoint accepts the same CSV format the tool already produces, so the push step can reuse the existing CSV serialization with minimal additional work.

### 12.3 API-related tests (when 12.2 is implemented)

- API client tests using mocked HTTP responses — no real network calls.
- CSV round-trip test: the file produced locally matches the format Open edX's `team_membership_csv` endpoint expects.
- Auth failure surfaces an actionable error, not a 500.
- Network failure during push leaves local team state unchanged.

---

## Appendix A — Open edX Teams API

Research conducted for future implementation of §12.1. All findings verified against official Open edX documentation.

### A.1 Authentication

- **Method:** OAuth2 with JWT bearer tokens (primary). Session auth also accepted.
- **Setup:** Create an OAuth2 Application via Django Admin at `/admin/oauth2_provider/application/` on the target Open edX instance. Grant type: `Client Credentials`. Client type: `Confidential`. The associated user must have staff privileges on the target course for any write/admin operation.
- **Token exchange:** `POST /oauth2/access_token` with Basic auth (Base64 `client_id:client_secret`) → returns a JWT.
- **Request header:** `Authorization: JWT <access_token>` on every subsequent API call.

### A.2 Endpoints

All paths are under the LMS host.

| Operation                        | Method   | Path                                                                         | Notes / Permission                                                                                                   |
| -------------------------------- | -------- | ---------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| List teams in course             | `GET`    | `/api/team/v0/teams/?course_id={course_id}`                                  | Paginated. Supports `topic_id`, `text_search`, `order_by`, `username`, `expand`.                                     |
| Create team                      | `POST`   | `/api/team/v0/teams/`                                                        | Body: `name`, `course_id`, `description`, `topic_id`.                                                                |
| Update team                      | `PATCH`  | `/api/team/v0/teams/{team_id}`                                               | **Staff only.** `Content-Type: application/merge-patch+json`.                                                        |
| Delete team                      | `DELETE` | `/api/team/v0/teams/{team_id}`                                               | **Staff only.** Cascades to memberships.                                                                             |
| Add member to team               | `POST`   | `/api/team/v0/team_membership`                                               | Body: `username`, `team`. Staff can add others.                                                                      |
| Remove member from team          | `DELETE` | `/api/team/v0/team_membership/{team_id},{username}`                          | Staff can remove others.                                                                                             |
| **Bulk membership upload**       | `POST`   | `/api/team/v0/teams/{course_id}/team_membership_csv`                         | **Staff only.** Accepts CSV; applies full team-membership state in one call. Reuses the CSV the tool already produces. |
| **Bulk membership download**     | `GET`    | `/api/team/v0/teams/{course_id}/team_membership_csv`                         | **Staff only.** Returns current membership state as CSV.                                                             |
| Course roster                    | `GET`    | `/api/enrollment/v1/enrollments?course_id={course_id}`                       | Lists enrolled users. May be capped on some instances.                                                               |

### A.3 Sources

- LMS API: https://docs.openedx.org/projects/edx-platform/en/latest/references/lms_apis.html
- Teams package docstrings: https://docs.openedx.org/projects/edx-platform/en/latest/references/docstrings/lms/lms.djangoapps.teams.html
- API authentication how-to: https://docs.openedx.org/projects/edx-platform/en/latest/how-tos/use_the_api.html
- eduNEXT teams plugin: https://github.com/eduNEXT/platform-plugin-teams
- Open edX API discussion: https://discuss.openedx.org/t/edx-platform-rest-api-documentation/10746

---

## Appendix B — LMS CSV Format

Source: `req/team-membership-template.csv` and `req/email_from_Thomas.txt`.

### B.1 Input file (exported from LMS, uploaded to Team Matcher)

| Column             | Always present | Value                                      |
| ------------------ | -------------- | ------------------------------------------ |
| `username`         | yes            | Student ID (maps to `StudentProfile.student_id`) |
| `external_user_id` | yes            | Always empty                               |
| `mode`             | yes            | Always `"professional"`                    |
| `cp`               | yes            | Project assignment, generated by LMS       |
| `wp`               | yes            | Project assignment, generated by LMS       |

Example:
```
username,external_user_id,mode,cp,wp
s-001085,,professional,Alfa,Alfa
s-01123,,professional,Alfa,Alfa
```

### B.2 Output file (downloaded from Team Matcher, imported to LMS)

Identical to the input file with one addition: a team name column appended. All original columns and values are preserved unchanged. The LMS only reads the team name column on import; all other columns pass through unmodified.

The only change the Team Matcher makes to the file is adding the team names.

---

## Appendix C — Plan: Soft-Constraint Warnings for All GA Criteria

This appendix records the agreed plan for extending the manual-adjustment view's existing team-size warning to cover all 9 GA criteria.

### C.1 Overview

Extend the existing team-size soft-constraint check on `adjust.html` to cover all 9 GA criteria (availability, commitment, job, education, age, gender, experience, lead, tasks). Each weighted criterion becomes a per-team check that, when violated, surfaces as an inline warning icon in that team's column header and gates CSV export.

### C.2 Violation Rules

A criterion's check is only evaluated for teams when the corresponding `session.weights[key] > 0`.

- **`size`** *(unchanged)* — team member count outside `[min_size, max_size]`.
- **`availability`** — 0 time slots where every team member is free (no perfect-overlap slot).
- **`commitment`** — standard deviation of commitment levels ≥ 1.0 across the team (members at very different commitment levels).
- **`job`** — only 1 unique professional background across the team.
- **`education`** — only 1 unique educational background across the team.
- **`age`** — age standard deviation < 1.0 (no meaningful spread).
- **`gender`** — only 1 unique gender.
- **`experience`** — only 1 unique experience level.
- **`lead`** — number of "lead"-preference members ≠ 1 (i.e. zero leaders or 2+ leaders).
- **`tasks`** — max agreement on any single task < 50% of team members.

Empty teams (count = 0) only trigger the `size` violation; other criteria are not evaluated. Single-member teams skip diversity / std-based criteria (always pass for those) — only `size`, `lead`, and `availability` apply to size-1 teams.

### C.3 Backend Changes

- New module `teacher_side/matcher/violations.py` exposes:
  - A canonical `VIOLATION_CODES` constant and human-readable label/icon map.
  - `compute_session_violations(session) -> {team_id: [codes...]}` — encodes all members once, slices by team.
- Reuses the existing `prepare_data` / `encode_student` logic from `encoder.py` so violation checks stay consistent with the GA's scoring (no duplicate encoding rules).
- `adjust_teams` view passes `team_violations` to the template plus a `violation_count` reflecting the total number of teams with at least one violation (any code, not just size).
- `api_move_student` returns `from_violations` and `to_violations` (lists of codes) for the affected teams alongside the existing `from_count` / `to_count`.
- `export_csv` extends the 409 "violations" response so each entry includes `name`, `count`, `min`, `max`, **and `codes`** (list of violation codes). Blocks & confirms if **any** team has any violation, not just size.

### C.4 Frontend Changes (`adjust.html`)

- Each team column header renders a row of inline warning icons — one Bootstrap icon per failing constraint, each with a Bootstrap tooltip giving a human-readable reason.
- The size badge keeps its current red style when size is violated; new criteria use small icon-only warning chips next to the size badge.
- Outer `.team-column.violation` red highlight applies if **any** code is present (not just size).
- Toolbar `violation_count` badge counts teams with any violation.
- After a drop, both source and target columns' icons re-render from the new `from_violations` / `to_violations` returned by `api_move_student` (no full reload).
- Download confirm modal lists each violating team plus a comma-separated list of human-readable reasons. Heading and button copy generalized from "Size Violations Detected" → "Constraint Violations Detected".

### C.5 Notes

- Thresholds are deliberately strict — they fire when that criterion's contribution to the GA fitness is essentially zero, so icons remain meaningful and don't appear on every team.
- All thresholds, code list, and tooltip strings live in one place (`violations.py`) plus a small mirrored JS map in `adjust.html`, so adjustments later are one-line edits.
- No DB schema changes — violations are computed on demand from existing `MatchingSession` / `Team` / `TeamMembership` / `StudentProfile` data.
- Tests should cover: empty team, single-member team, all-same-gender team, perfectly-balanced team, weight=0 suppresses the corresponding code, lead count of 0/1/2+. Existing tests for `api_move_student` and `export_csv` should be extended to assert the new fields.

