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

  // Minimal Markdown -> HTML renderer for preview/editing
  function mdToHtml(md) {
    // Normalize line endings
    md = (md || '').replace(/\r\n?/g, '\n');

    // Escape HTML first
    const escapeHtml = s => s
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');

    // Inline replacements: bold, italic, code, links, images
    function renderInline(text) {
      if (!text) return '';
      // Escape first
      text = escapeHtml(text);
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
        return node.nodeValue.replace(/\n/g, ' ');
      }
      if (node.nodeType !== Node.ELEMENT_NODE) return '';
      const name = node.nodeName;
      const children = Array.from(node.childNodes).map(serialize).join('');
      switch (name) {
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

      // Render initial content
      try {
        editor.innerHTML = mdToHtml(opts.initialMarkdown || textarea.value || '');
      } catch (e) {
        editor.textContent = textarea.value || '';
      }

      // Toolbar actions via execCommand
      qsa('[data-cmd]', toolbar).forEach(btn => {
        btn.addEventListener('click', () => {
          const cmd = btn.getAttribute('data-cmd');
          const val = btn.getAttribute('data-value');
          editor.focus();
          exec(cmd, val);
        });
      });
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

      // Sync on submit: serialize editor HTML to Markdown
      if (form) {
        form.addEventListener('submit', () => {
          const md = htmlToMd(editor);
          textarea.value = md;
        });
      }
    },
    insertImage({ src, alt }) {
      const img = document.createElement('img');
      img.src = src; img.alt = alt || '';
      insertNodeAtSelection(img);
    }
  };

  window.WikiEditor = WikiEditor;
})();

