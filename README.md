# PathFinder (FastHTML + FastAPI)

FastHTML serves the website pages and templates. FastAPI provides the versioned backend API mounted at `/api/v1`.

## Run

1. Create and activate a virtual environment.
2. Install dependencies:

   pip install -r requirements.txt

3. Start the app:

   Copy `.env.example` to `.env` and fill in `DATABASE_URL` with your Supabase
   Postgres connection string. In Supabase, open **Project Settings > Database**,
   choose the transaction pooler connection string for deployed apps, and set
   the password there. Set `BOOTSTRAP_ADMIN_EMAIL` and a strong
   `BOOTSTRAP_ADMIN_PASSWORD` to create the initial Super Admin on a fresh
   database. The app loads `.env` automatically on startup; subsequent admin
   accounts and roles are managed from the Admin Accounts page.

      postgresql://postgres.[project-ref]:[password]@[pooler-host]:6543/postgres

   Apply `supabase_schema.sql` in the Supabase SQL Editor before the first run.
   For an existing database, reapply the schema to replace the old recommendation
   catalog with 44 university programs and their cluster categories. The obsolete
   course-training table is removed; student grades and profiles remain in place.

   Course deactivation is a soft delete: inactive courses and their cluster
   assignments remain in the admin catalog and can be reactivated. Existing
   databases receive the course-status column automatically on app startup.
   Restart the app after backend changes; the development server does not
   automatically reload Python code. Do not reapply the catalog seed just to
   add this column, since the seed replaces existing courses.

   python fastapi_app.py

4. Open:

   http://127.0.0.1:5000

## API

- FastHTML serves website pages; FastAPI serves backend API routes at `/api/v1`.
- Interactive API documentation is available at `/api/v1/docs`; the OpenAPI schema is at `/api/v1/openapi.json`.
- Browser API calls use the versioned FastAPI routes under `/api/v1`.

## UI motion and layout

Student, Admin, and sign-in pages load `static/UIFlow.css` after their page
styles for shared interaction timing, panel and dialog entrances, fluid spacing,
and mobile adjustments. Navigation scrolling follows CSS `scroll-behavior`
instead of forcing animation in JavaScript. The operating system's
`prefers-reduced-motion` setting disables motion on all three interfaces.
Existing light/dark colors and account permissions are unchanged.

## OCR provider

Install the Docling provider in the same virtual environment:

      pip install docling

Docling is used for every report-card upload:

      python fastapi_app.py

The first upload initializes Docling and may download its model files. Report cards
use Docling OCR plus the accurate TableFormer table-structure model.

For signed-in students, saving another report card adds its reviewed subjects to
the existing academic record. Numbered subjects (such as Physical Education and
Health 1 and 3) remain separate; duplicate subject names retain the saved grade.
The saved-grades view, average, and recommendations use the combined record
immediately after saving. Review corrections take precedence over the original
OCR table.
Recommendations are recalculated and saved after each additional report card. When a student who already has a saved profile uploads another report card, the review step shows the saved subjects that will be kept and lists only the new card's subjects for editing; saving adds them to the same profile instead of starting a new one.
Students who signed in with Google see an **Email my results** button under their
recommendations. It sends only the recommended courses, match scores, and
recommendation explanations to the Gmail address of the signed-in Google account
(never an address typed in the browser), using the same
`SMTP_USERNAME`/`SMTP_PASSWORD` sender as recovery email. Grades and student
academic details are not included. Requests are limited to one per minute per
session. Students who sign in with an email and password do not see the button.
College program labels (such as BSCS-DS) are not SHS clusters: when no SHS cluster
is detected, matching uses the combined grades across the course catalog instead
of inferring an SHS cluster from a college program.
For recognized SHS clusters, recommendations include two separate groups:
cluster-matched courses and grade-based alternatives outside that cluster's catalog
group. Alternatives use only existing database courses and their stored academic
profiles. Both groups require at least one recognized major-related subject field;
supporting subjects alone cannot qualify a course. For example, English alone
cannot qualify Entrepreneurship, and general education subjects cannot qualify
Physical Education. Sports/Physical Education is categorized separately from
general education. Unclassified programs require grades in their highest stored
reference area; flat profiles without a distinct major area do not qualify.
All six observed subject areas can influence ranking, including technology,
business, and social studies. Major-field grade averages contribute 30% of the
score. Relative subject strengths contribute up to 35%, with the remainder based
on observed weighted profile distance. Missing key areas reduce the score;
specific-subject bonuses are capped at six points. Missing grades are not
replaced with zero grades. Candidates are sorted before rounding, and displayed
scores above 95 approach 100 smoothly rather than all being capped at 100.
FCL / Filipino Christian Living and PE / Physical Education are excluded from
both recommendation groups' scoring and strongest-grade calculations. Their OCR
extraction, saved grade rows, and GWA remain unchanged.
Course fields determine major/supporting evidence; stored catalog profiles still
determine profile similarity. The cards and explanations distinguish major-related
subjects from supporting subjects. Fewer results (or none) are shown when the
available grades do not support enough courses; unrelated courses do not fill slots.
Maritime Transportation is assessed using quantitative and physical-science grades,
not business grades alone. Subject labels use whole-term matches, so "Earth" does
not also count as "Art."
The configured result limit applies to primary recommendations; only the single
highest-ranked grade-based alternative is included. Both groups are
saved with recommendation history and restored on reload for signed-in students;
**Saved recommendations** also reopens the saved results after starting another
report-card upload; starting an upload does not delete the saved recommendations.
Guest results remain in the current page. Full recommendation payloads are not
duplicated in session cookies. Regenerate older
recommendations to include alternatives. Similarity scores are guidance, not
admission probabilities.
The active SHS clusters are BE (Business Entrepreneurship), STEM, ICTECH
(Information and Communications Technology), HT (Hospitality and Tourism), and
ARSSH (Arts, Social Sciences, and Humanities).
Clusters are recognized when supplied as standalone student metadata, under a
Cluster label (or a legacy Strand/Track label), or under Course/Major. Older
labels are converted: ABM → BE,
HUMSS → ARSSH, ICT / TVL-ICT → ICTECH, and HE / Home Economics / TVL-HE → HT.
GAS, Sports, plain TVL, Industrial Arts, and Arts & Design are no longer offered;
students with those labels receive grade-based recommendations without a cluster
filter. The original label is shown in the upload preview.
During extraction, the upload screen displays server-reported OCR and result
preparation stages. During processing, the bar displays a clearly labeled
estimated percentage, capped below the next completed-file milestone. It reaches
100% only when results are ready. This is not an exact OCR percentage.
A moving highlight in the bar and a spinner remain
active until the request finishes; both stop on completion or failure.
Interrupted requests show an error rather than completing the bar.
Loading motion and login-button hover/press scaling respect reduced-motion
preferences.
Clients can request newline-delimited progress events from the OCR endpoint with
`Accept: application/x-ndjson`; other clients still receive the usual JSON result.

