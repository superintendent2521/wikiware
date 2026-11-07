const test = require('node:test');
const assert = require('node:assert/strict');
const { browser, input, submit, settle } = require('./browser-harness.cjs');

test('opening and saving preserves authored Markdown byte for byte', async () => {
  const markdown = '## Header\n\n```text\n[[literal]]\n```\n\n| A | B |\n| - | - |\n| 1 | 2 |\n\n{{source|url=https://example.com}}\n\n![Alt](/media/a.png "Caption"){width=320}\n';
  const app = browser({ markdown });
  app.start();
  assert.equal(app.window.WikiEditor.getMarkdown(), markdown);
  submit(app.elements.editForm);
  await settle();
  assert.equal(app.elements.content.value, markdown);
  assert.equal(JSON.parse(app.records.get(app.key)).markdown, markdown);
});

test('network failure retains draft and leaves the editor usable', async () => {
  const app = browser();
  app.start();
  input(app.elements.content, '# My unsaved work');
  submit(app.elements.editForm);
  await settle();
  assert.equal(JSON.parse(app.records.get(app.key)).markdown, '# My unsaved work');
  assert.equal(app.elements.content.value, '# My unsaved work');
  assert.equal(app.elements.content.readOnly, false);
  assert.equal(app.navigations.length, 0);
  assert.match(app.elements.editorStatus.textContent, /Network unavailable/);
});

test('draft clears only after successful persistence acknowledgment', async () => {
  let resolveRequest;
  const app = browser({ fetch: () => new Promise(resolve => { resolveRequest = resolve; }) });
  app.start();
  input(app.elements.content, 'Changed');
  submit(app.elements.editForm);
  assert.equal(JSON.parse(app.records.get(app.key)).markdown, 'Changed');
  resolveRequest({ ok: true, json: async () => ({ redirect_url: '/page/Article' }) });
  await settle();
  assert.equal(app.records.has(app.key), false);
  assert.equal(app.timers.size, 0);
  assert.deepEqual(app.navigations, ['http://wiki.test/page/Article']);
});

test('a 409 conflict keeps submitted content and recovery copy', async () => {
  const app = browser({ fetch: async () => ({ ok: false, status: 409,
    json: async () => ({ error: 'This page changed since you opened it.' }) }) });
  app.start();
  input(app.elements.content, 'My edit');
  submit(app.elements.editForm);
  await settle();
  assert.equal(JSON.parse(app.records.get(app.key)).markdown, 'My edit');
  assert.match(app.elements.editorStatus.textContent, /page changed/);
  assert.equal(app.navigations.length, 0);
});

test('saving one tab cannot erase a newer draft written in another tab', async () => {
  let resolveRequest;
  const app = browser({ fetch: () => new Promise(resolve => { resolveRequest = resolve; }) });
  app.start();
  submit(app.elements.editForm);
  app.records.set(app.key, JSON.stringify({ markdown: 'Other tab work' }));
  resolveRequest({ ok: true, json: async () => ({ redirect_url: '/page/Article' }) });
  await settle();
  assert.equal(JSON.parse(app.records.get(app.key)).markdown, 'Other tab work');
});

test('an empty draft remains recoverable', () => {
  const app = browser();
  app.records.set(app.key, JSON.stringify({ markdown: '', updatedAt: Date.now() }));
  app.start();
  assert.equal(app.elements.draftRestoreBanner.hidden, false);
  app.elements.draftRestoreBtn.dispatchEvent(new Event('click'));
  assert.equal(app.elements.content.value, '');
});

test('disabled local storage does not stop editing or successful saves', async () => {
  const app = browser({ storageError: true, fetch: async () => ({ ok: true,
    json: async () => ({ redirect_url: '/page/Article' }) }) });
  app.start();
  submit(app.elements.editForm);
  await settle();
  assert.deepEqual(app.navigations, ['http://wiki.test/page/Article']);
  assert.match(app.elements.draftStatusText.textContent, /unavailable/);
});

test('preview uses server HTML and does not modify Markdown', async () => {
  const app = browser({ markdown: '**Keep this exactly**', fetch: async () => ({ ok: true,
    json: async () => ({ html: '<p><strong>Keep this exactly</strong></p>' }) }) });
  app.start();
  app.elements.toggleRawBtn.dispatchEvent(new Event('click'));
  await settle();
  assert.equal(app.requests[0][0], '/api/preview');
  assert.equal(app.requests[0][1].body.get('content'), '**Keep this exactly**');
  assert.equal(app.requests[0][1].body.get('csrf_token'), 'token');
  assert.equal(app.elements['visual-editor'].innerHTML, '<p><strong>Keep this exactly</strong></p>');
  app.elements.toggleRawBtn.dispatchEvent(new Event('click'));
  assert.equal(app.elements.content.value, '**Keep this exactly**');
  assert.equal(app.elements.content.hidden, false);
});

test('preview failure displays a useful message without losing content', async () => {
  const app = browser();
  app.start();
  const original = app.elements.content.value;
  app.elements.toggleRawBtn.dispatchEvent(new Event('click'));
  await settle();
  assert.match(app.elements['visual-editor'].textContent, /Preview unavailable/);
  assert.equal(app.elements.content.value, original);
});

test('insertion tools operate on source without serializing the rest of the page', () => {
  const app = browser({ markdown: 'before\n\n```\n[[literal]]\n```\nafter' });
  app.start();
  app.elements.content.setSelectionRange(0, 6);
  app.window.WikiEditor.captureSelection();
  assert.equal(app.window.WikiEditor.insertWikiLink({ page: 'Home', branch: 'draft' }), true);
  assert.equal(app.elements.content.value, '[[ Home:draft ]]\n\n```\n[[literal]]\n```\nafter');
  assert.equal(app.window.WikiEditor.insertSourceCitation('javascript:alert(1)'), false);
  assert.equal(app.window.WikiEditor.insertWikiLink({ page: 'Bad]]name' }), false);
});

test('successful save cannot navigate to a different origin', async () => {
  const app = browser({ fetch: async () => ({ ok: true,
    json: async () => ({ redirect_url: 'https://other.example/page' }) }) });
  app.start();
  submit(app.elements.editForm);
  await settle();
  assert.equal(app.navigations.length, 0);
  assert.equal(app.records.has(app.key), true);
});
