document.addEventListener('DOMContentLoaded', function () {
  const statusText = document.getElementById('draftStatusText');
  const saveBtn = document.getElementById('draftSaveBtn');
  const clearBtn = document.getElementById('draftClearBtn');
  const restoreBanner = document.getElementById('draftRestoreBanner');
  const restoreBtn = document.getElementById('draftRestoreBtn');
  const discardBtn = document.getElementById('draftDiscardBtn');
  const restoreTimestamp = document.getElementById('draftRestoreTimestamp');
  const visualEditor = document.getElementById('visual-editor');
  const contentTextarea = document.getElementById('content');
  const editForm = document.getElementById('editForm');
  const draftMeta = editForm ? editForm.dataset || {} : {};
  const context = {
    title: draftMeta.draftTitle || '',
    branch: draftMeta.draftBranch || 'main',
    user: draftMeta.draftUser || 'anonymous'
  };

  if (!statusText || !contentTextarea) {
    return;
  }

  let storage;
  try {
    storage = window.localStorage;
    const testKey = '__wikiware_draft_probe__';
    storage.setItem(testKey, '1');
    storage.removeItem(testKey);
  } catch (_) {
    storage = null;
  }

  if (!storage) {
    statusText.textContent = 'Local drafts are unavailable in this browser.';
    if (saveBtn) saveBtn.disabled = true;
    if (clearBtn) clearBtn.hidden = true;
    if (restoreBanner) restoreBanner.hidden = true;
    return;
  }

  const storageKeyParts = [
    'wikiware',
    'draft',
    'v1',
    encodeURIComponent(context.user || 'anonymous'),
    encodeURIComponent(context.branch || 'main'),
    encodeURIComponent(context.title || 'untitled')
  ];
  const draftKey = storageKeyParts.join(':');
  const initialMarkdown = contentTextarea.value || '';
  const initialNormalized = initialMarkdown.trim();
  let saveTimer = null;

  function setHasDraft(flag) {
    if (clearBtn) {
      clearBtn.hidden = !flag;
    }
  }

  function triggerInput(target) {
    if (!target) return;
    if (window.WikiEditor && typeof window.WikiEditor.dispatchEvent === 'function') {
      window.WikiEditor.dispatchEvent(target, 'input', { bubbles: true });
      return;
    }
    try {
      target.dispatchEvent(new Event('input', { bubbles: true }));
    } catch (_) {
      if (typeof document.createEvent === 'function') {
        const evt = document.createEvent('Event');
        evt.initEvent('input', true, false);
        target.dispatchEvent(evt);
      }
    }
  }

  function formatTimestamp(value) {
    if (!value) return 'an unknown time';
    const asNumber = Number(value);
    const date = Number.isFinite(asNumber) ? new Date(asNumber) : new Date(value);
    if (Number.isNaN(date.getTime())) {
      return 'an unknown time';
    }
    return date.toLocaleString();
  }

  function readDraft() {
    const raw = storage.getItem(draftKey);
    if (!raw) return null;
    try {
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed.markdown !== 'string') {
        return null;
      }
      return parsed;
    } catch (_) {
      storage.removeItem(draftKey);
      return null;
    }
  }

  function updateStatus(message) {
    if (statusText) {
      statusText.textContent = message;
    }
  }

  function updateRestoreBanner(draft) {
    if (!restoreBanner) return;
    const normalized = draft && typeof draft.markdown === 'string' ? draft.markdown.trim() : '';
    const shouldShow = !!(normalized && normalized !== initialNormalized);
    if (!shouldShow) {
      restoreBanner.hidden = true;
      return;
    }
    restoreBanner.hidden = false;
    if (restoreTimestamp) {
      restoreTimestamp.textContent = formatTimestamp(draft.updatedAt);
    }
  }

  function getCurrentMarkdown() {
    if (!contentTextarea) return '';
    if (window.WikiEditor && typeof window.WikiEditor.getMarkdown === 'function') {
      return window.WikiEditor.getMarkdown();
    }
    return contentTextarea.value || '';
  }

  function saveDraft(manual = false) {
    saveTimer = null;
    const markdown = getCurrentMarkdown();
    const payload = {
      markdown,
      updatedAt: Date.now(),
      meta: context
    };
    try {
      storage.setItem(draftKey, JSON.stringify(payload));
      setHasDraft(true);
      updateStatus('Draft saved locally at ' + formatTimestamp(payload.updatedAt) + (manual ? ' (saved now).' : '.'));
      updateRestoreBanner(null);
    } catch (error) {
      updateStatus('Unable to save draft locally: ' + error.message);
    }
  }

  function scheduleSave() {
    if (saveTimer) {
      clearTimeout(saveTimer);
    }
    saveTimer = window.setTimeout(() => saveDraft(false), 1500);
  }

  function flushPendingSave() {
    if (!saveTimer) return;
    clearTimeout(saveTimer);
    saveDraft(false);
  }

  function removeDraft(showMessage) {
    storage.removeItem(draftKey);
    setHasDraft(false);
    if (showMessage) {
      updateStatus('Local draft cleared.');
    } else {
      updateStatus('Drafts save automatically to this browser.');
    }
    updateRestoreBanner(null);
  }

  function restoreDraft(draft) {
    if (!draft || typeof draft.markdown !== 'string') return;
    if (window.WikiEditor && typeof window.WikiEditor.loadMarkdown === 'function') {
      window.WikiEditor.loadMarkdown(draft.markdown);
    } else {
      contentTextarea.value = draft.markdown;
      triggerInput(contentTextarea);
    }
    updateStatus('Draft restored from ' + formatTimestamp(draft.updatedAt) + '.');
    updateRestoreBanner(null);
    setHasDraft(true);
  }

  const existingDraft = readDraft();
  if (existingDraft) {
    setHasDraft(true);
    updateRestoreBanner(existingDraft);
    updateStatus('Last local draft saved at ' + formatTimestamp(existingDraft.updatedAt) + '.');
  } else {
    setHasDraft(false);
  }

  if (saveBtn) {
    saveBtn.addEventListener('click', () => {
      flushPendingSave();
      saveDraft(true);
    });
  }
  if (clearBtn) {
    clearBtn.addEventListener('click', () => removeDraft(true));
  }
  if (restoreBtn) {
    restoreBtn.addEventListener('click', () => {
      const draft = readDraft();
      if (draft) {
        restoreDraft(draft);
      }
    });
  }
  if (discardBtn) {
    discardBtn.addEventListener('click', () => removeDraft(true));
  }

  contentTextarea.addEventListener('input', scheduleSave);
  if (visualEditor) {
    visualEditor.addEventListener('input', scheduleSave);
  }

  if (editForm) {
    editForm.addEventListener('submit', (event) => {
      if (event.defaultPrevented) {
        return;
      }
      flushPendingSave();
      removeDraft(false);
    });
  }

  window.addEventListener('beforeunload', () => {
    flushPendingSave();
  });
});