## Notes

- Cluster-to-course eligibility lives in `university_course_strands` in
  `supabase_schema.sql`. For an existing database, rerun `supabase_schema.sql`
  to replace the old tags with BE, STEM, ICTECH, HT, and ARSSH, then
  restart the app to refresh the cached catalog. Saved settings that still list
  old cluster codes reset to the five active clusters on startup. Regenerate
  recommendations for saved profiles to use the updated eligibility.
- Main app entrypoint: fastapi_app.py
- Static assets: static/
- HTML templates: templates/
- Database: Supabase Postgres, configured through `DATABASE_URL`

## Admin Roles

- Configure `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD` in `.env` before the first run to create the initial Super Admin. No default admin credentials are committed to the repository.
- Super Admins can manage admin accounts and roles, student records, reports, recommendations, and audit activity.
- Semi Admins can view student, report, recommendation, and analytics pages, but cannot edit records, recalculate recommendations, manage admin accounts, or view the admin audit log.
- New admin accounts use a unique username and temporary password; existing email-based sign-in remains available. The initial Super Admin can still be bootstrapped with `BOOTSTRAP_ADMIN_EMAIL`.
- Newly created Semi Admins change their temporary password in a required dashboard pop-up before accessing admin data.
- Super Admins and Semi Admins can change their own password under **Account settings > Account > Change password**. Select **Account** to expand or hide its submenu. Enter the current password and confirm the new password. Recovery Gmail setup is in its own **Account > Recovery Gmail** section.
- When students or admins change their profile picture, a crop dialog opens first. Drag to reposition, zoom with the slider, mouse wheel, or pinch, rotate or reset, then select **Save picture** to upload a 512×512 square image. **Cancel** or Esc leaves the current picture unchanged.
- Admin username/password sign-in locks the account after each group of three
  failed attempts: 30 seconds, 1 minute, 2 minutes, 4 minutes, 8 minutes, then a
  15-minute maximum. Attempts during a cooldown do not extend it. State is stored
  in Postgres and shared across browsers and server restarts. A successful
  password sign-in or password reset clears the escalation. Student, Google,
  and guest sign-in are unchanged. Incorrect current-password checks when adding
  recovery Gmail share the same cooldown.
- In **Account settings > Account > Recovery Gmail**, admins can enter a Gmail
  address and their current password, then confirm the emailed verification link
  within 30 minutes. A replacement address does not replace the existing one
  until verified. Recovery Gmail is separate from the sign-in email/username.
