const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const directory = path.join(__dirname, '..', 'templates');
for (const filename of fs.readdirSync(directory).filter(name => name.endsWith('.html'))) {
  const content = fs.readFileSync(path.join(directory, filename), 'utf8');
  const scripts = Array.from(content.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi));
  if (!scripts.length) continue;
  test(filename + ' inline scripts parse after template interpolation', () => {
    for (const [index, match] of scripts.entries()) {
      const script = match[1].replace(/\{\{[\s\S]*?\}\}/g, 'null').replace(/\{%[\s\S]*?%\}/g, '');
      assert.doesNotThrow(() => new vm.Script(script, { filename: filename + ':' + index }));
    }
  });
}
