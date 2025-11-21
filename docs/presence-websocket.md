# Edit Presence WebSocket

Real-time, low-friction edit presence that shows who is editing or watching a page/branch. Presence is informational only (no locking) and mirrors the FastAPI/WebSocket approach used by `log_streamer`. All state is coordinated through MongoDB TTL documents so multiple workers stay in sync without bespoke locks.

## Goals
- Surface active editors/watchers for the current page/branch with second-level freshness.
- Self-heal via heartbeats and TTL expiry so stale connections disappear automatically.
- Keep save flows unchanged; presence metadata is optional and should never block edits.

## Feature Flag
- Guard all endpoints and the WebSocket behind `feature_flags.edit_presence_enabled` (default off until ready).
- When disabled, the REST endpoints return 404 and the WebSocket rejects with `4404`; the editor UI hides presence UI and skips the handshake.

## Data Model (`edit_sessions` collection)
- `page`: normalized title
- `branch`: branch name for the edit context
- `user_id`, `username`: bound to the session cookie user
- `mode`: `"edit"` or `"view"` (for read-only observers)
- `session_id`: short random string returned to the client
- `client_id`: per-tab UUID supplied by the frontend
- `created_at`, `last_heartbeat`: timestamps for diagnostics
- `lease_expires_at`: expiration timestamp (TTL index) that removes stale rows automatically

Indexes:
- `{page: 1, branch: 1, mode: 1, lease_expires_at: 1}`
- TTL on `lease_expires_at` (e.g., 120s) to clear dead sessions

## Lifecycle
1. **Create lease**: `POST /api/pages/{title}/edit-session` with `{branch, mode, client_id}`. Server authenticates the cookie, issues `session_id`, writes the TTL-backed row, and returns the current roster and `lease_expires_at`.
2. **Open WebSocket**: Connect to `/ws/edit-presence?page={title}&branch={branch}&session_id={id}&mode={mode}` using the same cookies. Server validates the lease, accepts the socket, tracks it in a room keyed by `page|branch`, and immediately broadcasts the roster.
3. **Heartbeat**: Client sends `{type: "ping"}` every 20s (and optionally on bursts of typing). Server updates `last_heartbeat`, extends `lease_expires_at`, and rebroadcasts only when the roster changes.
4. **Leave**: Client sends `{type: "release"}` before unload, or calls `DELETE /api/pages/{title}/edit-session/{session_id}`. Otherwise the TTL expiry removes the row and triggers a broadcast.

## API Reference
### `POST /api/pages/{title}/edit-session`
- **Body**: `{ "branch": "main", "mode": "edit" | "view", "client_id": "uuid" }`
- **Response**: `{ "status": "ok", "session_id": "abc123", "lease_expires_at": "...", "active_editors": [...], "active_watchers": [...] }`
- **Errors**: `401` unauthenticated, `404` missing page (optional), `409` when an existing live lease already exists for the same `client_id` and user.

### `DELETE /api/pages/{title}/edit-session/{session_id}`
- Marks the lease expired and broadcasts removal. Safe to call multiple times.
- Returns `{ "status": "released" }` even if already expired.

### `WS /ws/edit-presence`
- **Query params**: `page`, `branch`, `session_id`, `mode`.
- **Auth**: Valid session cookie; rejects with `4401` if missing/invalid, `4409` for mismatched session/page/branch, `4404` when feature is disabled.
- **Client -> server**: `{type:"ping"}` to extend lease; `{type:"release"}` to drop presence and close.
- **Server -> client**:
  - `presence`: `{type:"presence", editors:[{username, client_id}], watchers:[{username, client_id}] }`
  - `goodbye`: `{type:"goodbye", reason:"expired"|"released"|"invalid"}` before closing

## Server Components
### `EditPresenceService` (`src/services/edit_presence_service.py`)
- Validate session cookie via `UserService.get_user_by_session`.
- Create leases with `session_id`, `lease_expires_at`, and per-tab `client_id`.
- `touch_heartbeat(session_id)` to extend `lease_expires_at` and `last_heartbeat`.
- `release_session(session_id)` to expire early and return whether a broadcast is needed.
- `get_roster(page, branch)` to fetch active editors/watchers (filtering expired rows).
- `attach_presence_context(user_id, page, branch, session_id)` helper for log enrichment; no-op if the lease is missing or expired.

### `edit_presence_router`
- REST endpoints above plus feature-flag guarding.
- WebSocket handler modeled after `log_streamer.logs_ws`:
  - Builds per-room registry: `{ "page|branch": { "sockets": set(), "queue": asyncio.Queue() } }`.
  - Enqueues roster updates when leases are created, heartbeated, released, or TTL-pruned.
  - Closes sockets with `goodbye` when validation fails or the lease expires.

### Cross-worker broadcasts
- Preferred: MongoDB change stream on `edit_sessions` that pushes roster deltas into each worker’s room queue.
- Optional: Redis pub/sub channel `edit-presence` carrying `{page, branch}` invalidations.
- Fallback: in-process only when running a single worker.

## Heartbeat and Lease Rules
- Default lease: 90s; heartbeat extends by another 90s up to a max of +120s to avoid unbounded growth.
- Heartbeats are rate-limited server-side (e.g., ignore pings faster than every 5s).
- Presence broadcasts only when roster membership changes, not on every ping.

## Client Integration (`templates/edit.html`)
- Generate a stable `client_id` per tab (UUID stored in `sessionStorage`).
- Before rendering the editor, call the `edit-session` endpoint; if it fails, proceed without presence UI.
- Open the WebSocket and render a “Currently editing” pill showing initials/usernames for editors and watchers.
- Send `{type:"ping"}` on a 20s interval and on typing bursts; send `{type:"release"}` on `beforeunload`.
- Post saves may include header `X-Wikiware-Edit-Session: {session_id}` for analytics/trace logs; saves must continue without it.
- If the socket closes unexpectedly, show a soft warning and stop pinging/deleting (TTL cleanup will remove the lease).

## Failure and Security Notes
- Network drop or tab close: TTL expiry removes the row and triggers a roster broadcast.
- Spoofed session ids: WebSocket validation binds `session_id` to `user_id/page/branch` and rejects mismatches with `4409`.
- Database offline: REST returns 503 and the client hides presence; editor remains usable.
- Rapid reconnects: short backoff (e.g., 1–2s) before retrying the WebSocket to avoid thundering herds.

## Rollout Checklist
- [ ] Add feature flag plumbing and disabled behavior.
- [ ] Implement `EditPresenceService` with TTL index migration.
- [ ] Wire REST endpoints and WebSocket router; add to `src/server.py`.
- [ ] Implement in-process broadcaster; stub change stream/Redis hooks.
- [ ] Add edit page UI/JS module; gate with feature flag.
- [ ] Smoke test: multiple tabs editing the same page, heartbeat drop, TTL expiry, and auth rejection paths.
