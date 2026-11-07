const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

class Element extends EventTarget {
  constructor(value = '') {
    super();
    this.value = value;
    this.style = {};
    this.dataset = {};
    this.hidden = false;
    this.disabled = false;
    this.textContent = '';
    this.innerHTML = '';
    this.selectionStart = 0;
    this.selectionEnd = 0;
    this.fields = {};
  }
  focus() {}
  setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
  setRangeText(text, start, end) {
    this.value = this.value.slice(0, start) + text + this.value.slice(end);
    this.setSelectionRange(start + text.length, start + text.length);
  }
  querySelector(selector) { return this.fields[selector] || null; }
  querySelectorAll() { return []; }
}

function browser({ markdown = '# Existing page', fetch: fetchImpl, storageError = false } = {}) {
  const names = [
    'draftStatusText', 'draftSaveBtn', 'draftClearBtn', 'draftRestoreBanner',
    'draftRestoreBtn', 'draftDiscardBtn', 'draftRestoreTimestamp', 'visual-editor',
    'content', 'editForm', 'wiki-editor-toolbar', 'toggleRawBtn', 'editorStatus'
  ];
  const elements = Object.fromEntries(names.map(name => [name, new Element()]));
  elements.content.value = markdown;
  elements.editForm.dataset = { draftTitle: 'Article', draftBranch: 'main', draftUser: 'alice' };
  elements.editForm.action = 'http://wiki.test/edit/Article';
  elements.editForm.fields['[name="csrf_token"]'] = new Element('token');
  elements.editForm.fields['[name="edit_summary"]'] = new Element('Test edit');
  const document = new EventTarget();
  document.getElementById = name => elements[name] || null;
  document.querySelector = selector => elements[selector.slice(1)] || null;
  const records = new Map();
  const localStorage = {
    getItem(key) { return records.get(key) || null; },
    setItem(key, value) {
      if (storageError) throw new Error('Storage full');
      records.set(key, value);
    },
    removeItem(key) { records.delete(key); }
  };
  const window = new EventTarget();
  const navigations = [];
  window.localStorage = localStorage;
  window.location = { origin: 'http://wiki.test', assign(url) { navigations.push(url); } };
  window.prompt = () => null;
  const timers = new Map();
  let timerId = 0;
  window.setTimeout = callback => { timers.set(++timerId, callback); return timerId; };
  const clearTimeout = id => timers.delete(id);
  const requests = [];
  const fetch = async (...args) => {
    requests.push(args);
    if (fetchImpl) return fetchImpl(...args);
    throw new Error('Network unavailable');
  };
  class FormData {
    constructor(form) { this.form = form; this.values = new Map(); }
    append(key, value) { this.values.set(key, value); }
    get(key) { return this.values.get(key); }
  }
  const context = vm.createContext({ window, document, Event, CustomEvent, URL, FormData,
    AbortController, fetch, clearTimeout, console });
  function load(filename) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', filename), 'utf8'), context);
  }
  function start() {
    load('editor.js');
    load('drafts.js');
    document.dispatchEvent(new Event('DOMContentLoaded'));
    window.WikiEditor.init({ textareaSelector: '#content', editorSelector: '#visual-editor',
      toolbarSelector: '#wiki-editor-toolbar', formSelector: '#editForm' });
  }
  return { elements, window, records, navigations, requests, timers, start,
    key: 'wikiware:draft:v1:alice:main:Article' };
}

function input(element, value) {
  element.value = value;
  element.dispatchEvent(new Event('input', { bubbles: true }));
}

function submit(form) { form.dispatchEvent(new Event('submit', { cancelable: true })); }
function settle() { return new Promise(resolve => setImmediate(resolve)); }

module.exports = { browser, input, submit, settle };
