# PathFinder (FastHTML)

This project now runs on FastHTML.

## Run

1. Create and activate a virtual environment.
2. Install dependencies:

   pip install -r requirements.txt

3. Start the app:

   Copy `.env.example` to `.env` and fill in `DATABASE_URL` with your Supabase
   Postgres connection string. In Supabase, open **Project Settings > Database**,
   choose the transaction pooler connection string for deployed apps, and set
   the password there. The app loads `.env` automatically on startup.

      postgresql://postgres.[project-ref]:[password]@[pooler-host]:6543/postgres

   Apply `supabase_schema.sql` in the Supabase SQL Editor before the first run.

   python fasthtml_app.py

4. Open:

   http://127.0.0.1:5000

## OCR provider

Install the Docling provider in the same virtual environment:

      pip install docling

Docling is used for every report-card upload:

      python fasthtml_app.py

The first upload initializes Docling and may download its model files. Report cards
use Docling OCR plus the accurate TableFormer table-structure model.

## Notes

- Main app entrypoint: fasthtml_app.py
- Static assets: static/
- HTML templates: templates/
- Database: Supabase Postgres, configured through `DATABASE_URL`

## OAuth Redirect URIs

If Google/GitHub login is enabled in your provider dashboards, ensure callback URLs include:

- http://127.0.0.1:5000/authorize
- http://127.0.0.1:5000/github-authorize

If you also use localhost explicitly, add these too:

- http://localhost:5000/authorize
- http://localhost:5000/github-authorize
