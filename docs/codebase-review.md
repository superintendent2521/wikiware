# WikiWare codebase review

See [implementation status](rewrite-progress.md) for the completed source changes,
local checks, and remaining runtime/data verification. The findings below describe
the original review baseline.

Reviewed 2026-11-01. This is a static review of the current working tree,
including the uncommitted PostgreSQL migration. No application code was changed
and no Python commands, application servers, migrations, or tests were run.
The findings below distinguish code-path evidence from proposed improvements;
runtime behavior and migration correctness still need verification.

## Recommendation

Keep the product concepts, content, and much of the PostgreSQL foundation.
Rebuild selected subsystems incrementally. A complete replacement would spend
considerable effort rediscovering the existing content format and behavior.
The largest problems are inconsistent rules between entry points, duplicated
rendering logic, and failures being disguised as empty results.

## How the application fits together

- `index.py` loads environment variables and starts Uvicorn.
- `src/server.py` creates FastAPI, registers web/API routers, installs security
  and settings middleware, and connects the database during startup.
- `src/routes/web/` renders Jinja templates and handles HTML forms.
  `src/routes/api/` supports browser widgets, uploads, favorites, and exports.
- Services implement page, branch, user, settings, storage, and analytics work.
  Persistence currently mixes SQLAlchemy Core expressions with parameterized SQL.
- `src/schema.py` and `alembic/` describe a PostgreSQL schema. The current importer
  reads MongoDB into that schema; `migrator.py` is a legacy Mongo backup utility.
- A page has a unique title and multiple named branches. Each branch stores its
  current Markdown. Updating an existing branch archives its previous content
  in `revisions`. `talk` is a special branch that appends signed messages.
- Content supports `[[Page]]`, `[[Page:Branch]]`, source citations, image captions
  and widths, colors, timestamps, and global statistics placeholders.
- The browser has a custom visual editor with Markdown/HTML conversion, raw
  Markdown editing, and local drafts. The server renders Markdown separately.

Branches are copies of an individual page and its history. The reviewed branch
service does not implement Git-style merge or a whole-wiki version snapshot.

## Keep, adapt, or rebuild

| Area | Decision | Reason |
| --- | --- | --- |
| FastAPI, Jinja, server-rendered pages | Keep | Fits the application; no architectural need for a frontend framework migration. |
| PostgreSQL schema and Alembic | Keep and validate | Useful relational boundaries, foreign keys, cascading cleanup, and unique branch/revision constraints already exist. |
| Page/revision transactions | Keep and improve | Updates and restores archive content transactionally and lock the branch row. Add stale-edit detection. |
| Existing Markdown and wiki syntax | Preserve compatibility | Existing content depends on these conventions. Build representative fixtures before replacing parsing. |
| HTML sanitizer and limited placeholders | Keep the approach | Explicit HTML allowlists and limited substitution are useful boundaries. Test every output path. |
| Storage service boundary | Adapt | S3/local upload, download, listing, and deletion already have a useful shared interface. Simplify configuration and lifecycle. |
| Local drafts | Adapt | Saving by user, branch, and title is useful. Retain drafts until a save is confirmed successful. |
| Permissions and authenticated user context | Rebuild centrally | Current routes enforce different rules and receive incomplete user context. |
| Visual editor internals | Rebuild | A large custom parser, serializer, selection system, and command handling duplicate server behavior. |
| Route error handling and response construction | Rebuild incrementally | Repeated fallbacks obscure actual failures and produce inconsistent HTTP behavior. |
| Search | Improve when needed | Current substring search is adequate for a small wiki, but lacks ranking and a matching search index. |
| Analytics, PDF bundles, live log dashboard | Defer during a rebuild | Useful extras, but they should not determine the core editing architecture. |
| Mongo-specific runtime compatibility | Retire after verified cutover | Preserve the importer and backups until data parity is established. |

## Concrete findings

### 1. Revision restore bypasses normal editing policy

`src/routes/web/history.py:625` authenticates and validates CSRF, but does not
apply the page permission or editing feature-flag checks used by normal saves.
`PageService.restore_version` also does not enforce those checks. A logged-in
user can reach a write path without the policy applied to regular editing.

Move authorization into a shared application operation used by save, restore,
and other page mutations. Explicitly specify personal-page and admin behavior.

### 2. Edit-count protection receives an incomplete user

`AuthMiddleware.get_current_user` returns only `username` and `is_admin`.
`src/routes/web/pages.py:179` checks `user.get("total_edits", 0)` for the
10/50-edit protection modes. Those callers receive no edit count, so these
checks evaluate against zero. The helper also has no explicit admin override.

