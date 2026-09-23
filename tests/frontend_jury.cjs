const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const elements = new Map();
const element = selector => {
  if (!elements.has(selector)) elements.set(selector, {innerHTML:'',value:'',textContent:'',addEventListener(){}});
  return elements.get(selector);
};
const context = vm.createContext({localStorage:{getItem(){return null;}},document:{querySelector:element,querySelectorAll(){return [];}}});
const code = fs.readFileSync('static/app.js','utf8').replace(/\ninit\(\);\s*$/, '\n');
vm.runInContext(code, context);
assert.equal(vm.runInContext(`escapeHtml('<script>')`,context),'&lt;script&gt;');
assert.ok(vm.runInContext(`markedDifference('<script> test','test')`,context).includes('&lt;script&gt;'));
const page=fs.readFileSync('static/workspace.html','utf8');
for(const id of ['before-files','after-files','analyze-button','findings-body','before-quote','after-quote','report-html','new-comparison']) assert.equal((page.match(new RegExp('id="'+id+'"','g'))||[]).length,1,id);
for(const text of ['jury-demo','demo-button','Открытое демо','Судьба каждой']) assert.ok(!page.includes(text));
assert.ok(page.includes('baqbaq-transparent.png'));
assert.ok(!page.includes('<strong>BaqBaq</strong>'));
console.log('PASS: Live controls, supplied logo, no duplicated brand/demo, escaping');
