'use strict';
const $ = id => document.getElementById(id);
// 页面统一状态：代码选择、固定预览、当前任务和 SSE 连接；密钥只保存在服务端。
const state = {examples: [], selected: null, file: null, view: 'diff', job: null, stream: null, busy: false, loading: false, source: 'example', repoInfo: null, gitPreview: null, gitSource: null, limits: {max_steps: 50, timeout_seconds: 300}, events: new Set(), result: null};
const labels = {idle: '待开始', running: '审查中', completed: '已完成', failed: '审查失败', limited: '未完成'};
// 模型返回和源码都按文本渲染，避免其中的 HTML 被网页执行。
function node(tag, cls, text) { const el = document.createElement(tag); if (cls) el.className = cls; if (text !== undefined) el.textContent = text; return el; }
function error(message = '') { $('error').textContent = message; $('error').hidden = !message; }
async function api(path, options) {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || '服务请求失败');
  return body;
}
function status(value) { $('status').textContent = labels[value] || value; $('status').className = 'status ' + value; }
function budgetText() { return '最多 ' + state.limits.max_steps + ' 轮 · 总时限 ' + (state.limits.timeout_seconds / 60) + ' 分钟'; }
const availabilityLabels = {provided: '用户声明已提供（未验证）', not_provided: '用户声明未提供', unknown: '未说明／不确定'};
function projectContext() {
  return {notes: $('project-notes').value, dataset: $('dataset-status').value || 'unknown', weights: $('weights-status').value || 'unknown',
    excluded_paths: $('excluded-paths').value.split(/\r?\n/).map(value => value.trim()).filter(Boolean)};
}
function restoreProjectContext(context = {}) {
  $('project-notes').value = context.notes || ''; $('dataset-status').value = context.dataset || 'unknown';
  $('weights-status').value = context.weights || 'unknown'; $('excluded-paths').value = (context.excluded_paths || []).join('\n');
}
function resourceSummary(context = {}, resources = []) {
  const box = node('details', 'resource-summary'); box.append(node('summary', '', '项目说明与验证范围'));
  box.append(node('p', '', '静态代码审查；未执行项目、训练或推理。'));
  box.append(node('p', '', '数据集：' + (availabilityLabels[context.dataset] || availabilityLabels.unknown) + '；未验证图片与样本。'));
  box.append(node('p', '', '模型权重：' + (availabilityLabels[context.weights] || availabilityLabels.unknown) + '；未验证加载与推理。'));
  if (context.notes) box.append(node('p', 'project-note-text', '用户说明：' + context.notes));
  if (context.excluded_paths?.length) box.append(node('p', '', '额外排除：' + context.excluded_paths.join('、')));
  if (resources.length) {
    const list = node('ul');
    for (const entry of resources) list.append(node('li', '', entry.path + ' [' + entry.version + ']：' + entry.reason + ' 重复拦截 ' + entry.blocked_repeats + ' 次。'));
    box.append(node('h3', '', '不可用资源记录'), list);
  } else box.append(node('p', '', '没有额外资源访问失败记录，不表示数据或权重已验证。'));
  return box;
}
function setBusy(busy) {
  state.busy = busy;
  syncControls();
}
function syncControls() {
  const locked = state.busy || state.loading;
  $('workspace').setAttribute('aria-busy', String(locked));
  $('start-real').textContent = state.busy ? '审查进行中…' : '开始真实审查';
  $('start-real').disabled = locked || !state.selected || (state.source === 'git' && !state.gitPreview);
  $('start-replay').disabled = locked || !state.selected; $('start-replay').hidden = state.source === 'git';
  for (const id of ['source-example', 'source-git', 'repo-path', 'inspect-repo', 'comparison-mode', 'base-ref', 'head-ref', 'preview-git', 'project-notes', 'dataset-status', 'weights-status', 'excluded-paths']) $(id).disabled = locked;
  document.querySelectorAll('.example-card').forEach(el => { el.disabled = locked; });
}
function renderScope(source) {
  const root = $('source-context'); root.replaceChildren(); root.hidden = !source;
  if (!source) return;
  const scope = source.after_scope;
  root.append(node('p', '', '固定版本：' + (source.base_commit?.slice(0, 12) || '空快照') + ' → ' + source.head_commit.slice(0, 12)));
  root.append(node('p', '', '加载 ' + scope.files.length + ' 个代码/说明文件 · ' + (scope.bytes / 1024).toFixed(1) + ' KB · ' + source.changed_files.length + ' 个变更文件'));
  // 固定版本和规模保持可见，较长的范围与资源声明按需展开。
  const scopeDetails = node('details', 'scope-details'); scopeDetails.append(node('summary', '', '审查范围与资源设置'));
  scopeDetails.append(node('p', '', '数据集、权重与未提交文件不纳入审查。'));
  if (source.review_plan) scopeDetails.append(node('p', '', '审查计划：' + source.review_plan.batches + ' 批代码变更；' + source.review_plan.context_only_files.length + ' 个变更文件仅作契约上下文。'));
  if (source.project_context) {
    const context = source.project_context;
    scopeDetails.append(node('p', '', '数据集：' + availabilityLabels[context.dataset] + ' · 权重：' + availabilityLabels[context.weights]));
    if (context.notes) scopeDetails.append(node('p', 'project-note-text', '用户说明：' + context.notes));
    if (context.excluded_paths.length) scopeDetails.append(node('p', '', '额外排除：' + context.excluded_paths.join('、')));
  }
  if (scope.excluded.length) {
    const details = node('details'); details.append(node('summary', '', '查看 ' + scope.excluded.length + ' 个已排除条目'));
    const list = node('ul');
    for (const entry of scope.excluded) list.append(node('li', '', entry.path + '：' + entry.reason));
    details.append(list); scopeDetails.append(details);
  }
  root.append(scopeDetails);
}
function applySelection(example, reset = true) {
  state.selected = example;
  // 首次打开仓库时优先展示实际变更代码，避免默认停留在README或空的__init__。
  state.file = example.files.find(f => f.changed && f.path.endsWith('.py') && !f.path.endsWith('__init__.py') && f.after.trim())?.path || example.files.find(f => f.changed)?.path || example.files[0]?.path;
  $('example-title').textContent = example.title; $('example-description').textContent = example.description;
  $('example-title').title = example.title;
  globalThis.reviewLayout?.selectSource(state.source);
  $('selection-kind').textContent = state.source === 'git' ? '本地仓库' : '内置示例';
  $('selection-meta').textContent = example.files.length + ' 个文件 / ' + example.files.filter(file => file.changed).length + ' 个变更文件';
  if (reset) resetReview();
  renderExamples(); renderFiles(); renderCode(); syncControls();
}
function clearSelection() {
  state.selected = null; state.file = null;
  globalThis.reviewLayout?.selectSource('git');
  $('repo-settings').open = true;
  $('example-title').textContent = '选择本地代码变更'; $('example-description').textContent = '读取仓库、选择版本并预览后，即可开始真实审查。';
  $('selection-kind').textContent = '本地仓库'; $('selection-meta').textContent = '尚未预览';
  $('files').replaceChildren(); $('file-count').textContent = '00'; $('current-file').textContent = '尚未预览';
  $('code').replaceChildren(node('p', 'code-placeholder', '代码预览将在这里显示')); $('change-stat').textContent = '—'; renderScope(null);
}
function switchSource(source) {
  state.source = source; resetReview();
  $('examples').hidden = source === 'git'; $('git-panel').hidden = source !== 'git';
  $('source-example').setAttribute('aria-pressed', String(source === 'example')); $('source-git').setAttribute('aria-pressed', String(source === 'git'));
  if (source === 'example') { renderScope(null); selectExample(state.examples[0].id); }
  else if (state.gitPreview) { applySelection(state.gitPreview.example); renderScope(state.gitPreview.source); }
  else clearSelection();
  syncControls();
}
function invalidatePreview() {
  // 提交或项目说明改变后，旧预览不能继续用于发起新任务。
  state.gitPreview = null; state.gitSource = null;
  if (state.source === 'git') { resetReview(); clearSelection(); }
  syncControls();
}
function showVersions(info) {
  state.repoInfo = info; $('repo-path').value = info.repository; $('repo-versions').hidden = false;
  for (const id of ['base-ref', 'head-ref']) {
    $(id).replaceChildren();
    for (const commit of info.commits) {
      const option = node('option', '', commit.id.slice(0, 7) + ' · ' + commit.subject); option.value = commit.id; $(id).append(option);
    }
  }
  $('comparison-mode').value = info.commits.length > 1 ? 'commits' : 'initial';
  if (info.commits.length > 1) $('base-ref').selectedIndex = 1;
  const initial = $('comparison-mode').value === 'initial'; $('base-label').hidden = $('base-ref').hidden = initial;
  $('repo-summary').textContent = info.name + ' · 已读取最近 ' + info.commits.length + ' 个提交' + (info.commits.length === 1 ? '，可按首次导入审查。' : '。');
}
async function inspectRepo(event) {
  event.preventDefault(); invalidatePreview(); state.loading = true; syncControls(); error();
  $('repo-summary').textContent = '正在读取提交记录…';
  $('repo-summary').classList.toggle('repo-error', false);
  try { showVersions(await api('/api/git/inspect', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({repository: $('repo-path').value.trim()})})); }
  catch (err) { state.repoInfo = null; $('repo-versions').hidden = true; $('repo-summary').textContent = '未能读取仓库。'; error(err.message); }
  finally { state.loading = false; syncControls(); }
}
async function previewGit() {
  if (!state.repoInfo) return;
  invalidatePreview(); state.loading = true; syncControls(); error(); $('repo-summary').textContent = '正在加载固定版本的代码…';
  $('repo-summary').classList.toggle('repo-error', false);
  try {
    const body = {repository: state.repoInfo.repository, head: $('head-ref').value, base: $('comparison-mode').value === 'initial' ? null : $('base-ref').value, project_context: projectContext()};
    const preview = await api('/api/git/preview', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    state.gitPreview = preview; state.gitSource = preview.source; applySelection(preview.example); renderScope(preview.source);
    // 预览后把侧栏还给文件列表；需要修改提交时可再次展开设置。
    $('repo-settings').open = false;
    $('repo-summary').textContent = '预览就绪：' + preview.source.changed_files.length + ' 个变更文件。可以开始真实审查。';
    $('mode-note').textContent = '本地 Git 真实审查 · 数据集已排除 · 按预览中的固定版本执行';
  } catch (err) {
    // 把具体原因放在触发操作旁，避免右侧错误区不在视野内时看起来没有响应。
    $('repo-summary').textContent = '预览失败：' + err.message;
    $('repo-summary').classList.toggle('repo-error', true);
    error(err.message);
  }
  finally { state.loading = false; syncControls(); }
}
function renderExamples() {
  $('examples').replaceChildren();
  state.examples.forEach(example => {
    const button = node('button', 'example-card' + (state.selected?.id === example.id ? ' active' : ''));
    button.type = 'button'; button.disabled = state.busy; button.setAttribute('aria-pressed', String(state.selected?.id === example.id));
    button.append(node('h3', '', example.title), node('span', 'example-tag', example.tag));
    button.addEventListener('click', () => selectExample(example.id));
    $('examples').append(button);
  });
}
function selectExample(id, reset = true) {
  applySelection(state.examples.find(e => e.id === id), reset);
}
function renderFiles() {
  $('files').replaceChildren(); $('file-count').textContent = String(state.selected.files.length).padStart(2, '0');
  for (const file of state.selected.files) {
    const button = node('button', 'file-button' + (file.path === state.file ? ' active' : ''));
    button.type = 'button'; button.setAttribute('aria-label', file.path + (file.changed ? ' 已修改' : ' 关联文件'));
    button.setAttribute('aria-pressed', String(file.path === state.file));
    button.append(node('span', 'file-icon', file.path.split('.').pop()), node('span', '', file.path));
    if (file.changed) button.append(node('span', 'file-modified', 'M'));
    button.addEventListener('click', () => { state.file = file.path; renderFiles(); renderCode(); });
    $('files').append(button);
  }
}
function highlightedCode(text) {
  const fragment = document.createDocumentFragment();
  const regex = /(#.*$|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\b(?:def|return|from|import|for|in|if|not|raise|class|None|True|False)\b|\b\d+(?:\.\d+)?\b)/g;
  let last = 0;
  for (const match of text.matchAll(regex)) {
    fragment.append(document.createTextNode(text.slice(last, match.index)));
    const token = match[0];
    fragment.append(node('span', token.startsWith('#') ? 'token-comment' : /^['"]/.test(token) ? 'token-string' : /^\d/.test(token) ? 'token-number' : 'token-keyword', token));
    last = match.index + token.length;
  }
  fragment.append(document.createTextNode(text.slice(last)));
  return fragment;
}
function renderCode(highlightLine) {
  // 差异视图使用服务端计算的新旧行号；证据定位跳到修改后的源码行。
  if (!state.selected || !state.file) return;
  const file = state.selected.files.find(f => f.path === state.file);
  $('current-file').textContent = file.path;
  $('diff-view').classList.toggle('active', state.view === 'diff'); $('diff-view').setAttribute('aria-pressed', String(state.view === 'diff'));
  $('source-view').classList.toggle('active', state.view === 'source'); $('source-view').setAttribute('aria-pressed', String(state.view === 'source'));
  $('code').replaceChildren();
  const rows = state.view === 'diff' ? file.rows : file.after.trimEnd().split('\n').map((text, i) => ({kind: 'context', old: null, new: i + 1, text}));
  for (const row of rows) {
    const el = node('div', 'code-row ' + row.kind + (row.new === highlightLine ? ' highlight' : ''));
    if (row.new) el.id = 'line-' + row.new;
    el.append(node('span', 'line-no', row.old ?? ''), node('span', 'line-no', row.new ?? ''), node('span', 'line-marker', row.kind === 'add' ? '+' : row.kind === 'delete' ? '−' : ''));
    const code = node('span', 'code-text'); code.append(highlightedCode(row.text)); el.append(code); $('code').append(el);
  }
  $('change-stat').textContent = file.changed ? '+' + file.rows.filter(r => r.kind === 'add').length + ' / −' + file.rows.filter(r => r.kind === 'delete').length + ' 行' : '关联文件 · 无变更';
  if (highlightLine) $('line-' + highlightLine)?.scrollIntoView({block: 'nearest', behavior: 'smooth'});
}
function locate(file, line) { state.file = file; state.view = 'source'; renderFiles(); renderCode(line); }
function tab(which) {
  for (const name of ['trace', 'report']) {
    $(name + '-tab').setAttribute('aria-selected', String(name === which));
    $(name + '-tab').setAttribute('tabindex', name === which ? '0' : '-1');
    $(name + '-panel').hidden = name !== which;
  }
}
function resetReview() {
  state.stream?.close(); state.stream = null; state.events.clear(); state.job = null; state.result = null;
  localStorage.removeItem('code-review-job');
  $('trace').replaceChildren(); $('trace-empty').hidden = false; $('trace-count').textContent = '0'; $('finding-count').textContent = '—';
  $('trace-empty').replaceChildren(node('h3', '', '尚未开始审查'), node('p', '', '选择一组变更后开始审查。代码查阅、工具返回和复核进度会记录在这里。'));
  const empty = node('div', 'empty-state'); empty.append(node('h3', '', '尚无审查报告'), node('p', '', '审查结束后，查看问题、代码证据和修复建议。'));
  $('report').replaceChildren(empty);
  $('download').hidden = true; $('run-metrics').textContent = budgetText();
  $('mode-note').textContent = '真实审查调用模型；离线回放使用固定脚本。'; $('mode-note').className = 'mode-note';
  error(); status('idle'); setBusy(false); tab('trace');
}
function trace(kind, data) {
  $('trace-empty').hidden = true;
  const item = node('li', 'trace-item ' + kind);
  const title = node('div', 'trace-item-title');
  const names = {start: '开始审查', plan: '审查计划', batch: '分批审查', budget: '使用量', verification: '候选复核', model: '模型决策', tool: data.tool, feedback: '校验反馈', validation: '证据与位置校验', resource: '资源已跳过'};
  title.append(node('span', '', names[kind] || kind));
  if (data.step) title.append(node('span', 'trace-step', '第 ' + data.step + ' 轮'));
  item.append(title);
  let description = data.message || '';
  if (kind === 'tool') { description = JSON.stringify(data.arguments) + (data.result?.status === 'unavailable' ? ' · 已跳过（不再读取）' : data.ok ? ' · 成功' : ' · 失败'); }
  item.append(node('p', '', description));
  if (kind === 'tool') {
    const details = node('details'); details.append(node('summary', '', '查看工具返回'));
    details.append(node('pre', '', JSON.stringify(data.ok ? data.result : {error: data.error, hint: data.hint}, null, 2)));
    item.append(details);
  }
  $('trace').append(item); $('trace-count').textContent = String($('trace').children.length);
  $('trace-panel').scrollTop = $('trace-panel').scrollHeight;
}
function renderReport(result) {
  const root = $('report'); root.replaceChildren();
  const supportingDetails = [];
  if (state.job?.mode === 'replay') root.append(node('div', 'report-warning', '离线脚本回放：结论预先编写，仅用于体验界面和控制流程，不代表模型审查能力。'));
  if (!result.complete) root.append(node('div', 'report-warning', '审查未完成。没有问题记录，不代表代码没有问题。'));
  if (state.job?.source?.kind === 'git') {
    const source = state.job.source;
    root.append(node('p', 'report-version', '审查版本：' + (source.base_commit?.slice(0, 12) || '空快照') + ' → ' + source.head_commit.slice(0, 12) + '。数据集已排除。'));
    supportingDetails.push(resourceSummary(result.project_context || source.project_context, result.resource_notes || []));
  }
  root.append(node('p', 'report-summary', result.summary));
  if (result.candidate_reviews) {
    const panel = node('section', 'resource-summary'); panel.append(node('h3', '', '候选问题复核'));
    const labels = {supported: '模型支持', refuted: '已有反证', uncertain: '待核实'};
    const counts = result.verification_summary;
    root.append(node('p', 'report-review-counts', '模型支持 ' + counts.supported + ' / 已有反证 ' + counts.refuted + ' / 待核实 ' + counts.uncertain));
    panel.append(node('p', '', '支持 ' + counts.supported + ' · 反驳 ' + counts.refuted + ' · 待核实 ' + counts.uncertain));
    panel.append(node('p', '', '模型支持仍需开发者验收；未确定的候选不会作为无问题结论。'));
    for (const review of result.candidate_reviews) {
      const details = node('details'); details.setAttribute('data-verdict', review.verdict); details.append(node('summary', '', labels[review.verdict] + '：' + review.candidate.title));
      details.append(node('p', '', review.rationale), node('p', '', '限制：' + review.limitations.join('；')));
      const assessments = {supported: '支持', contradicted: '被反驳', unknown: '未知'};
      details.append(node('p', '', [['故障机制', 'mechanism_assessment'], ['变更引入性', 'regression_assessment'], ['具体影响', 'impact_assessment']]
        .map(([label, key]) => label + '：' + assessments[review[key] || 'unknown']).join('；')));
      for (const [label, refs] of [['支持证据', review.supporting_evidence], ['反证', review.counterevidence]]) {
        for (const ref of refs) {
          const text = label + ' · ' + (ref.version === 'before' ? '修改前 ' : '修改后 ') + ref.file + ':' + ref.line;
          if (ref.version === 'after') {
            const location = node('button', 'location', text); location.addEventListener('click', () => locate(ref.file, ref.line)); details.append(location);
          } else details.append(node('p', '', text));
          details.append(node('pre', 'evidence', ref.evidence));
        }
      }
      panel.append(details);
    }
    supportingDetails.push(panel);
  }
  if (result.coverage) {
    const coverage = result.coverage;
    root.append(node('p', 'report-version', '批次完成 ' + coverage.completed_batches + '/' + coverage.batches + '；代码文件交付 ' + coverage.completed_files.length + '/' + coverage.code_files.length));
    root.append(node('p', '', '累计输入 ' + (result.usage?.prompt_tokens ?? 0).toLocaleString() + ' token；输出 ' + (result.usage?.completion_tokens ?? 0).toLocaleString() + ' token。'));
    if (!result.usage_complete) root.append(node('p', '', '部分请求用量未知，以上数字不能视为完整消耗。'));
    if (coverage.pending_files.length) root.append(node('p', 'report-warning', '未完成：' + coverage.pending_files.join('、')));
    if (coverage.context_only_files.length) root.append(node('p', '', '仅作上下文：' + coverage.context_only_files.join('、')));
  }
  if (result.complete && result.findings.length === 0) root.append(node('div', 'no-findings', '未发现可确认的问题。此结论仅覆盖本次静态审查范围。'));
  for (const finding of result.findings) {
    const article = node('article', 'finding'); const title = node('div', 'finding-title');
    const severityNames = {high: '高风险', medium: '中风险', low: '低风险'};
    title.append(node('span', 'severity ' + finding.severity, severityNames[finding.severity]), node('h3', '', finding.title)); article.append(title);
    const link = node('button', 'location', finding.file + ':' + finding.line + ' ↗'); link.type = 'button'; link.addEventListener('click', () => locate(finding.file, finding.line)); article.append(link);
    for (const [label, value] of [['触发条件', finding.trigger], ['问题影响', finding.explanation]]) { const p = node('p'); p.append(node('strong', '', label + '：'), document.createTextNode(value)); article.append(p); }
    article.append(node('pre', 'evidence', finding.evidence));
    for (const ref of finding.references) {
      const refButton = node('button', 'location', '关联 ' + ref.file + ':' + ref.line + ' ↗'); refButton.type = 'button'; refButton.addEventListener('click', () => locate(ref.file, ref.line)); article.append(refButton, node('pre', 'evidence', ref.evidence));
    }
    const suggestion = node('p'); suggestion.append(node('strong', '', '修复建议：'), document.createTextNode(finding.suggestion)); article.append(suggestion, node('p', 'validation-note', finding.validation)); root.append(article);
  }
  // 问题与原文优先，完整的候选复核和资源范围仍保留在报告下方。
  root.append(...supportingDetails);
}
function finish(job) {
  if (!job.result) return;
  state.stream?.close(); state.stream = null; state.job = job; state.result = job.result;
  setBusy(false); status(job.status); renderReport(job.result); $('finding-count').textContent = String(job.result.findings.length);
  $('run-metrics').textContent = job.result.steps + ' 轮 / ' + job.result.elapsed_seconds + ' 秒';
  $('download').href = '/api/reviews/' + job.id + '/report'; $('download').hidden = false;
  tab('report');
}
function connect(job) {
  // 同一任务的进度由服务端推送；事件 ID 防止重连后重复显示记录。
  state.job = job; const stream = new EventSource('/api/reviews/' + job.id + '/events'); state.stream = stream;
  for (const kind of ['start', 'plan', 'batch', 'budget', 'verification', 'model', 'tool', 'feedback', 'validation', 'resource', 'done']) {
    stream.addEventListener(kind, event => {
      if (state.events.has(event.lastEventId)) return;
      state.events.add(event.lastEventId); const data = JSON.parse(event.data);
      if (kind === 'done') { error(); finish(data); } else trace(kind, data);
    });
  }
  stream.onerror = async () => {
    if (state.stream !== stream) return;
    try { const current = await api('/api/reviews/' + job.id); if (current.result) { error(); finish(current); } else error('实时连接暂时中断，正在尝试重连；审查任务仍在运行。'); }
    catch (err) { stream.close(); state.stream = null; setBusy(false); status('failed'); error(err.message + '。执行记录可能不完整，请重新发起审查。'); }
  };
}
async function start(mode) {
  if (!state.selected || (state.source === 'git' && !state.gitPreview)) return;
  resetReview(); setBusy(true); status('running');
  $('trace-empty').replaceChildren(node('h3', '', mode === 'replay' ? '正在启动离线回放' : '正在启动审查'), node('p', '', '等待服务创建任务，执行记录随后显示。'));
  $('mode-note').textContent = mode === 'replay' ? '离线脚本回放 · 固定动作与答案，不代表模型能力' : '真实模型审查 · 自主选择工具，不会自动切换为回放';
  $('mode-note').classList.toggle('replay-active', mode === 'replay');
  try {
    const gitMode = state.source === 'git';
    const job = await api(gitMode ? '/api/git/reviews' : '/api/reviews', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(gitMode ? {preview_id: state.gitPreview.preview_id} : {example_id: state.selected.id, mode})});
    if (gitMode) $('mode-note').textContent = '本地 Git 真实审查 · 数据集已排除 · 固定提交 ' + job.source.head_commit.slice(0, 12);
    localStorage.setItem('code-review-job', job.id); connect(job);
  } catch (err) {
    setBusy(false); status('failed'); error(err.message);
    $('trace-empty').replaceChildren(node('h3', '', '未能开始审查'), node('p', '', err.message + '。处理后可以重新发起。'));
  }
}
async function init() {
  // 刷新时读取服务配置，再根据任务编号恢复原快照与执行轨迹。
  try {
    const [examples, config] = await Promise.all([api('/api/examples'), api('/api/config')]);
    state.examples = examples;
    state.limits = {max_steps: config.max_steps ?? 50, timeout_seconds: config.timeout_seconds ?? 300};
    $('run-metrics').textContent = budgetText();
    $('repo-path').value = config.default_repository || '';
    const savedId = localStorage.getItem('code-review-job');
    selectExample(examples[0].id, false);
    $('model-status').textContent = config.configured ? '真实模型密钥已配置' : '未配置模型密钥 · 可先体验回放';
    $('model-dot').classList.toggle('ready', config.configured);
    if (savedId) {
      try {
        const job = await api('/api/reviews/' + encodeURIComponent(savedId));
        if (job.source?.kind === 'git') {
          switchSource('git');
          const input = await api('/api/reviews/' + encodeURIComponent(job.id) + '/input');
          const source = input.source;
          restoreProjectContext(source.project_context);
          const ids = [...new Set([source.head_commit, source.base_commit].filter(Boolean))];
          showVersions({repository: source.repository, name: '已恢复本地任务', commits: ids.map(id => ({id, subject: '任务固定版本'}))});
          $('comparison-mode').value = source.base_commit ? 'commits' : 'initial'; $('base-label').hidden = $('base-ref').hidden = !source.base_commit;
          if (source.base_commit) $('base-ref').value = source.base_commit;
          state.gitSource = source; applySelection(input.example, false); renderScope(source);
          $('repo-summary').textContent = '已恢复任务快照；再次审查请重新预览。';
        } else selectExample(job.example_id, false);
        setBusy(true); status('running');
        $('mode-note').textContent = job.mode === 'replay' ? '离线脚本回放 · 固定动作与答案，不代表模型能力' : '真实模型审查 · 自主选择工具';
        $('mode-note').classList.toggle('replay-active', job.mode === 'replay'); localStorage.setItem('code-review-job', job.id); connect(job);
      } catch (_) { localStorage.removeItem('code-review-job'); }
    }
  } catch (err) { error('无法连接本地服务：' + err.message); $('start-real').disabled = $('start-replay').disabled = true; }
}
$('diff-view').addEventListener('click', () => { if (state.selected) { state.view = 'diff'; renderCode(); } });
$('source-view').addEventListener('click', () => { if (state.selected) { state.view = 'source'; renderCode(); } });
$('trace-tab').addEventListener('click', () => tab('trace')); $('report-tab').addEventListener('click', () => tab('report'));
for (const [index, name] of ['trace', 'report'].entries()) {
  $(name + '-tab').addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 'trace' : event.key === 'End' ? 'report' : ['trace', 'report'][1 - index];
    tab(next); $(next + '-tab').focus();
  });
}
$('start-real').addEventListener('click', () => start('real')); $('start-replay').addEventListener('click', () => start('replay'));
$('source-example').addEventListener('click', () => switchSource('example')); $('source-git').addEventListener('click', () => switchSource('git'));
$('repo-form').addEventListener('submit', inspectRepo); $('preview-git').addEventListener('click', previewGit);
$('repo-path').addEventListener('input', () => { state.repoInfo = null; $('repo-versions').hidden = true; invalidatePreview(); });
for (const id of ['head-ref', 'base-ref', 'comparison-mode']) $(id).addEventListener('change', () => {
  invalidatePreview(); const initial = $('comparison-mode').value === 'initial'; $('base-label').hidden = $('base-ref').hidden = initial;
  $('repo-summary').textContent = '版本已变更，请重新预览。';
});
for (const id of ['project-notes', 'excluded-paths', 'dataset-status', 'weights-status']) $(id).addEventListener(id.endsWith('-status') ? 'change' : 'input', () => {
  invalidatePreview(); $('repo-summary').textContent = '项目说明或资源设置已改变，请重新预览。';
});
// 布局偏好与审查状态分开：只存尺寸和可见性，不保存源码或任务内容。
if (typeof window !== 'undefined') globalThis.reviewLayout = (() => {
  const workspace = $('workspace'), storageKey = 'code-review-layout-v1';
  const defaults = () => ({sidebar:true, review:true, left:null, right:null, height:null, context:{example:true, git:false}});
  let prefs = defaults(), source = 'example', focused = false, drag = null;
  const clamp = (value, low, high) => Math.max(low, Math.min(Math.max(low, high), value));
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey));
    if (saved && typeof saved === 'object') {
      for (const key of ['sidebar','review']) if (typeof saved[key] === 'boolean') prefs[key] = saved[key];
      for (const key of ['left','right','height']) if (Number.isFinite(saved[key])) prefs[key] = saved[key];
      for (const key of ['example','git']) if (typeof saved.context?.[key] === 'boolean') prefs.context[key] = saved.context[key];
    }
  } catch (_) { /* 损坏或不可用的本机存储不影响阅读与审查。 */ }
  const save = () => { try { localStorage.setItem(storageKey, JSON.stringify(prefs)); } catch (_) {} };
  const desktop = () => innerWidth > 960;
  function dimensions() {
    const css = getComputedStyle(workspace), available = workspace.clientWidth;
    const left = clamp(prefs.left ?? parseFloat(css.getPropertyValue('--sidebar-default')),180,Math.min(420,available-556));
    const right = clamp(prefs.right ?? parseFloat(css.getPropertyValue('--review-default')),260,Math.min(720,available-(prefs.sidebar ? left+8 : 0)-288));
    return {left,right};
  }
  function limits(kind) {
    const widths = dimensions(), available = workspace.clientWidth;
    if (kind === 'left') return [180,Math.max(180,Math.min(420,available-(prefs.review ? widths.right+8 : 0)-288))];
    if (kind === 'right') return [260,Math.max(260,Math.min(720,available-(prefs.sidebar ? widths.left+8 : 0)-288))];
    return [440,Math.max(1600,innerHeight*2)];
  }
  function apply() {
    const widths = dimensions();
    workspace.style.setProperty('--sidebar-width',widths.left+'px');
    workspace.style.setProperty('--review-width',widths.right+'px');
    if (prefs.height !== null) workspace.style.setProperty('--workspace-height',clamp(prefs.height,...limits('height'))+'px');
    else workspace.style.removeProperty('--workspace-height');
    workspace.classList.toggle('sidebar-hidden',focused || !prefs.sidebar);
    workspace.classList.toggle('review-hidden',focused || !prefs.review);
    workspace.classList.toggle('code-focus',focused);
    document.body.classList.toggle('code-focused',focused);
    for (const [name,visible] of [['sidebar',prefs.sidebar],['review',prefs.review]]) {
      $('toggle-'+name).setAttribute('aria-expanded',String(visible && !focused));
      $('toggle-'+name).disabled = focused;
    }
    $('toggle-focus').textContent = focused ? '退出专注' : '专注代码';
    $('toggle-focus').setAttribute('aria-pressed',String(focused));
    const expanded = !focused && prefs.context[source];
    $('editor-context').hidden = !expanded;
    $('toggle-context').textContent = expanded ? '收起说明' : '展开说明';
    $('toggle-context').setAttribute('aria-expanded',String(expanded));
    $('toggle-context').disabled = focused;
    for (const [id,kind,value] of [['sidebar-resizer','left',widths.left],['review-resizer','right',widths.right],['height-resizer','height',workspace.getBoundingClientRect().height]]) {
      const handle=$(id), range=limits(kind);
      handle.setAttribute('aria-valuemin',String(range[0])); handle.setAttribute('aria-valuemax',String(range[1]));
      handle.setAttribute('aria-valuenow',String(Math.round(value))); handle.setAttribute('aria-valuetext',Math.round(value)+' 像素');
    }
  }
  const setValue = (kind,value) => { prefs[kind] = clamp(value,...limits(kind)); apply(); };
  for (const name of ['sidebar','review']) $('toggle-'+name).addEventListener('click',()=>{prefs[name]=!prefs[name];apply();save();});
  $('toggle-context').addEventListener('click',()=>{prefs.context[source]=!prefs.context[source];apply();save();});
  $('toggle-focus').addEventListener('click',()=>{focused=!focused;apply();});
  $('reset-layout').addEventListener('click',()=>{prefs=defaults();focused=false;apply();save();});
  window.addEventListener('keydown',event=>{if(event.key==='Escape' && focused){focused=false;apply();$('toggle-focus').focus();}});
  window.addEventListener('resize',()=>{apply();});
  // 指针捕获让拖动越过分隔线后仍能继续；宽度和高度都受最小阅读范围保护。
  for (const [id,kind] of [['sidebar-resizer','left'],['review-resizer','right'],['height-resizer','height']]) {
    const handle=$(id), vertical=kind==='height';
    handle.addEventListener('pointerdown',event=>{
      if(event.button!==0 || !desktop() || (focused && !vertical)) return;
      event.preventDefault();
      drag={id,kind,pointer:event.pointerId,start:vertical?event.clientY:event.clientX,
        value:vertical?workspace.getBoundingClientRect().height:dimensions()[kind]};
      handle.setPointerCapture(event.pointerId); document.body.classList.add(vertical?'resizing-height':'resizing-width');
    });
    handle.addEventListener('pointermove',event=>{
      if(!drag || drag.id!==id || drag.pointer!==event.pointerId) return;
      const delta=(vertical?event.clientY:event.clientX)-drag.start;
      setValue(kind,drag.value+delta*(kind==='right'?-1:1));
    });
    const stop = () => {if(!drag || drag.id!==id) return;drag=null;document.body.classList.remove('resizing-height','resizing-width');save();};
    handle.addEventListener('pointerup',stop); handle.addEventListener('pointercancel',stop); handle.addEventListener('lostpointercapture',stop);
    handle.addEventListener('dblclick',()=>{prefs[kind]=null;apply();save();});
    handle.addEventListener('keydown',event=>{
      const accepted=vertical?['ArrowUp','ArrowDown','Home','End']:['ArrowLeft','ArrowRight','Home','End'];
      if(!desktop() || !accepted.includes(event.key)) return;
      event.preventDefault();
      const range=limits(kind), current=vertical?workspace.getBoundingClientRect().height:dimensions()[kind];
      const sign=['ArrowLeft','ArrowUp'].includes(event.key)?-1:1;
      const value=event.key==='Home'?range[0]:event.key==='End'?range[1]:current+sign*(kind==='right'?-1:1)*(event.shiftKey?40:16);
      setValue(kind,value);save();
    });
  }
  apply();
  return {selectSource(value){source=value==='git'?'git':'example';apply();}};
})();
init();
