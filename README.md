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
   catalog with 44 university programs and their strand categories. The obsolete
   course-training table is removed; student grades and profiles remain in place.

   python fastapi_app.py

4. Open:

   http://127.0.0.1:5000

## API

- FastHTML serves website pages; FastAPI serves backend API routes at `/api/v1`.
- Interactive API documentation is available at `/api/v1/docs`; the OpenAPI schema is at `/api/v1/openapi.json`.
- Browser API calls use the versioned FastAPI routes under `/api/v1`.

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
Recommendations are recalculated and saved after each additional report card.
College program labels (such as BSCS-DS) are not SHS strands: when no SHS strand
is detected, matching uses the combined grades across the course catalog instead
of inferring an SHS strand from a college program.
For recognized SHS strands, recommendations include two separate groups:
strand-matched courses and grade-based alternatives outside that strand's catalog
group. Alternatives use only existing database courses and their stored academic
profiles. Only Math, Science, English, and their recognized branches determine
the best grade-based alternative. Technology, business, social studies, and
foreign-language grades do not influence it; strand-matched scoring is unchanged.
Recognized branches include algebra, calculus, geometry, statistics, probability,
physics, chemistry, biology, earth/life sciences, reading, writing, literature,
speech, and oral communication. Observed core-category grades are compared using weighted root-mean-square
distance; weights are `(category average - lowest observed category average + 1)^2`,
so stronger graded areas have more influence. Final alternative scores combine
85% of this strength-weighted distance with 15% equal-weight distance across all
observed categories, allowing full-profile fit to distinguish close matches.
Candidates are sorted using unrounded distances; full-profile distance breaks
exact ties before the course name. Displayed match scores use two decimal places.
Missing categories are excluded from both distances.
FCL / Filipino Christian Living and PE / Physical Education are excluded from
both recommendation groups' scoring and strongest-grade calculations. Their OCR
extraction, saved grade rows, and GWA remain unchanged.
No course-name-to-subject rules determine the alternatives' ranking.
The configured result limit applies to primary recommendations; only the single
highest-ranked grade-based alternative is included. Both groups are
saved with recommendation history and restored on reload for signed-in students;
guest results remain in the current page. Full recommendation payloads are not
duplicated in session cookies. Regenerate older
recommendations to include alternatives. Similarity scores are guidance, not
admission probabilities.
The active SHS strands are BE (Business Entrepreneurship), STEM, ICTech,
HT (Hospitality and Tourism), and ARSSH (Arts, Social Sciences, and Humanities).
Strands are recognized when supplied as standalone student metadata, under a
Strand label, or under Course/Major. Older labels are converted: ABM → BE,
HUMSS → ARSSH, ICT / TVL-ICT → ICTech, and HE / Home Economics / TVL-HE → HT.
GAS, Sports, plain TVL, Industrial Arts, and Arts & Design are no longer offered;
students with those labels receive grade-based recommendations without a strand
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

- Strand-to-course eligibility lives in `university_course_strands` in
  `supabase_schema.sql`. For an existing database, rerun `supabase_schema.sql`
  to replace the old strand tags with BE, STEM, ICTech, HT, and ARSSH, then
  restart the app to refresh the cached catalog. Saved settings that still list
  old strands reset to the five active strands on startup. Regenerate
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
- Super Admins and Semi Admins can change their own password from the dashboard profile menu by selecting **Change password** (also available under **Account settings > Account**). Enter the current password and confirm the new password.
- Super Admins and Semi Admins can generate, print, and download PDF reports from the Reports page.
- New or changed passwords require at least 8 characters, an uppercase letter, and a symbol; the maximum is 72 UTF-8 bytes.
- Admin activity logs record sign-ins and administrative changes, including the session source IP. Logs are retained in `admin_activity_logs`.

## OAuth Redirect URIs

If Google/GitHub login is enabled in your provider dashboards, ensure callback URLs include:

- http://127.0.0.1:5000/authorize
- http://127.0.0.1:5000/github-authorize

If you also use localhost explicitly, add these too:

- http://localhost:5000/authorize
- http://localhost:5000/github-authorize
