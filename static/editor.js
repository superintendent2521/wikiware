// WikiWare client-side visual editor (M1 + M2)
// No external deps; serializes back to Markdown on submit

(function () {
  function qs(sel, root = document) { return root.querySelector(sel); }
  function qsa(sel, root = document) { return Array.from(root.querySelectorAll(sel)); }

  function getRange() {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return null;
    return sel.getRangeAt(0);
  }

  function insertNodeAtSelection(node) {
    const range = getRange();
    if (!range) return;
    range.deleteContents();
    range.insertNode(node);
    // Move caret after inserted node
    range.setStartAfter(node);
    range.collapse(true);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }

  function findAncestor(node, tagNames) {
    tagNames = Array.isArray(tagNames) ? tagNames : [tagNames];
    const upper = tagNames.map(t => String(t).toUpperCase());
    let cur = node;
    while (cur && cur !== document) {
      if (cur.nodeType === 1 && upper.includes(cur.nodeName)) return cur;
      cur = cur.parentNode;
    }
    return null;
  }

  let lastSelectionRange = null;
  let lastSelectionEditor = null;

  // Minimal Markdown -> HTML renderer for preview/editing
  function mdToHtml(md) {
    // Normalize line endings
    md = (md || '').replace(/\r\n?/g, '\n');

    // Escape HTML first
    const escapeHtml = s => s
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');

    const escapeAttr = s => String(s || '')
      .replace(/&/g, '&amp;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');

    let sourceCounter = 0;

    // Inline replacements: bold, italic, code, links, images
    function renderInline(text) {
      if (!text) return '';
      // Escape first
      text = escapeHtml(text);
      text = text.replace(/\{\{source\|([^}]+)\}\}/gi, (_, body) => {
        const meta = { url: '', title: '', author: '' };
        body.split('|').forEach((segment) => {
          const [rawKey, ...rawValue] = segment.split('=');
          if (!rawKey) return;
          const key = rawKey.trim().toLowerCase();
          const value = rawValue.join('=').trim();
          if (!value) return;
          if (key === 'url') meta.url = value;
          if (key === 'title') meta.title = value;
          if (key === 'author') meta.author = value;
        });
        const index = ++sourceCounter;
        const supAttrs = [
          'class="source-ref"',
          `data-source-index="${index}"`,
          `data-source-url="${escapeAttr(meta.url)}"`,
        ];
        if (meta.title) supAttrs.push(`data-source-title="${escapeAttr(meta.title)}"`);
        if (meta.author) supAttrs.push(`data-source-author="${escapeAttr(meta.author)}"`);
        const hrefAttr = ` href="#source-${index}"`;
        return `<sup ${supAttrs.join(' ')}><a${hrefAttr} class="source-citation">[ ${index} ]</a></sup>`;
      });
      // code `code`
      text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
      // images ![alt](url)
      text = text.replace(/!\[([^\]]*)\]\(([^\)]+)\)/g, '<img alt="$1" src="$2">');
      // links [text](url)
      text = text.replace(/\[([^\]]+)\]\(([^\)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
      // bold **text**
      text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
      // italic *text* (basic, not perfect)
      text = text.replace(/(^|\W)\*([^*]+)\*(?=\W|$)/g, '$1<em>$2</em>');
      return text;
    }

    const lines = md.split('\n');
    let html = '';
    let i = 0;
    let inUl = false, inOl = false;

    function closeLists() {
      if (inUl) { html += '</ul>'; inUl = false; }
      if (inOl) { html += '</ol>'; inOl = false; }
    }

    function parseTable(startIdx) {
      const tblLines = [];
      let idx = startIdx;
      while (idx < lines.length && /\|/.test(lines[idx])) {
        tblLines.push(lines[idx]);
        idx++;
      }
      // Check header separator on line 2
      if (tblLines.length >= 2 && /^\s*\|?\s*:?[-]{3,}/.test(tblLines[1])) {
        const rowToCells = (row) => row.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(s => s.trim());
        const header = rowToCells(tblLines[0]);
        const bodyLines = tblLines.slice(2);
        html += '<table><thead><tr>' + header.map(h => `<th>${renderInline(h)}</th>`).join('') + '</tr></thead><tbody>';
        bodyLines.forEach(ln => {
          const cells = rowToCells(ln);
          if (cells.length && cells.some(c => c.length)) {
            html += '<tr>' + cells.map(c => `<td>${renderInline(c)}</td>`).join('') + '</tr>';
          }
        });
        html += '</tbody></table>';
        return idx - startIdx; // consumed count
      }
      return 0;
    }

    while (i < lines.length) {
      const line = lines[i];
      if (/^\s*$/.test(line)) { // blank line
        closeLists();
        html += '';
        i++;
        continue;
      }
      // Headings
      const h = line.match(/^(#{1,3})\s+(.*)$/);
      if (h) {
        closeLists();
        const level = h[1].length;
        html += `<h${level}>${renderInline(h[2])}</h${level}>`;
        i++;
        continue;
      }
      // Table
      const consumed = parseTable(i);
      if (consumed > 0) { closeLists(); i += consumed; continue; }
      // Ordered list
      if (/^\s*\d+\.\s+/.test(line)) {
        if (!inOl) { closeLists(); html += '<ol>'; inOl = true; }
        html += `<li>${renderInline(line.replace(/^\s*\d+\.\s+/, ''))}</li>`;
        i++;
        continue;
      }
      // Unordered list
      if (/^\s*[-*+]\s+/.test(line)) {
        if (!inUl) { closeLists(); html += '<ul>'; inUl = true; }
        html += `<li>${renderInline(line.replace(/^\s*[-*+]\s+/, ''))}</li>`;
        i++;
        continue;
      }
      // Paragraph
      closeLists();
      html += `<p>${renderInline(line)}</p>`;
      i++;
    }
    closeLists();
    return html;
  }

  // DOM -> Markdown serialization
  function htmlToMd(root) {
    function serialize(node) {
      if (node.nodeType === Node.TEXT_NODE) {
        return node.nodeValue.replace(/\u200B/g, '').replace(/\n/g, ' ');
      }
      if (node.nodeType !== Node.ELEMENT_NODE) return '';
      const name = node.nodeName;
      const children = Array.from(node.childNodes).map(serialize).join('');
      switch (name) {
        case 'SUP': {
          if (node.classList && node.classList.contains('source-ref')) {
            const url = node.getAttribute('data-source-url') || '';
            const title = node.getAttribute('data-source-title') || '';
            const author = node.getAttribute('data-source-author') || '';
            const parts = [];
            if (url) parts.push(`url=${url}`);
            if (title) parts.push(`title=${title}`);
            if (author) parts.push(`author=${author}`);
            if (parts.length) {
              return `{{source|${parts.join('|')}}}`;
            }
          }
          return children;
        }
        case 'H1': return `# ${children}\n\n`;
        case 'H2': return `## ${children}\n\n`;
        case 'H3': return `### ${children}\n\n`;
        case 'P': return children.trim() ? `${children}\n\n` : '\n';
        case 'STRONG': return `**${children}**`;
        case 'EM': return `*${children}*`;
        case 'U': return `<u>${children}</u>`; // Markdown has no underline; keep HTML
        case 'CODE': return '`' + children + '`';
        case 'A': {
          const href = node.getAttribute('href') || '';
          return `[${children}](${href})`;
        }
        case 'IMG': {
          const alt = node.getAttribute('alt') || '';
          const src = node.getAttribute('src') || '';
          return `![${alt}](${src})`;
        }
        case 'UL': {
          const items = qsa(':scope > li', node).map(li => '- ' + Array.from(li.childNodes).map(serialize).join(''));
          return items.join('\n') + '\n\n';
        }
        case 'OL': {
          const items = qsa(':scope > li', node).map((li, idx) => `${idx + 1}. ` + Array.from(li.childNodes).map(serialize).join(''));
          return items.join('\n') + '\n\n';
        }
        case 'LI': return children + '\n';
        case 'TABLE': {
          // Convert simple tables to Markdown (no colspan/rowspan)
          const headers = qsa('thead th', node).map(th => th.textContent.trim());
          const rows = qsa('tbody tr', node).map(tr => qsa('td,th', tr).map(td => td.textContent.trim()));
          if (!headers.length && rows.length) {
            // No explicit thead; use first row as header if present
            const first = rows.shift();
            headers.push(...first);
          }
          const headerLine = '| ' + headers.join(' | ') + ' |';
          const sepLine = '| ' + headers.map(() => '---').join(' | ') + ' |';
          const bodyLines = rows.map(r => '| ' + r.join(' | ') + ' |');
          return [headerLine, sepLine, ...bodyLines].join('\n') + '\n\n';
        }
        case 'TBODY':
        case 'THEAD':
        case 'TR':
        case 'TD':
        case 'TH':
          return children; // handled at TABLE level
        default:
          return children;
      }
    }
    // Serialize block children of root
    let out = '';
    Array.from(root.childNodes).forEach(n => { out += serialize(n); });
    return out.trim() + '\n';
  }

  function exec(cmd, val = null) {
    document.execCommand(cmd, false, val);
  }

  function createTable(rows, cols) {
    rows = Math.max(1, Math.min(50, rows|0));
    cols = Math.max(1, Math.min(20, cols|0));
    const table = document.createElement('table');
    const thead = document.createElement('thead');
    const trh = document.createElement('tr');
    for (let c = 0; c < cols; c++) { const th = document.createElement('th'); th.textContent = `H${c+1}`; trh.appendChild(th); }
    thead.appendChild(trh);
    table.appendChild(thead);
    const tbody = document.createElement('tbody');
    for (let r = 0; r < rows; r++) {
      const tr = document.createElement('tr');
      for (let c = 0; c < cols; c++) { const td = document.createElement('td'); td.textContent = ''; tr.appendChild(td); }
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    return table;
  }

  function currentCell() {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return null;
    const node = sel.anchorNode instanceof Text ? sel.anchorNode.parentNode : sel.anchorNode;
    return findAncestor(node, ['TD', 'TH']);
  }

  function addRow(delta) {
    const cell = currentCell();
    if (!cell) return;
    const tr = findAncestor(cell, 'TR');
    const tbody = findAncestor(cell, 'TBODY') || findAncestor(cell, 'THEAD');
    if (!tr || !tbody) return;
    const cols = tr.children.length;
    const newTr = document.createElement('tr');
    for (let i = 0; i < cols; i++) newTr.appendChild(document.createElement('td'));
    if (delta > 0) {
      tr.parentNode.insertBefore(newTr, tr.nextSibling);
    } else {
      tr.parentNode.insertBefore(newTr, tr);
    }
  }

  function addCol(delta) {
    const cell = currentCell();
    if (!cell) return;
    const table = findAncestor(cell, 'TABLE');
    const idx = Array.prototype.indexOf.call(cell.parentNode.children, cell);
    qsa('tr', table).forEach(tr => {
      const ref = tr.children[idx] || tr.lastElementChild;
      const newCell = tr.parentNode.nodeName === 'THEAD' ? document.createElement('th') : document.createElement('td');
      if (delta > 0) { ref.after(newCell); } else { ref.before(newCell); }
    });
  }

  function delRow() {
    const cell = currentCell();
    if (!cell) return;
    const tr = findAncestor(cell, 'TR');
    if (tr && tr.parentNode) tr.parentNode.removeChild(tr);
  }

  function delCol() {
    const cell = currentCell();
    if (!cell) return;
    const table = findAncestor(cell, 'TABLE');
    const idx = Array.prototype.indexOf.call(cell.parentNode.children, cell);
    qsa('tr', table).forEach(tr => { if (tr.children[idx]) tr.removeChild(tr.children[idx]); });
  }

  // Public API
  const WikiEditor = {
    init(opts) {
      const textarea = qs(opts.textareaSelector);
      const editor = qs(opts.editorSelector);
      const toolbar = qs(opts.toolbarSelector);
      const form = qs(opts.formSelector);
      const buttons = () => qsa('[data-cmd]', toolbar);
      const toggleRawBtn = toolbar ? toolbar.querySelector('#toggleRawBtn') : null;
      const wikiLinkBtn = toolbar ? toolbar.querySelector('#insertWikiLinkBtn') : null;
      const unixTimeBtn = toolbar ? toolbar.querySelector('#insertUnixTimeBtn') : null;
      const toolbarButtonsAll = () => toolbar ? qsa('button', toolbar) : [];
      let isRawMode = false;

      function setToolbarDisabled(disabled) {
        toolbarButtonsAll().forEach(btn => {
          if (!btn || btn === toggleRawBtn) return;
          btn.disabled = !!disabled;
        });
      }

      function updateToggleButtonLabel() {
        if (!toggleRawBtn) return;
        if (isRawMode) {
          toggleRawBtn.innerHTML = '<i class="fas fa-pen-to-square"></i> Visual Editor';
        } else {
          toggleRawBtn.innerHTML = '<i class="fas fa-code"></i> Raw Markdown';
        }
        toggleRawBtn.setAttribute('aria-pressed', isRawMode ? 'true' : 'false');
        toggleRawBtn.classList.toggle('active', isRawMode);
      }


      function dispatchEditorInput() {
        if (!editor) return;
        try {
          editor.dispatchEvent(new Event('input', { bubbles: true }));
        } catch (_) {
          const evt = document.createEvent('Event');
          evt.initEvent('input', true, false);
          editor.dispatchEvent(evt);
        }
      }

      function syncTextareaFromEditor() {
        if (!textarea || isRawMode) return;
        try {
          textarea.value = htmlToMd(editor);
        } catch (_) {
          textarea.value = editor ? editor.textContent || '' : (textarea.value || '');
        }
      }

      function refreshVisualAfterSnippet(meta = {}) {
        if (isRawMode || !editor) return;
        const markdown = meta.markdown || htmlToMd(editor);
        editor.innerHTML = mdToHtml(markdown);
        if (textarea) textarea.value = markdown;
        updateToolbarState();
        dispatchEditorInput();
        const targetUrl = meta.url || '';
        let targetSup = null;
        if (targetUrl) {
          const candidates = Array.from(editor.querySelectorAll('sup.source-ref'));
          targetSup = candidates.find(node => (node.getAttribute('data-source-url') || '') === targetUrl) || null;
          if (!targetSup && candidates.length) {
            targetSup = candidates[candidates.length - 1];
          }
        }
        if (targetSup) {
          const range = document.createRange();
          range.setStartAfter(targetSup);
          range.collapse(true);
          const selection = window.getSelection();
          if (selection) {
            selection.removeAllRanges();
            selection.addRange(range);
          }
        }
        captureSelection();
      }

      function insertSnippet(snippet) {
        if (!snippet) return false;
        if (isRawMode && textarea) {
          textarea.focus();
          const start = typeof textarea.selectionStart === 'number' ? textarea.selectionStart : textarea.value.length;
          const end = typeof textarea.selectionEnd === 'number' ? textarea.selectionEnd : start;
          const value = textarea.value || '';
          textarea.value = value.slice(0, start) + snippet + value.slice(end);
          const pos = start + snippet.length;
          if (typeof textarea.setSelectionRange === 'function') {
            textarea.setSelectionRange(pos, pos);
          }
          try {
            textarea.dispatchEvent(new Event('input', { bubbles: true }));
          } catch (_) {
            const evt = document.createEvent('Event');
            evt.initEvent('input', true, false);
            textarea.dispatchEvent(evt);
          }
          return true;
        }
        if (!editor) return false;
        if (typeof restoreSavedSelection === 'function') {
          const sel = window.getSelection();
          let needsRestore = true;
          if (sel && sel.rangeCount) {
            const currentRange = sel.getRangeAt(0);
            const ancestor = currentRange && currentRange.commonAncestorContainer;
            needsRestore = !ancestor || !editor.contains(ancestor);
          }
          if (needsRestore && lastSelectionRange && lastSelectionEditor) {
            restoreSavedSelection();
          }
        }
        try {
          editor.focus({ preventScroll: true });
        } catch (_) {
          editor.focus();
        }
        let didInsert = false;
        try {
          if (document.queryCommandSupported && document.queryCommandSupported('insertText')) {
            didInsert = document.execCommand('insertText', false, snippet);
          } else if (document.execCommand) {
            didInsert = document.execCommand('insertText', false, snippet);
          }
        } catch (_) {
          didInsert = false;
        }
        if (!didInsert) {
          const range = getRange();
          if (range) {
            range.deleteContents();
            const textNode = document.createTextNode(snippet);
            range.insertNode(textNode);
            range.setStartAfter(textNode);
            range.collapse(true);
            const sel = window.getSelection();
            if (sel) {
              sel.removeAllRanges();
              sel.addRange(range);
            }
            didInsert = true;
          } else {
            editor.appendChild(document.createTextNode(snippet));
            didInsert = true;
          }
        }
        if (didInsert) {
          dispatchEditorInput();
          captureSelection();
          updateToolbarState();
          syncTextareaFromEditor();
        }
        return didInsert;
      }

      function insertSourceCitation(rawUrl) {
        const url = (rawUrl || '').trim();
        if (!url) return false;
        let encoded = url;
        try {
          encoded = encodeURI(url);
        } catch (_) {
          encoded = url;
        }
        const markdownUrl = encoded.replace(/\|/g, '%7C');
        const snippet = '{{source|url=' + markdownUrl + '}}';
        let inserted = insertSnippet(snippet);
        if (inserted) {
          if (!isRawMode) {
            requestAnimationFrame(() => refreshVisualAfterSnippet({ url: markdownUrl }));
          }
          return true;
        }
        if (textarea) {
          textarea.focus();
          const value = textarea.value || '';
          const start = typeof textarea.selectionStart === 'number' ? textarea.selectionStart : value.length;
          const end = typeof textarea.selectionEnd === 'number' ? textarea.selectionEnd : start;
          textarea.value = value.slice(0, start) + snippet + value.slice(end);
          const pos = start + snippet.length;
          if (typeof textarea.setSelectionRange === 'function') {
            textarea.setSelectionRange(pos, pos);
          }
          try {
            textarea.dispatchEvent(new Event('input', { bubbles: true }));
          } catch (_) {
            const fallbackEvt = document.createEvent('Event');
            fallbackEvt.initEvent('input', true, false);
            textarea.dispatchEvent(fallbackEvt);
          }
          if (!isRawMode) {
            requestAnimationFrame(() => refreshVisualAfterSnippet({ url: markdownUrl, markdown: textarea ? textarea.value : undefined }));
          }
          return true;
        }
        return false;
      }


      function setRawMode(enabled) {
        if (!textarea || !editor) return;
        if (enabled === isRawMode) {
          updateToggleButtonLabel();
          setToolbarDisabled(enabled);
          return;
        }
        isRawMode = !!enabled;
        setToolbarDisabled(isRawMode);
        if (toolbar) {
          toolbar.classList.toggle('raw-mode', isRawMode);
        }
        if (isRawMode) {
          textarea.value = htmlToMd(editor);
          textarea.style.display = 'block';
          textarea.classList.add('is-visible');
          editor.style.display = 'none';
          lastSelectionRange = null;
          textarea.focus();
        } else {
          const markdown = textarea.value;
          try {
            editor.innerHTML = mdToHtml(markdown);
          } catch (error) {
            editor.textContent = markdown;
          }
          textarea.style.display = 'none';
          textarea.classList.remove('is-visible');
          editor.style.display = '';
          updateToolbarState();
          editor.focus();
        }
        updateToggleButtonLabel();
      }


      function captureSelection() {
        if (isRawMode) return;
        const sel = window.getSelection();
        if (!sel || sel.rangeCount === 0) return;
        const range = sel.getRangeAt(0);
        const ancestor = range.commonAncestorContainer;
        if (!ancestor || !editor.contains(ancestor)) return;
        lastSelectionEditor = editor;
        lastSelectionRange = range.cloneRange();
      }

      function restoreSavedSelection() {
        if (isRawMode) return false;
        if (!lastSelectionRange || !lastSelectionEditor) return false;
        if (!document.contains(lastSelectionEditor)) {
          lastSelectionRange = null;
          lastSelectionEditor = null;
          return false;
        }
        try {
          lastSelectionEditor.focus({ preventScroll: true });
        } catch (_) {
          lastSelectionEditor.focus();
        }
        const sel = window.getSelection();
        if (!sel) return false;
        sel.removeAllRanges();
        sel.addRange(lastSelectionRange.cloneRange());
        return true;
      }

      function clearActive() {
        buttons().forEach(b => b.classList.remove('active'));
      }

      function setActive(selector) {
        const btn = toolbar.querySelector(selector);
        if (btn) btn.classList.add('active');
      }

      function updateToolbarState() {
        if (isRawMode) {
          clearActive();
          return;
        }
        const sel = window.getSelection();
        if (!sel || sel.rangeCount === 0) return;
        let node = sel.anchorNode;
        if (!node) return;
        if (node.nodeType === Node.TEXT_NODE) node = node.parentNode;
        if (!editor.contains(node)) return; // only update when selection is in editor

        clearActive();

        // Heading/paragraph state
        const block = findAncestor(node, ['H1','H2','H3','P','DIV']);
        if (block) {
          const tag = block.nodeName;
          if (tag === 'H1' || tag === 'H2' || tag === 'H3') {
            setActive(`[data-cmd="formatBlock"][data-value="${tag}"]`);
          } else {
            setActive('[data-cmd="formatBlock"][data-value="P"]');
          }
        }

        // Inline styles
        try {
          if (document.queryCommandState('bold')) setActive('[data-cmd="bold"]');
          if (document.queryCommandState('italic')) setActive('[data-cmd="italic"]');
          if (document.queryCommandState('underline')) setActive('[data-cmd="underline"]');
          if (document.queryCommandState('insertUnorderedList')) setActive('[data-cmd="insertUnorderedList"]');
          if (document.queryCommandState('insertOrderedList')) setActive('[data-cmd="insertOrderedList"]');
        } catch (_) { /* some browsers may restrict queryCommandState */ }
      }

      const NON_WHITESPACE_RE = /[^\s\u00A0\u200B]/;

      function hasTextContent(value) {
        return NON_WHITESPACE_RE.test(value || '');
      }

      function isWhitespaceTextNode(node) {
        return node && node.nodeType === Node.TEXT_NODE && !hasTextContent(node.textContent);
      }

      function createWalker() {
        return document.createTreeWalker(
          editor,
          NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT
        );
      }

      function getPreviousNode(range) {
        const walker = createWalker();
        const { startContainer, startOffset } = range;
        let candidate = null;

        if (startContainer.nodeType === Node.TEXT_NODE) {
          const before = (startContainer.textContent || '').slice(0, startOffset);
          if (hasTextContent(before)) return 'TEXT';
          walker.currentNode = startContainer;
          candidate = walker.previousNode();
        } else if (startOffset > 0) {
          candidate = startContainer.childNodes[startOffset - 1];
          while (candidate && candidate.nodeType === Node.ELEMENT_NODE && candidate.lastChild) {
            candidate = candidate.lastChild;
          }
          if (candidate) walker.currentNode = candidate;
        } else {
          walker.currentNode = startContainer;
          candidate = walker.previousNode();
        }

        while (candidate && isWhitespaceTextNode(candidate)) {
          walker.currentNode = candidate;
          candidate = walker.previousNode();
        }

        if (candidate && candidate.nodeType === Node.TEXT_NODE) {
          return hasTextContent(candidate.textContent) ? 'TEXT' : null;
        }

        return candidate || null;
      }

      function getNextNode(range) {
        const walker = createWalker();
        const { startContainer, startOffset } = range;
        let candidate = null;

        if (startContainer.nodeType === Node.TEXT_NODE) {
          const len = startContainer.textContent ? startContainer.textContent.length : 0;
          const after = (startContainer.textContent || '').slice(startOffset, len);
          if (hasTextContent(after)) return 'TEXT';
          walker.currentNode = startContainer;
          candidate = walker.nextNode();
        } else if (startOffset < startContainer.childNodes.length) {
          candidate = startContainer.childNodes[startOffset];
          while (candidate && candidate.nodeType === Node.ELEMENT_NODE && candidate.firstChild) {
            candidate = candidate.firstChild;
          }
          if (candidate) walker.currentNode = candidate;
        } else {
          walker.currentNode = startContainer;
          candidate = walker.nextNode();
        }

        while (candidate && isWhitespaceTextNode(candidate)) {
          walker.currentNode = candidate;
          candidate = walker.nextNode();
        }

        if (candidate && candidate.nodeType === Node.TEXT_NODE) {
          return hasTextContent(candidate.textContent) ? 'TEXT' : null;
        }

        return candidate || null;
      }

      function removeAdjacentImage(range, direction) {
        const { startContainer, startOffset } = range;
        const target = direction === 'backward' ? getPreviousNode(range) : getNextNode(range);
        if (!target || target === 'TEXT') return false;
        if (target.nodeType !== Node.ELEMENT_NODE || target.nodeName !== 'IMG') return false;

        const selection = window.getSelection();
        const caretRange = range.cloneRange();

        target.remove();

        try {
          if (startContainer.nodeType === Node.TEXT_NODE) {
            const text = startContainer.textContent || '';
            const newOffset = Math.min(startOffset, text.length);
            caretRange.setStart(startContainer, newOffset);
          } else {
            const container = startContainer;
            const childCount = container.childNodes.length;
            const baseOffset = Math.min(startOffset, childCount);
            const newOffset = direction === 'backward' ? Math.max(0, baseOffset - 1) : baseOffset;
            caretRange.setStart(container, newOffset);
          }
          caretRange.collapse(true);
        } catch (_) {
          caretRange.selectNodeContents(editor);
          caretRange.collapse(direction === 'backward');
        }

        if (selection) {
          selection.removeAllRanges();
          selection.addRange(caretRange);
        }

        try {
          editor.dispatchEvent(new Event('input', { bubbles: true }));
        } catch (_) {
          const evt = document.createEvent('Event');
          evt.initEvent('input', true, false);
          editor.dispatchEvent(evt);
        }
        updateToolbarState();
        return true;
      }

      // Render initial content
      try {
        editor.innerHTML = mdToHtml(opts.initialMarkdown || textarea.value || '');
      } catch (e) {
        editor.textContent = textarea.value || '';
      }

      // Toolbar actions via execCommand
      qsa('[data-cmd]', toolbar).forEach(btn => {
        btn.addEventListener('click', () => {
          if (isRawMode) {
            if (textarea) textarea.focus();
            return;
          }
          const cmd = btn.getAttribute('data-cmd');
          const val = (btn.getAttribute('data-value') || '').toUpperCase();
          editor.focus();

          // Toggle headings back to paragraph if the same level is clicked
          if (cmd === 'formatBlock' && val) {
            const sel = window.getSelection();
            let node = sel && sel.anchorNode;
            if (node && node.nodeType === Node.TEXT_NODE) node = node.parentNode;
            const block = findAncestor(node, ['H1', 'H2', 'H3', 'P', 'DIV']);
            if (block && block.nodeName === val && val !== 'P') {
              exec('formatBlock', 'P');
              return;
            }
            exec('formatBlock', val);
            return;
          }

          exec(cmd, val || null);
          updateToolbarState();
        });
      });
      if (toggleRawBtn) {
        toggleRawBtn.addEventListener('click', () => setRawMode(!isRawMode));
        updateToggleButtonLabel();
      }

      function requestWikiLinkModal() {
        const detail = { handled: false };
        try {
          const evt = new CustomEvent('wikiLink:open', { bubbles: true, cancelable: false, detail });
          document.dispatchEvent(evt);
        } catch (_) {
          if (typeof document.createEvent === 'function') {
            const evt = document.createEvent('CustomEvent');
            evt.initCustomEvent('wikiLink:open', true, false, detail);
            document.dispatchEvent(evt);
          }
        }
        return detail.handled;
      }

      if (wikiLinkBtn) {
        wikiLinkBtn.addEventListener('click', () => {
          if (wikiLinkBtn.disabled) return;
          const handled = requestWikiLinkModal();
          if (handled) return;
          const page = prompt("Wiki page name (required)");
          if (!page) return;
          const trimmedPage = page.trim();
          if (!trimmedPage) return;
          let branch = prompt("Branch name (optional)");
          WikiEditor.insertWikiLink({ page: trimmedPage, branch });
        });
      }

      if (unixTimeBtn) {
        unixTimeBtn.addEventListener('click', () => {
          if (unixTimeBtn.disabled) return;
          const defaultValue = String(Math.floor(Date.now() / 1000));
          const input = prompt('Unix timestamp (seconds)', defaultValue);
          if (input === null) return;
          const value = String(input).trim();
          if (!value) return;
          if (!/^[0-9]+$/.test(value)) {
            alert('Unix timestamp must be digits only.');
            return;
          }
          restoreSavedSelection();
          insertSnippet('{{ global.unix:' + value + ' }}');
        });
      }

      // Link creation
      const linkBtn = qs('#createLinkBtn');
      if (linkBtn) linkBtn.addEventListener('click', () => {
        const url = prompt('Enter URL');
        if (url) { editor.focus(); exec('createLink', url); }
      });
      // Table insertion
      const insertTableBtn = qs('#insertTableBtn');
      if (insertTableBtn) insertTableBtn.addEventListener('click', () => {
        const rows = parseInt(prompt('Rows?', '2') || '2', 10);
        const cols = parseInt(prompt('Columns?', '2') || '2', 10);
        const table = createTable(rows, cols);
        insertNodeAtSelection(table);
      });
      // Table row/col ops
      const addRowBtn = qs('#addRowBtn');
      const addColBtn = qs('#addColBtn');
      const delRowBtn = qs('#delRowBtn');
      const delColBtn = qs('#delColBtn');
      if (addRowBtn) addRowBtn.addEventListener('click', () => addRow(+1));
      if (delRowBtn) delRowBtn.addEventListener('click', () => delRow());
      if (addColBtn) addColBtn.addEventListener('click', () => addCol(+1));
      if (delColBtn) delColBtn.addEventListener('click', () => delCol());

      // Ensure Backspace/Delete can remove images, including those nested in tables.
      editor.addEventListener('keydown', (ev) => {
        if (ev.defaultPrevented) return;
        if (ev.key !== 'Backspace' && ev.key !== 'Delete') return;
        if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
        const range = getRange();
        if (!range || !range.collapsed) return;
        const container = range.startContainer;
        if (container !== editor && !editor.contains(container)) return;
        const direction = ev.key === 'Backspace' ? 'backward' : 'forward';
        if (removeAdjacentImage(range.cloneRange(), direction)) {
          ev.preventDefault();
        }
      });

      // Sync on submit: serialize editor HTML to Markdown
      if (form) {
        form.addEventListener('submit', (e) => {
          const md = isRawMode ? textarea.value : htmlToMd(editor);
          textarea.value = md;
          // Simple required validation since hidden textarea isn't required
          if (!md.trim()) {
            e.preventDefault();
            editor.focus();
            alert('Content cannot be empty.');
          }
        });
      }

      // Keep toolbar state and selection in sync with editor activity
      function handleEditorInteraction() {
        if (isRawMode) return;
        captureSelection();
        updateToolbarState();
      }

      ['keyup','mouseup','mouseleave','input','focus','blur'].forEach(ev => {
        editor.addEventListener(ev, handleEditorInteraction);
      });
      document.addEventListener('selectionchange', handleEditorInteraction);

      WikiEditor.captureSelection = captureSelection;
      WikiEditor.restoreSelection = restoreSavedSelection;
      WikiEditor.insertWikiLink = function ({ page, branch } = {}) {
        const pageName = (page || '').trim();
        if (!pageName) return false;
        const branchName = (branch || '').trim();
        let snippet = '[[ ' + pageName;
        if (branchName) snippet += ':' + branchName;
        snippet += ' ]]';
        insertSnippet(snippet);
        return true;
      };
      WikiEditor.requestWikiLinkModal = requestWikiLinkModal;
      WikiEditor.insertSnippet = insertSnippet;
      WikiEditor.insertSourceCitation = insertSourceCitation;
      WikiEditor.syncTextareaFromEditor = syncTextareaFromEditor;
      WikiEditor.isRawMode = () => isRawMode;

      // Initial state
      updateToolbarState();
    },
    insertImage({ src, alt }) {
      if (!src) return;
      if (typeof WikiEditor.restoreSelection === 'function') {
        WikiEditor.restoreSelection();
      }
      const img = document.createElement('img');
      img.src = src;
      img.alt = alt || '';
      insertNodeAtSelection(img);

      let rangeForCaret = null;
      const parent = img.parentNode;
      if (parent) {
        const spacer = document.createTextNode('\u200B');
        parent.insertBefore(spacer, img.nextSibling);
        const selection = window.getSelection();
        const range = document.createRange();
        range.setStart(spacer, 0);
        range.collapse(true);
        if (selection) {
          selection.removeAllRanges();
          selection.addRange(range);
        }
        rangeForCaret = range;
        let editorEl = parent;
        while (editorEl && editorEl.nodeType === Node.ELEMENT_NODE && !editorEl.isContentEditable) {
          editorEl = editorEl.parentNode;
        }
        if (editorEl && editorEl.isContentEditable) {
          lastSelectionEditor = editorEl;
        }
      }

      if (!rangeForCaret) {
        rangeForCaret = getRange();
      }
      if (rangeForCaret) {
        lastSelectionRange = rangeForCaret.cloneRange();
      }
    }
  };

  window.WikiEditor = WikiEditor;
})();

