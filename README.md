# WikiWare
gurt

^ my beloved gurt will not be removed.

WikiWare is a server-rendered Markdown wiki with per-page branches, revision
history, protected editing, citations, local drafts, and optional S3 image storage.

## Local setup

Install application and development dependencies:

~~~text
pip install -r requirements-dev.txt
~~~

Copy .env.example to .env (copy on Windows, cp on Linux/macOS). The example
enables local HTTP development and uses local files for images. No private
credentials module or S3 account is required.

Start the optional local PostgreSQL container, apply migrations, and start the app:

~~~text
docker compose up -d database
alembic upgrade head
uvicorn src.server:app --reload --host 127.0.0.1 --port 8000
~~~

Open http://127.0.0.1:8000 and register an account. To make that account an
administrator, run this SQL against your database, substituting the username:

~~~sql
UPDATE users SET is_admin = TRUE WHERE username = 'your_username';
~~~

The index.py launcher uses the same HOST, PORT, and DEV configuration as the
application. Static assets and templates resolve relative to the repository.

## Deployment settings

Set DATABASE_URL to an async SQLAlchemy PostgreSQL URL. Ordinary postgres://
and postgresql:// URLs are normalized automatically. Apply alembic upgrade head
before accepting writes.

For production, set DEV=false, a random CSRF_SECRET_KEY of at least 32
characters, SESSION_COOKIE_SECURE=true, and CSRF_COOKIE_SECURE=true, and serve
the app over HTTPS. Use the same CSRF secret across workers. Configure Uvicorn
to trust forwarded headers only from your reverse proxy; rate limiting uses the
server-normalized peer address.

For S3-compatible images, configure S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY,
and S3_BUCKET. Existing installations using src/env_config.py must move their
storage settings into these environment variables. Without S3 configuration,
images live in static/uploads (or UPLOAD_DIR). Persist that directory when
deploying with local storage. S3_PUBLIC_URL is an optional public base URL;
otherwise the app proxies its stored images.

Keep credentials out of version control. The ignore rule does not remove .env
from Git in older clones that already track it; remove its tracking before
putting credentials in that file.

## Editing behavior

The editor keeps Markdown as the source and renders previews using the same
pipeline as published pages, personal pages, history, and PDF export. Formatting
buttons insert Markdown; image captions and widths remain supported.

Saves detect changes made since the editor opened and return a conflict instead
of overwriting them. Keep your draft, open the latest editor, and merge your work.
A successful save clears only the matching local draft; failed saves retain it.
Restores use stable revision IDs and apply the same protection rules as edits.

## Tests and CI

See [test instructions](src/tests/README.md). CI is configured for Python 3.13
and 3.14, PostgreSQL 17, database migrations, backend tests, JavaScript tests,
correctness lint, and CodeQL. The browser suite needs only Node 24:

~~~text
node --test tests/*.test.cjs
~~~

## Migrating MongoDB data

Stop application writes, back up MongoDB and media, and configure MONGODB_URL,
MONGODB_DB_NAME, and the target DATABASE_URL. Apply Alembic to an empty target,
then run migrate_to_postgres.py. The importer uses one PostgreSQL transaction
and refuses a nonempty target.

Compare imported counts, contents, histories, permissions, and media before
cutover. Keep the MongoDB backup until those checks are complete.
migrator.py remains available for legacy MongoDB backups.

Use PostgreSQL's pg_dump and pg_restore for database backups, with an ordinary
PostgreSQL/libpq URL rather than an SQLAlchemy +asyncpg URL. Back up local
uploads or the S3 bucket separately. Collection ZIP downloads are consistent
content snapshots; they exclude accounts, sessions, settings, and image bytes,
so they are not full disaster-recovery backups.

[Architecture and current interfaces](docs/developer-guide.md) ·
[Review and implementation status](docs/rewrite-progress.md)
