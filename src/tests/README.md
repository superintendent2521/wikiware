# WikiWare tests

Install development dependencies with `pip install -r requirements-dev.txt`.
The maintained suites run in-process and do not require a running wiki or S3.
The former live-server smoke script has been replaced; tests no longer create
users and pages in a development installation.

Run `pytest` from the repository root. PostgreSQL tests skip unless
`WIKIWARE_TEST_DATABASE_URL` is explicitly set. Run `pytest -m "not integration"`
for only isolated HTTP, configuration, rendering, and schema checks.

## PostgreSQL integration

Use a dedicated database whose name contains `test`, for example
`postgresql+asyncpg://postgres:postgres@localhost:5432/wikiware_test`.
Set `WIKIWARE_TEST_DATABASE_URL` to that URL before running `pytest -m integration`.
The fixture rejects other database names. Each test creates its own random
`wikiware_test_...` schema, sets that schema as its connection search path, and
drops only that schema during cleanup. It never falls back to `DATABASE_URL`.

These suites verify:

- registered accounts persist, passwords authenticate, and sessions expire or
  become invalid after logout;
- password changes persist and favorites follow renames and deletions;
- revision snapshots and edit counts commit together;
- stale and simultaneous saves cannot overwrite a newer edit;
- database policy is rechecked rather than trusting caller roles;
- failed transactions roll back content and history;
- restores identify a stable revision and reject revisions from other branches;
- branch creation retains more than 100 revisions;
- page deletion cascades to dependent branches and revisions;
- the Mongo importer preserves content, history order, permissions, favorites,
  sessions, image metadata, and settings;
- nonempty targets are refused and import failures roll back the whole import.
- exports unzip successfully, share a consistent snapshot while writes happen,
  reserve cooldowns atomically, and release interrupted reservations safely.
- search treats LIKE wildcard characters literally, ranks exact/prefix titles,
  scopes results by branch, and pages deterministically when timestamps tie.

The importer tests use a read-only in-memory Mongo source and a real PostgreSQL
target, so no MongoDB server or production credentials are needed.

## HTTP and rendering

The HTTP suites use real FastAPI routers and dependency injection with isolated
service doubles. They cover authentication, page protection, feature flags,
CSRF rejection, version/revision identity forwarding, JSON save acknowledgements,
recoverable HTML save errors, and authenticated previews. The renderer fixtures
cover wiki links, literal code, nested headings, tables, image captions and widths,
citations, unsafe URLs/HTML, statistics placeholders, and source escaping.
Secondary suites reject remote/filesystem PDF image sources, enforce image byte
and count limits, validate admin log-stream origins, check API mutation CSRF,
and verify that the web image library awaits its storage listing.

Run `node --test tests/*.test.cjs` for editor and draft recovery tests. No npm
dependencies are needed. `node --check static/editor.js` checks editor syntax.

## CI

The CI workflow runs backend tests on Python 3.13 and 3.14 with PostgreSQL 17,
applies Alembic to an empty database, checks installed dependency compatibility,
runs Ruff correctness checks and maintained-suite lint, and runs Node browser
logic tests. Test results and coverage reports are retained for seven days.
Coverage is reported without an arbitrary repository-wide percentage gate.
CodeQL analyzes Python, JavaScript, and Actions on pull requests and weekly.
Dependabot proposes weekly Actions and Python dependency updates.

Local verification during the current rewrite uses native Ruff and Node tools;
the repository instruction against running Python locally remains in effect.
The Python suites and GitHub workflows still need their first execution in CI.
