# Rewrite implementation status

This follows the original [codebase review](codebase-review.md). Existing
PostgreSQL migration work and the authored Markdown format were preserved.

| Review item | Current implementation |
| --- | --- |
| Reproducible local setup | Environment-only configuration, .env.example, local PostgreSQL Compose service, shared launcher settings, absolute template/static paths, production startup validation. |
| Shared editing policy | Save, restore, and branch creation re-read the account and protection inside their database transaction; edit counts reach the policy; personal pages remain owner-only. |
| CSRF coverage | Normal/personal saves, previews, branch creation, restores, favorites, image deletion, logout, and PDF requests validate tokens. |
| Stale saves | Forms carry an expected branch version; the transaction rejects stale editors with 409 and preserves the submitted draft. Concurrent initial creation also conflicts safely. |
| Stable history identity | History links, comparisons, and restore forms carry database revision IDs; restores scope them to the requested branch. |
| Failure handling | Page read/write failures are distinct from missing content; saves return JSON errors or recoverable HTML submissions. API HTTP errors retain JSON and response headers. |
| Shared rendering | Published pages, personal pages, history, preview, and PDF share one sanitized Markdown pipeline, including citations and legacy wiki extensions. Code samples retain literal wiki syntax. |
| Editor rebuild | Markdown remains authoritative; toolbar actions insert source, preview requests use the server, and ordinary form posts work. Captions and explicit image widths are supported. |
| Draft recovery | Submit keeps the recovery copy; acknowledgment clears only matching text. Network failures, conflicts, blocked storage, empty drafts, and other tabs are covered. |
| Render performance | Plain articles skip statistics queries. Public counters do not fetch all per-user/page edit totals; authentication caches within a request. |
| Search | Literal wildcard characters, exact/prefix title ranking, deterministic ordering, and 50-result pagination. Substring search remains the small-wiki default. |
| Storage and PDFs | Async image-library bug fixed, deleted image hashes removed, local paths checked, clients closed at shutdown; PDF assets come from bounded wiki storage, generation runs outside the event loop. |
| Export consistency | All ZIP tables share a repeatable-read snapshot; row truncation removed; cooldown reservations are atomic and released for interrupted downloads. |
| Live server logs | Administrator-only with origin and periodic session checks, bounded queues/client counts, send timeouts, and cleanup. |
| Account persistence | Returning inserts now commit registration. Password work runs outside the event loop; password changes invalidate older sessions. |
| Tests and CI | Isolated HTTP/render/config suites and PostgreSQL transaction/import/export/search suites; Node browser tests; PostgreSQL-backed CI, Alembic smoke test, correctness lint, CodeQL, artifacts, and Dependabot. |

## Verification performed locally

- 18 Node tests pass, covering editor/draft behavior and inline template-script syntax.
- Native Ruff syntax/undefined-name checks pass across application code, migrations,
  and tests. The maintained backend suites also pass their static Ruff checks.
- Python test execution and database migrations have not been run locally, in
  accordance with the repository's no-Python instruction. CI is configured to
  perform backend and PostgreSQL verification when these changes are pushed.
- The browser tests use a small DOM harness; a live browser layout review remains
  part of deployment verification.

## Remaining verification and optional follow-up

1. Run the complete backend workflow and resolve any runtime incompatibilities
   before deploying. Capture a dependency lock after a successful tested resolve.
2. Back up the actual database and media, rehearse the importer with representative
   data, and compare content/history/permissions alongside counts before cutover.
   Legacy migration and backup utilities remain until that is verified.
3. Review the editor in a running browser. The replacement is Markdown plus
   preview; visual HTML editing and drag resizing have been replaced by source
   controls and an explicit image-width field.
4. Add indexed/full-text search, background analytics, or richer visual editing
   after workload measurements and content compatibility tests justify them.

The collection ZIP endpoint is a content export, not a backup of accounts,
settings, sessions, or image bytes. Production migration and deployment are
separate from these source changes.
