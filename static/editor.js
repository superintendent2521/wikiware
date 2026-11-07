/* Markdown is authoritative; the server renders previews without rewriting it. */
(function () {
  'use strict';

  const WikiEditor = {
    init(opts) {
      const textarea = document.querySelector(opts.textareaSelector);
      const preview = document.querySelector(opts.editorSelector);
      const form = document.querySelector(opts.formSelector);
      const toolbar = document.querySelector(opts.toolbarSelector || '#wiki-editor-toolbar');
      if (!textarea || !preview || !form) return;

      const status = document.getElementById('editorStatus');
      const toggle = document.getElementById('toggleRawBtn');
      let selection = [textarea.selectionStart || 0, textarea.selectionEnd || 0];
      let previewing = false;
      let previewRequest = 0;
      let controller = null;
      let submitting = false;

      function setStatus(message) {
        if (status) status.textContent = message;
      }

      function captureSelection() {
        selection = [textarea.selectionStart, textarea.selectionEnd];
      }

      function restoreSelection() {
        textarea.focus();
        textarea.setSelectionRange(selection[0], selection[1]);
      }

      function showMarkdown() {
        ++previewRequest;
        if (controller) controller.abort();
        controller = null;
        previewing = false;
        textarea.hidden = false;
        textarea.style.display = '';
        preview.hidden = true;
        preview.style.display = 'none';
        if (toggle) toggle.textContent = 'Preview';
      }

      function insertSnippet(snippet) {
        if (submitting) return false;
        showMarkdown();
        restoreSelection();
        textarea.setRangeText(snippet, selection[0], selection[1], 'end');
        captureSelection();
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
        return true;
      }

      function wrapSelection(before, after, fallback) {
        const selected = textarea.value.slice(selection[0], selection[1]) || fallback;
        return insertSnippet(before + selected + after);
      }

      function formatLines(prefix) {
        const start = textarea.value.lastIndexOf('\n', selection[0] - 1) + 1;
        let end = textarea.value.indexOf('\n', selection[1]);
        if (end === -1) end = textarea.value.length;
        const lines = textarea.value.slice(start, end).split('\n');
        selection = [start, end];
        insertSnippet(lines.map(function (line) {
          return prefix + line.replace(/^(?:#{1,6}\s+|[-*]\s+|\d+\.\s+)/, '');
        }).join('\n'));
      }

      async function showPreview() {
        previewing = true;
        textarea.hidden = true;
        textarea.style.display = 'none';
        preview.hidden = false;
        preview.style.display = 'block';
        if (toggle) toggle.textContent = 'Edit Markdown';
        const requestId = ++previewRequest;
        if (controller) controller.abort();
        controller = new AbortController();
        const data = new FormData();
        data.append('content', textarea.value);
        const csrf = form.querySelector('[name="csrf_token"]');
        data.append('csrf_token', csrf ? csrf.value : '');
        setStatus('Loading preview…');
        try {
          const response = await fetch('/api/preview', {
            method: 'POST', body: data, credentials: 'same-origin',
            headers: { Accept: 'application/json' }, signal: controller.signal
          });
          const result = await response.json();
          if (!response.ok) throw new Error(result.error || result.detail || 'Preview failed.');
          if (requestId !== previewRequest || !previewing) return;
          // Only the shared server renderer supplies this HTML.
          preview.innerHTML = result.html;
          setStatus('Preview');
        } catch (error) {
          if (error.name === 'AbortError' || requestId !== previewRequest) return;
          preview.textContent = 'Preview unavailable. Your Markdown is unchanged.';
          setStatus(typeof error.message === 'string' ? error.message : 'Preview failed.');
        }
      }

      ['select', 'keyup', 'mouseup', 'input', 'blur'].forEach(function (eventName) {
        textarea.addEventListener(eventName, captureSelection);
      });

      if (toolbar) {
        toolbar.addEventListener('mousedown', function (event) {
          if (event.target.closest('button')) captureSelection();
        });
        toolbar.querySelectorAll('[data-cmd]').forEach(function (button) {
          button.addEventListener('click', function () {
            const command = button.dataset.cmd;
            if (command === 'bold') wrapSelection('**', '**', 'bold text');
            else if (command === 'italic') wrapSelection('*', '*', 'italic text');
            else if (command === 'code') wrapSelection('`', '`', 'code');
            else if (command === 'formatBlock') {
              const level = Number((button.dataset.value || '').slice(1)) || 0;
              formatLines(level ? '#'.repeat(level) + ' ' : '');
            } else if (command === 'insertUnorderedList') formatLines('- ');
            else if (command === 'insertOrderedList') formatLines('1. ');
          });
        });
      }

      function onClick(id, handler) {
        const button = document.getElementById(id);
        if (button) button.addEventListener('click', handler);
      }

      onClick('toggleRawBtn', function () {
        if (previewing) { showMarkdown(); restoreSelection(); }
        else showPreview();
      });
      onClick('createLinkBtn', function () {
        const url = window.prompt('Enter URL');
        if (!url) return;
        try {
          const parsed = new URL(url, window.location.origin);
          if (!['http:', 'https:', 'mailto:'].includes(parsed.protocol)) return;
          wrapSelection('[', '](' + url.replace(/\)/g, '%29') + ')', 'link text');
        } catch (_) { setStatus('Enter a valid URL.'); }
      });
      onClick('insertWikiLinkBtn', function () {
        document.dispatchEvent(new CustomEvent('wikiLink:open'));
      });
      onClick('insertUnixTimeBtn', function () {
        const value = window.prompt('Unix timestamp', String(Math.floor(Date.now() / 1000)));
        if (value && /^\d+$/.test(value.trim())) insertSnippet('{{ global.unix:' + value.trim() + ' }}');
      });
      onClick('insertTableBtn', function () {
        insertSnippet('\n| Column 1 | Column 2 |\n| --- | --- |\n|  |  |\n');
      });

      form.addEventListener('submit', async function (event) {
        if (event.defaultPrevented || submitting) {
          if (submitting) event.preventDefault();
          return;
        }
        const summary = form.querySelector('[name="edit_summary"]');
        // Let the required summary field use the browser's native validation.
        if (!summary || !summary.value.trim()) return;
        event.preventDefault();
        submitting = true;
        textarea.readOnly = true;
        const savedMarkdown = textarea.value;
        form.dispatchEvent(new CustomEvent('wiki:save-start'));
        const data = new FormData(form);
        const buttons = Array.from(form.querySelectorAll('button[type="submit"]'));
        buttons.forEach(function (button) { button.disabled = true; });
        setStatus('Saving…');
        try {
          const response = await fetch(form.action, {
            method: 'POST', body: data, credentials: 'same-origin', redirect: 'error',
            headers: { Accept: 'application/json' }
          });
          const result = await response.json();
          if (!response.ok) {
            throw new Error(result.error || (typeof result.detail === 'string' ? result.detail : '') || 'Page not saved (' + response.status + ').');
          }
          if (typeof result.redirect_url !== 'string') throw new Error('Save was not confirmed. Your draft is kept.');
          const destination = new URL(result.redirect_url, window.location.origin);
          if (destination.origin !== window.location.origin) throw new Error('Invalid save destination.');
          if (textarea.value !== savedMarkdown) {
            setStatus('The earlier text was saved. Your newer changes are kept locally; reload before continuing.');
            return;
          }
          form.dispatchEvent(new CustomEvent('wiki:saved', { detail: { markdown: savedMarkdown } }));
          window.location.assign(destination.href);
        } catch (error) {
          setStatus(typeof error.message === 'string' ? error.message : 'Save failed. Your draft is kept.');
        } finally {
          submitting = false;
          textarea.readOnly = false;
          buttons.forEach(function (button) { button.disabled = false; });
        }
      });

      Object.assign(WikiEditor, {
        captureSelection, restoreSelection, insertSnippet,
        dispatchEvent(target, eventName, eventInit) {
          target.dispatchEvent(new Event(eventName || 'input', eventInit || { bubbles: true }));
        },
        getMarkdown() { return textarea.value; },
        loadMarkdown(value) {
          if (submitting) return;
          showMarkdown();
          textarea.value = typeof value === 'string' ? value : '';
          selection = [textarea.value.length, textarea.value.length];
          textarea.dispatchEvent(new Event('input', { bubbles: true }));
        },
        isRawMode() { return true; },
        insertWikiLink({ page, branch } = {}) {
          if (!page || /[\[\]\r\n]/.test(page) || (branch && /[\[\]\r\n]/.test(branch))) return false;
          return insertSnippet('[[ ' + page.trim() + (branch ? ':' + branch.trim() : '') + ' ]]');
        },
        insertSourceCitation(rawUrl) {
          try {
            const url = new URL(rawUrl);
            if (!['http:', 'https:'].includes(url.protocol)) return false;
            return insertSnippet('{{source|url=' + url.href.replace(/\|/g, '%7C').replace(/}/g, '%7D') + '}}');
          } catch (_) { return false; }
        },
        insertImage({ src, alt, caption, width } = {}) {
          if (!src) return false;
          const label = String(alt || '').replace(/[\[\]\\]/g, '\\$&');
          const title = String(caption || '').replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/[\r\n]/g, ' ');
          const size = Number(width);
          const suffix = Number.isInteger(size) && size >= 120 && size <= 1200 ? '{width=' + size + '}' : '';
          return insertSnippet('\n![' + label + '](' + src.replace(/\)/g, '%29') + (title ? ' "' + title + '"' : '') + ')' + suffix + '\n');
        }
      });
      showMarkdown();
    }
  };

  window.WikiEditor = WikiEditor;
})();
