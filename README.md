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
   For an existing database, reapply the schema to replace the old 200-course
   catalog with the 44 university programs. Existing student grades and profiles
   remain in place; legacy course matches are refreshed from saved grades.

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

## Notes

- Main app entrypoint: fastapi_app.py
- Static assets: static/
- HTML templates: templates/
- Database: Supabase Postgres, configured through `DATABASE_URL`

## Admin Roles

- Configure `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD` in `.env` before the first run to create the initial Super Admin. No default admin credentials are committed to the repository.
- Super Admins can manage admin accounts and roles, student records, reports, recommendations, and audit activity.
- Semi Admins can view student, report, recommendation, and analytics pages, but cannot edit records, recalculate recommendations, manage admin accounts, or view the admin audit log.
- Newly created Semi Admins must change their temporary password before accessing the dashboard for the first time.
- New or changed passwords require at least 8 characters, an uppercase letter, and a symbol; the maximum is 72 UTF-8 bytes.
- Admin activity logs record sign-ins and administrative changes, including the session source IP. Logs are retained in `admin_activity_logs`.

## OAuth Redirect URIs

If Google/GitHub login is enabled in your provider dashboards, ensure callback URLs include:

- http://127.0.0.1:5000/authorize
- http://127.0.0.1:5000/github-authorize

If you also use localhost explicitly, add these too:

- http://localhost:5000/authorize
- http://localhost:5000/github-authorize
