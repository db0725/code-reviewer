// DOM-state regression checks only. These do not replace a browser/layout test.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
const script = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
const head = 'a'.repeat(40), base = 'b'.repeat(40);
const source = {kind: 'git', repository: 'E:/projects/DKNet', preview_id: 'preview-1', head_commit: head, base_commit: base,
  changed_files: ['main.py'], after_scope: {bytes: 24, files: [{path: 'main.py'}], excluded: [{path: 'data/train.csv', reason: '数据集不纳入'}]}};
const example = {id: 'git-aaaaaaaaaaaa', title: 'DKNet · 本地 Git 审查', description: '固定代码版本', files: [
  {path: 'main.py', changed: true, before: 'return 1\n', after: 'return 2\n', rows: [{kind: 'add', old: null, new: 1, text: 'return 2'}]}]};
const builtin = {...example, id: 'checkout', title: '内置示例'};
const running = {id: 'job-1', example_id: example.id, mode: 'real', status: 'running', source, result: null};
const done = {...running, status: 'completed', result: {complete: true, steps: 2, elapsed_seconds: 1, summary: '完成', findings: []}};

async function app({saved = new Map(), failPreview = false, maxSteps = 50, restoredContext} = {}) {
  class Element {
    constructor(tag) { this.tagName = tag; this.children = []; this.events = {}; this.attributes = {}; this.className = ''; this.hidden = false; this.disabled = false; this.value = ''; this._text = ''; this.scrollHeight = 0; }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map(x => x.textContent ?? '').join(''); }
    append(...items) { for (const item of items) this.children.push(typeof item === 'string' ? {textContent: item} : item); }
    replaceChildren(...items) { this.children = []; this._text = ''; this.append(...items); }
    setAttribute(key, value) { this.attributes[key] = value; }
    addEventListener(key, action) { this.events[key] = action; }
    get classList() { return {toggle: (key, enabled) => { const words = new Set(this.className.split(' ').filter(Boolean)); if (enabled) words.add(key); else words.delete(key); this.className = [...words].join(' '); }}; }
    set selectedIndex(index) { this.value = this.children[index]?.value || ''; }
    scrollIntoView() { this.scrolled = true; }
  }
  const nodes = {};
  for (const match of html.matchAll(/<([a-z][\w-]*)\b[^>]*\bid="([^"]+)"[^>]*>/g)) {
    const node = nodes[match[2]] = new Element(match[1]); node.id = match[2]; node.hidden = /\shidden(?:\s|>)/.test(match[0]);
  }
  const all = () => { const found = new Set(); function visit(node) { if (!node || found.has(node)) return; found.add(node); for (const child of node.children || []) visit(child); } Object.values(nodes).forEach(visit); return [...found]; };
  const document = {getElementById: id => nodes[id] || all().find(x => x.id === id), createElement: tag => new Element(tag),
    createTextNode: text => ({textContent: text}), createDocumentFragment: () => new Element('fragment'),
    querySelectorAll: selector => all().filter(x => x.className?.split(' ').includes(selector.slice(1)))};
  const requests = [], streams = [];
  const fetch = async (url, options) => {
    requests.push({url, body: options?.body && JSON.parse(options.body)});
    let value;
    if (url === '/api/examples') value = [builtin];
    else if (url === '/api/config') value = {configured: true, default_repository: source.repository, max_steps: maxSteps, timeout_seconds: 300};
    else if (url === '/api/git/inspect') value = {repository: source.repository, name: 'DKNet', commits: [{id: head, subject: 'new'}, {id: base, subject: 'old'}]};
    else if (url === '/api/git/preview') value = failPreview ? {error: '提交不存在'} : {preview_id: source.preview_id, example, source};
    else if (url === '/api/git/reviews') value = running;
    else if (url === '/api/reviews/job-1/input') value = {example, source: {...source, project_context: restoredContext}};
    else if (url === '/api/reviews/job-1') value = {...running, source: {...source, project_context: restoredContext}};
    else throw new Error('Unexpected request: ' + url);
    return {ok: !(failPreview && url === '/api/git/preview'), json: async () => value};
  };
  class EventSource { constructor(url) { this.url = url; this.listeners = {}; streams.push(this); } addEventListener(kind, fn) { this.listeners[kind] = fn; } close() { this.closed = true; } }
  const context = vm.createContext({document, fetch, EventSource, console, localStorage: {getItem: key => saved.get(key) || null, setItem: (key, value) => saved.set(key, value), removeItem: key => saved.delete(key)}});
  vm.runInContext(script, context);
  await new Promise(setImmediate);
  return {nodes, requests, streams, saved, run: code => vm.runInContext(code, context)};
}