- **Forgot password?** under the admin sign-in fields emails a single-use,
  15-minute reset link to the verified recovery Gmail. Requests are limited to
  one email per account per minute. New links replace previous links of the same
  type. Resetting the password also clears lockout, invalidates outstanding
  verification/reset links, and revokes existing admin sessions. Without a
  previously verified recovery Gmail, an admin must contact a Super Admin.
  The request accepts an admin username, sign-in email, or verified recovery
  Gmail; pending, unverified addresses cannot be used. If an address matches
  another admin's sign-in email as well as a recovery Gmail, sign-in identifiers
  take precedence even when that account is inactive or has no verified recovery
  Gmail; use the intended admin's username to avoid ambiguity.
  The generic response does not disclose whether an account exists or whether a
  request was throttled. After confirming a recovery Gmail, the same signed-in
  admin returns to Account settings; a different or signed-out browser shows
  sign-in instead. Confirmation is still required after opening the link.
- Configure Gmail delivery in `.env` (never commit sender credentials):
  `SMTP_USERNAME` is the dedicated sender Gmail; `SMTP_PASSWORD` is its Google
  App Password, created after enabling Google two-step verification. Delivery
  uses `smtp.gmail.com:587` with STARTTLS. Set `APP_BASE_URL` to
  `http://127.0.0.1:5000` locally or the public HTTPS origin in production.
  Local links work only on the computer running the app. Missing configuration
  or delivery failures are reported; links are never displayed as a fallback.
  To enable delivery locally:
  1. Enable two-step verification on the dedicated sender Google account.
  2. Create a Google App Password (not the normal Gmail sign-in password).
  3. Add `SMTP_USERNAME=your-sender@gmail.com`, `SMTP_PASSWORD=your-app-password`,
     and `APP_BASE_URL=http://127.0.0.1:5000` to your local `.env`.
     Keep these credentials out of chat and source control.
  4. Restart the development server; changing `.env` alone does not reload the
     Python process. In Account settings, send a verification link and check the
     recipient's inbox and spam folder.

  Hosts that block outbound SMTP (for example Railway Free/Trial/Hobby plans,
  which fail with `OSError`) must use the Brevo HTTPS API instead. When
  `BREVO_API_KEY` is set, all email is sent through Brevo and `SMTP_PASSWORD`
  is not needed:
  1. Create a free Brevo account and add your sender Gmail under
     **Senders, Domains & Dedicated IPs → Senders**, then confirm it.
  2. Create a key under **SMTP & API → API Keys**.
  3. On the host, set `BREVO_API_KEY`, `EMAIL_SENDER=your-verified-sender@gmail.com`
     (falls back to `SMTP_USERNAME`), optional `EMAIL_SENDER_NAME=PathFinder`,
     and `APP_BASE_URL=https://your-app-domain`, then redeploy.

  The new security table and `users.auth_version` column are created by normal
  app startup (or by applying the corresponding additions in
  `supabase_schema.sql`).
- Recovery tests use an isolated in-memory database by default. To validate
  their SQL against Postgres without touching application records, set
  `RUN_POSTGRES_AUTH_TESTS=1` and run
  `python -m pytest tests/Test_admin_recovery.py -q`; this mode uses only
  connection-local temporary tables.
- Super Admins and Semi Admins can generate, print, and download PDF reports from the Reports page.
  Report headers show **PathFinder** with **University of Perpetual Help System
  Dalta** beneath it. Downloaded PDFs repeat this branding on every page, while
  printed reports exclude the dashboard navigation and controls.
- Newly created, changed, or reset admin passwords require all five rules:
  at least 8 characters, an uppercase letter, a lowercase letter, a number, and
  a symbol. The maximum remains 72 UTF-8 bytes. All admin new-password fields
  show a live checklist; existing passwords remain valid for sign-in. Student
  password requirements are unchanged.
- Admin activity logs record sign-ins and administrative changes, including the session source IP. Logs are retained in `admin_activity_logs`.
  Super Admins can export the filtered activity log as PDF or Excel. The Excel
  workbook includes the PathFinder and school branding, export filters and
  entry count, with sortable columns and print-ready landscape pages.
- Super Admins manage the recommendation catalog under **System Settings >
  Course Catalog**. Select **System Settings** in the sidebar to expand or
  collapse its submenu. Search courses by name, description, or cluster, filter
  by academic cluster, and use the page and rows-per-page controls to browse
  the results. **Add new course** asks for a name, a description, the
  academic clusters that fit it, and six ideal subject-area scores (Math,
  Science, English, Technology, Business, Social; 0–100). **Start from a
  similar course** copies an existing course's scores so they only need to be
  adjusted. Courses can also be edited or removed (the last course cannot be
  removed). Saving an edit opens a confirmation listing the changed fields, and
  removing a course requires typing `delete` to confirm. Changes apply to new
  recommendations immediately, without a restart,
  and are recorded in the admin activity log. Removing or renaming a course
  hides it from previously saved recommendation results. Semi Admins cannot
  access the catalog.

## OAuth Redirect URIs

If Google/GitHub login is enabled in your provider dashboards, ensure callback URLs include:

- http://127.0.0.1:5000/authorize
- http://127.0.0.1:5000/github-authorize

If you also use localhost explicitly, add these too:

- http://localhost:5000/authorize
- http://localhost:5000/github-authorize
