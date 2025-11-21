// Lightweight edit presence client for the editor page.
// Handles handshake, WebSocket heartbeats, and roster display.
(function () {
  const config = window.__EDIT_PRESENCE__;
  if (!config || !config.enabled) return;

  const root = document.querySelector("[data-edit-presence]");
  if (!root) return;

  const rosterList = root.querySelector("[data-presence-roster]");
  const statusEl = root.querySelector("[data-presence-status]");

  const state = {
    sessionId: null,
    socket: null,
    heartbeatTimer: null,
    closed: false,
  };

  function setStatus(text, tone = "info") {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.dataset.tone = tone;
    statusEl.hidden = !text;
  }

  function ensureClientId() {
    const key = "wikiware_client_id";
    try {
      const existing = sessionStorage.getItem(key);
      if (existing) return existing;
      const generated = crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2);
      sessionStorage.setItem(key, generated);
      return generated;
    } catch (_) {
      return Math.random().toString(36).slice(2);
    }
  }

  function renderRoster(roster) {
    if (!rosterList) return;
    const editors = roster.editors || [];
    const watchers = roster.watchers || [];
    if (!editors.length && !watchers.length) {
      rosterList.innerHTML = '<span class="presence-empty">You are the only one here.</span>';
      return;
    }

    function pill(user, kind) {
      const name = user.username || "Unknown";
      const initial = name.substring(0, 1).toUpperCase();
      return `<div class="presence-pill presence-${kind}" title="${name}">
        <span class="presence-avatar">${initial}</span>
        <span class="presence-name">${name}</span>
      </div>`;
    }

    const editorMarkup = editors.map((u) => pill(u, "editor")).join("");
    const watcherMarkup = watchers.map((u) => pill(u, "watcher")).join("");

    rosterList.innerHTML = `${editorMarkup}${watcherMarkup}`;
  }

  function buildWsUrl(sessionId, branch, mode) {
    const loc = window.location;
    const isSecure = loc.protocol === "https:";
    const wsProtocol = isSecure ? "wss:" : "ws:";
    const host = loc.host;
    const params = new URLSearchParams({
      page: config.page,
      branch,
      session_id: sessionId,
      mode,
    });
    return `${wsProtocol}//${host}/ws/edit-presence?${params.toString()}`;
  }

  function stopHeartbeat() {
    if (state.heartbeatTimer) {
      clearInterval(state.heartbeatTimer);
      state.heartbeatTimer = null;
    }
  }

  async function releaseSession(reason) {
    if (!state.sessionId || state.closed) return;
    state.closed = true;
    stopHeartbeat();
    try {
      await fetch(`/api/pages/${encodeURIComponent(config.page)}/edit-session/${state.sessionId}`, {
        method: "DELETE",
        keepalive: true,
      });
    } catch (_) {
      // ignore best-effort release
    }
    if (reason) setStatus(reason, "warning");
  }

  function startWebSocket(branch, mode) {
    const url = buildWsUrl(state.sessionId, branch, mode);
    const socket = new WebSocket(url);
    state.socket = socket;

    socket.addEventListener("open", () => {
      state.heartbeatTimer = setInterval(() => {
        if (socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ type: "ping" }));
        }
      }, 20000);
    });

    socket.addEventListener("message", (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload.type === "presence") {
          renderRoster(payload);
          setStatus("", "info");
        } else if (payload.type === "goodbye") {
          setStatus("Presence session ended: " + (payload.reason || "unknown"), "warning");
        }
      } catch (_) {
        // ignore malformed messages
      }
    });

    socket.addEventListener("close", () => {
      stopHeartbeat();
      if (!state.closed) {
        setStatus("Presence disconnected. You can keep editing.", "warning");
      }
    });

    socket.addEventListener("error", () => {
      setStatus("Presence connection error.", "error");
    });
  }

  async function startPresence() {
    const clientId = ensureClientId();
    try {
      const resp = await fetch(`/api/pages/${encodeURIComponent(config.page)}/edit-session`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          branch: config.branch,
          mode: config.mode,
          client_id: clientId,
        }),
        credentials: "same-origin",
      });

      if (!resp.ok) {
        if (resp.status === 404 || resp.status === 503) {
          setStatus("Presence unavailable right now.", "warning");
          return;
        }
        setStatus("Could not start presence.", "error");
        return;
      }

      const data = await resp.json();
      state.sessionId = data.session_id;
      renderRoster({ editors: data.active_editors || [], watchers: data.active_watchers || [] });
      startWebSocket(config.branch, config.mode);
      setStatus("", "info");
    } catch (err) {
      setStatus("Presence failed to start.", "warning");
      console.error("Edit presence error", err);
    }
  }

  window.addEventListener("beforeunload", () => {
    try {
      if (state.socket && state.socket.readyState === WebSocket.OPEN) {
        state.socket.send(JSON.stringify({ type: "release" }));
      }
    } catch (_) {
      // ignore
    }
    releaseSession();
  });

  setStatus("Connecting to presence...", "info");
  startPresence();
})();