test('Git workflow requires preview, uses its ID and exposes the report', async () => {
  const ui = await app();
  assert.match(ui.nodes['run-metrics'].textContent, /最多 50 轮.*5 分钟/);
  ui.run("switchSource('git')");
  assert.equal(ui.nodes['start-real'].disabled, true);
  assert.equal(ui.nodes['start-replay'].hidden, true);
  await ui.run('inspectRepo({preventDefault(){}})');
  await ui.run('previewGit()');
  assert.equal(ui.nodes['start-real'].disabled, false);
  assert.match(ui.nodes['source-context'].textContent, /数据集/);
  await ui.run("start('real')");
  assert.deepEqual(ui.requests.find(x => x.url === '/api/git/reviews').body, {preview_id: 'preview-1'});
  ui.streams.at(-1).listeners.done({lastEventId: '9', data: JSON.stringify(done)});
  assert.equal(ui.nodes.status.textContent, '已完成');
  assert.equal(ui.nodes.download.href, '/api/reviews/job-1/report');
  assert.equal(ui.nodes.download.hidden, false);
  ui.run("locate('main.py', 1)");
  assert.equal(ui.run('state.view'), 'source');
  assert.match(ui.nodes.report.textContent, /审查版本/);
});

test('budget hint follows server configuration after reset', async () => {
  const ui = await app({maxSteps: 17});
  ui.run("switchSource('git')");
  assert.match(ui.nodes['run-metrics'].textContent, /最多 17 轮.*5 分钟/);
});

test('counterevidence and uncertain candidates remain visible separately from findings', async () => {
  const ui = await app(); ui.run("switchSource('git')");
  await ui.run('inspectRepo({preventDefault(){}})'); await ui.run('previewGit()'); await ui.run("start('real')");
  const review = {candidate: {title: '状态未更新'}, verdict: 'refuted', status: 'judged', rationale: '发送函数已更新状态', limitations: ['静态复核'],
    supporting_evidence: [], counterevidence: [{version: 'after', file: 'main.py', line: 1, evidence: 'state.sent = True'}]};
  const pending = {...review, candidate: {title: '动态调用未知'}, verdict: 'uncertain', status: 'not_run', rationale: '预算不足', counterevidence: []};
  const job = {...done, status: 'limited', result: {...done.result, complete: false, candidate_reviews: [review, pending],
    verification_summary: {supported: 0, refuted: 1, uncertain: 1}}};
  ui.streams.at(-1).listeners.done({lastEventId: '70', data: JSON.stringify(job)});
  assert.match(ui.nodes.report.textContent, /已有反证：状态未更新/);
  assert.match(ui.nodes.report.textContent, /state.sent = True/);
  assert.match(ui.nodes.report.textContent, /待核实：动态调用未知/);
  assert.doesNotMatch(ui.nodes.report.textContent, /未发现可确认的问题/);
});

test('managed review displays partial scope and token usage without claiming no defects', async () => {
  const ui = await app(); ui.run("switchSource('git')");
  await ui.run('inspectRepo({preventDefault(){}})'); await ui.run('previewGit()'); await ui.run("start('real')");
  const job = {...done, status: 'limited', result: {...done.result, complete: false, usage_complete: true,
    usage: {prompt_tokens: 12000, completion_tokens: 100}, coverage: {completed_batches: 1, batches: 2,
      completed_files: ['main.py'], code_files: ['main.py', 'other.py'], pending_files: ['other.py'], context_only_files: ['README.md']}}};
  ui.streams.at(-1).listeners.done({lastEventId: '50', data: JSON.stringify(job)});
  assert.match(ui.nodes.report.textContent, /批次完成 1\/2/);
  assert.match(ui.nodes.report.textContent, /未完成：other.py/);
  assert.match(ui.nodes.report.textContent, /仅作上下文：README.md/);
  assert.match(ui.nodes.report.textContent, /12,000 token/);
  assert.doesNotMatch(ui.nodes.report.textContent, /未发现可确认的问题/);
});

