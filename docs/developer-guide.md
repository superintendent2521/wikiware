# WikiWare developer guide

## Application structure

index.py starts Uvicorn with shared configuration. src/server.py registers
FastAPI routers, security/settings middleware, exception handlers, and database
and storage startup/shutdown.

- src/routes/web handles HTML forms and Jinja views.
- src/routes/api handles browser widgets, previews, media actions, and exports.
- src/services owns content rendering, persistence, and editing rules.
- src/schema.py describes PostgreSQL tables; Alembic owns deployed schema changes.
- static/editor.js edits Markdown directly and requests server previews.
- static/drafts.js manages recovery copies by account, page, and branch.

Configuration comes from environment variables, with an optional repository
.env file. See .env.example and the root README for setup. Python 3.13/3.14
and PostgreSQL 17 are the configured CI targets.

## Page identity, history, and writes

A pages row owns a unique title. page_branches rows own each branch's current
content and protection. revisions archives prior branch states. Branches copy
an individual page's content, protection, and complete revision history; they
are not Git repositories or whole-wiki snapshots.

PageService.get_page(title, branch='main') returns a mapping, or None only for
absence. Each page includes version_token, formed from its branch ID and update
timestamp. A nonexistent branch starts with token "new".

PageService.update_page(..., *, expected_version, actor) locks the branch,
checks its version, reloads the account, global editing switch, personal-page
ownership, and protection inside the transaction, archives prior content, and
updates content and edit counts atomically. Admins may change protection;
other writers preserve the current database policy. Automatic talk-page creation
does not count as another user edit.

PageService.restore_version(title, branch, index, author, *, revision_id, actor)
selects the target by stable ID within the branch, authorizes it inside the
transaction, archives current content, and records the restoring user. The index
argument remains for legacy URLs; the revision ID chooses the target.

PageService.get_revision(title, branch, revision_id) returns one stable revision.
New history links and restore forms carry that ID. Legacy index-only view URLs
remain accepted. New comparison forms send stable from_revision and to_revision
values, with zero meaning current content.

BranchService.create_branch(..., *, actor) locks and authorizes the source
branch and copies its history and allowed editors in one transaction.

## Error and HTTP contracts

src/services/page_errors.py defines failures that must not become empty content:

| Failure | HTTP status |
| --- | --- |
| PageUnavailableError | 503 |
| PageConflictError | 409 |
| PagePermissionError | 403 |

Web handlers propagate these or render the appropriate error. API paths return
JSON. Both save routes require a CSRF token, edit summary, and expected_version.
With Accept: application/json, successful persistence returns a redirect_url
field; errors return a non-success status and an error string. Ordinary form
posts redirect on success. Failed ordinary submissions render submitted text
for recovery.

AuthMiddleware.get_current_user caches its result only for the current request
and exposes username, admin status, and total edit count. Password hashing and
verification run outside the event loop. User creation commits its returning
insert; use execute_returning for returning writes, not the fetch_one read
helper. Sessions issued before a password change are rejected at next lookup.

## Content dialect

The async render_markdown(content) function in src/services/render_service.py
returns (sanitized_html, toc_items, sources). Pages, history, personal pages,
previews, and PDF export use this interface.

The pipeline supports headings, fenced code, lists, tables, wiki links,
citations, image figures with captions and widths, colors, explicit Unix
timestamps, and public statistics placeholders. Wiki syntax inside code samples
stays literal. Source URLs require HTTP/HTTPS. No authored Jinja code executes.

render_sources_html(sources) renders the citation list when the caller does not
use the main page template's source section. Normal content without statistics
placeholders does not query counters; public totals do not load every user's
page-edit breakdown.

POST /api/preview accepts form content and CSRF token, requires authentication,
and returns rendered HTML plus TOC and sources. The browser treats Markdown as
authoritative: previewing and saving never serialize HTML back into Markdown.
A visible required summary field also permits ordinary form submissions.

## Draft recovery and storage

Drafts use a versioned local-storage key scoped by user, title, and branch.
Submitting keeps a recovery copy. The editor's wiki:saved event clears it only
when stored and current text both match the confirmed saved text. Other tabs'
newer drafts survive. Storage failures leave editing and saving available.
Failed ordinary form posts also include recoverable text.

Storage supports local files and S3. Uploaded images have MIME/signature and
size checks and SHA-256 deduplication. The library awaits storage operations.
Image deletion removes corresponding hash metadata. Storage connections close
at shutdown.

## Search, exports, and logs

Search treats percent and underscore literally, ranks exact/prefix title
matches before body matches, and paginates 50 results at a time. It retains
substring search; add a database search index after measuring real workloads.

Collection export streams every selected row through one repeatable-read,
read-only transaction. Cooldown reservation is atomic; interrupted archives
release their own reservation without clearing a newer one. ZIPs contain content,
history, branch, and image metadata only. Use PostgreSQL and media backups for
full recovery.

PDF export uses the shared sanitized renderer, runs generation outside the event
loop with per-process serialization, and embeds only bounded files fetched from
the wiki's own storage. Authored URLs cannot ask the PDF engine to read arbitrary
files or fetch remote images.

Live server logs require an active administrator and matching browser origin.
The queue and client count are bounded; sends have timeouts, permissions are
rechecked, and sinks and sockets close at shutdown.

## Tests and migrations

See [the maintained test suites](../src/tests/README.md). Integration tests require
an explicitly configured test database and create isolated random schemas.
The old live-server smoke script was replaced with isolated tests.

The Mongo importer is retained until real-data cutover is verified. Keep legacy
backups and validate actual content and media alongside counts. Application
development does not require MongoDB.

Other legacy reference documents may describe pre-migration interfaces;
this guide and the source define the current contracts.