Use a defined authenticated-user type containing the fields policy requires.
Test permissions through each HTTP write path, rather than only the helper.

### 3. Database failure can look like a missing page

`PageService.get_page` catches database exceptions and returns `None`, which is
also its missing-page result. Page-view routing responds to a missing page with
the editor. Other services similarly return empty lists, zero, or `False` on
failure, while routes often catch broad exceptions and return generic results.

Separate not-found, forbidden, conflict, and unavailable outcomes. Preserve
meaningful status codes for both HTML and JSON. A database outage should not
present itself as an empty wiki.

### 4. Row locks do not prevent stale browser edits

The branch lock in `PageService.update_page` serializes database writes, which
is useful. However, save requests contain no expected revision identifier.
Two users can open the same content, and the later save can overwrite the
earlier user's changes without a conflict response.

Use a stable revision/version value in the edit form and compare it atomically
when saving. History and restore URLs should identify stable revisions too:
their current newest-first indexes shift when another edit is made.

### 5. The editor and renderer have separate definitions of Markdown

`static/editor.js` implements `mdToHtml`, `htmlToMd`, and editing commands.
`src/routes/web/pages.py` builds a Python Markdown pipeline. Historical views
and PDF export configure additional rendering paths separately. This creates
multiple places to maintain wiki syntax and escaping rules.

Preserve raw Markdown as the authoritative representation. Start any new
editor with raw editing and a shared server preview; add visual editing after
content compatibility is demonstrated. Parser/serializer round-trip fidelity
needs fixtures for tables, citations, wiki links, code, and image metadata.

### 6. Drafts are deleted before server success

`static/drafts.js` removes the draft in the form's submit handler. A subsequent
network or database failure can therefore discard the recovery copy. Clear a
draft only after confirmed persistence, matching the saved content/version.

### 7. Setup depends on a private local module

`src/config.py:8` unconditionally imports storage keys from `env_config.py`.
That file exists locally but is ignored by Git. A clean checkout cannot import
the application, or the Alembic environment that imports configuration, without
recreating it. The endpoint, bucket, and wiki name are hardcoded as well.

Use one validated environment configuration with a documented example file,
explicit production settings, and a working local storage mode.

### 8. Ordinary rendering performs unnecessary statistics work

`render_template_content` calls `get_stats()` for every nonempty connected
content render, even when there are no statistics placeholders. `get_stats`
also fetches the user/page edit breakdown. Some totals are cached, but that
breakdown and other counts still run. Page-view analytics adds an awaited write.

Check whether placeholders are present before loading their values. Separate
small public counters from the admin/user breakdown. Profile before adding
broader infrastructure.

### 9. Current checks do not establish migration or application correctness

`test_postgres_migration.py` checks schema shape, cascade declarations, and
module imports. It does not execute the importer or validate real database
behavior. The older `test.py` smoke tests require a running server and leave
data behind; its filename also falls outside pytest's default discovery patterns.
The Pylint workflow uses `--exit-zero`, so lint findings cannot fail that job.

Prioritize isolated tests for permissions across save/restore, concurrent and
stale edits, render compatibility, failed-save draft recovery, and import parity.
Test migration against a disposable PostgreSQL database with representative
legacy documents, checking content and histories as well as counts.

## Suggested rebuild order

1. Preserve a snapshot of the current source and real database/media backups.
   Record representative existing content and page/history URLs as fixtures.
2. Make setup reproducible: environment-only configuration, documented local
   startup, dependencies, migration commands, and explicit failure behavior.
3. Repair the shared user context and centralize mutation policy. Cover normal
   edits, restores, branch creation, and personal pages with route-level tests.
4. Validate the existing PostgreSQL work rather than replacing it. Keep the
   transactional revision writes, add stable revision addressing and stale-save
   protection, and verify Mongo import before retiring the old data path.
5. Establish one content rendering interface and compatibility fixtures. Extract
   reusable view context so routes handle HTTP and services handle operations.
6. Replace the custom editor incrementally, preserving raw Markdown and drafts.
   Reuse the existing layout and interactions where they remain useful.
7. Reintroduce or improve PDF export, analytics, search ranking, and live logs
   according to actual usage. Keep data exports distinct from complete backups:
   the export service has row caps and reads collections in separate connections.

A first milestone should be a reliable loop: log in, open a page, edit it,
detect a stale save, view history, restore with the same permissions, and recover
a draft after a failed request. That establishes the core before feature parity.