test('version change invalidates an existing preview before another run', async () => {
  const ui = await app(); ui.run("switchSource('git')");
  await ui.run('inspectRepo({preventDefault(){}})'); await ui.run('previewGit()');
  ui.nodes['head-ref'].events.change();
  assert.equal(ui.nodes['start-real'].disabled, true);
  assert.equal(ui.run('state.gitPreview'), null);
  await ui.run("start('real')");
  assert.equal(ui.requests.filter(x => x.url === '/api/git/reviews').length, 0);
});

test('preview failure leaves no runnable snapshot and shows the error', async () => {
  const ui = await app({failPreview: true}); ui.run("switchSource('git')");
  await ui.run('inspectRepo({preventDefault(){}})'); await ui.run('previewGit()');
  assert.equal(ui.nodes['start-real'].disabled, true);
  assert.match(ui.nodes.error.textContent, /提交不存在/);
  assert.match(ui.nodes['repo-summary'].textContent, /预览失败：提交不存在/);
  assert.equal(ui.nodes.error.hidden, false);
});

test('refresh restores a Git snapshot and retains the job ID for another refresh', async () => {
  const saved = new Map([['code-review-job', 'job-1']]);
  const ui = await app({saved});
  assert.equal(ui.run('state.source'), 'git');
  assert.equal(ui.run('state.selected.id'), example.id);
  assert.equal(saved.get('code-review-job'), 'job-1');
  assert.equal(ui.nodes['start-real'].disabled, true);
  ui.streams.at(-1).listeners.done({lastEventId: '9', data: JSON.stringify(done)});
  const second = await app({saved});
  assert.equal(second.run('state.selected.id'), example.id);
  assert.equal(saved.get('code-review-job'), 'job-1');
});

test('project context is submitted with preview and edits invalidate that preview', async () => {
  const ui = await app(); ui.run("switchSource('git')");
  await ui.run('inspectRepo({preventDefault(){}})');
  ui.nodes['project-notes'].value = '数据集未入库'; ui.nodes['dataset-status'].value = 'not_provided';
  ui.nodes['weights-status'].value = 'unknown'; ui.nodes['excluded-paths'].value = 'cache/\nprivate/*.py\n';
  await ui.run('previewGit()');
  const body = ui.requests.filter(x => x.url === '/api/git/preview').at(-1).body;
  assert.deepEqual(body.project_context, {notes: '数据集未入库', dataset: 'not_provided', weights: 'unknown', excluded_paths: ['cache/', 'private/*.py']});
  ui.nodes['project-notes'].events.input();
  assert.equal(ui.run('state.gitPreview'), null);
  assert.equal(ui.nodes['start-real'].disabled, true);
});

test('restored task repopulates resource settings without changing their meaning', async () => {
  const context = {notes: '需自行准备权重', dataset: 'not_provided', weights: 'provided', excluded_paths: ['cache/']};
  const ui = await app({saved: new Map([['code-review-job', 'job-1']]), restoredContext: context});
  assert.equal(ui.nodes['project-notes'].value, context.notes);
  assert.equal(ui.nodes['dataset-status'].value, 'not_provided');
  assert.equal(ui.nodes['weights-status'].value, 'provided');
  assert.equal(ui.nodes['excluded-paths'].value, 'cache/');
  const result = {...done.result, project_context: context, resource_notes: [{path: 'data/missing.csv', version: 'after', reason: '用户声明未提供', blocked_repeats: 1}]};
  ui.streams.at(-1).listeners.resource({lastEventId: '8', data: JSON.stringify({path: 'data/missing.csv', message: '已跳过，不再读取'})});
  ui.streams.at(-1).listeners.done({lastEventId: '9', data: JSON.stringify({...done, result})});
  assert.match(ui.nodes.trace.textContent, /资源已跳过/);
  assert.match(ui.nodes.report.textContent, /用户声明已提供（未验证）/);
  assert.match(ui.nodes.report.textContent, /不可用资源记录/);
});
