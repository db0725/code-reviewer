/* 本地界面验证：使用已安装 Chrome 的隔离无头实例，不安装依赖或调用模型。
   Node 20: node --experimental-websocket tests/ui_visual_check.cjs --baseline
   默认验证改版后页面；截图和观测统一写入 tests/results。 */
'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const {spawn} = require('node:child_process');
const assert = require('node:assert/strict');
const output = path.resolve(__dirname, process.env.UI_TEST_OUTPUT || 'results/ui-reading-layout-20261111');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const baseline = process.argv.includes('--baseline');

async function until(check, description, timeout = 12000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { const result = await check(); if (result) return result; await pause(100); }
  throw new Error('Timed out: ' + description);
}

async function main() {
  await fs.mkdir(output, {recursive: true});
  const profile = await fs.mkdtemp(path.join(output, 'chrome-'));
  const browserPath = process.env.UI_TEST_BROWSER || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
  const chrome = spawn(browserPath, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1', '--user-data-dir=' + profile, 'about:blank'],
  {windowsHide: true, stdio: 'ignore'});
  let socket;
  try {
    let launchError;
    chrome.on('error', error => { launchError = error; });
    const port = await until(async () => {
      if (launchError) throw launchError;
      try { return Number((await fs.readFile(path.join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]); }
      catch { return null; }
    }, 'isolated Chrome startup');
    const targets = await (await fetch('http://127.0.0.1:' + port + '/json/list')).json();
    socket = new WebSocket(targets.find(target => target.type === 'page').webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, {once: true}); socket.addEventListener('error', reject, {once: true}); });
    let serial = 0;
    const pending = new Map();
    socket.addEventListener('message', event => {
      const message = JSON.parse(event.data), row = pending.get(message.id);
      if (!row) return;
      pending.delete(message.id); clearTimeout(row.timer);
      message.error ? row.reject(new Error(JSON.stringify(message.error))) : row.resolve(message.result);
    });
    const call = (method, params = {}) => new Promise((resolve, reject) => {
      const id = ++serial;
      const timer = setTimeout(() => { pending.delete(id); reject(new Error('CDP timeout: ' + method)); }, 15000);
      pending.set(id, {resolve, reject, timer}); socket.send(JSON.stringify({id, method, params}));
    });
    const evaluate = async expression => {
      const result = await call('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.text + ': ' + result.exceptionDetails.exception?.description);
      return result.result.value;
    };
    const viewports = [[1440, 900], [1024, 768], [390, 844]];
    const observations = [];
    const screenshot = async name => {
      const image = await call('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false});
      await fs.writeFile(path.join(output, name + '.png'), Buffer.from(image.data, 'base64'));
    };
    await call('Page.enable');
    for (const [width, height] of viewports) {
      await call('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor: 1, mobile: false});
      await call('Page.navigate', {url: 'http://127.0.0.1:8765/'});
      await until(() => evaluate("typeof state !== 'undefined' && state.examples.length > 0 && !!state.selected"), 'initial example');
      await evaluate('document.fonts.ready');
      await screenshot((baseline ? 'before-' : 'after-') + width);
      const observation = await evaluate(`(() => {
        const bounds = selector => {const r = document.querySelector(selector).getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height};};
        return {viewport: '${width}x${height}', overflow: document.documentElement.scrollWidth > innerWidth,
          workspace: bounds('.workspace'), code:bounds('#code-scroll'), review:bounds('.review-panel'),
          codeFont: getComputedStyle(document.getElementById('code')).fontSize};
      })()`);
      observations.push(observation);
      if (!baseline) assert.equal(observation.overflow, false, 'No page overflow at ' + width);
    }
    await fs.writeFile(path.join(output, baseline ? 'before.json' : 'layout.json'), JSON.stringify(observations, null, 2));
    if (baseline) { console.log(JSON.stringify(observations)); return; }
    const measureAccessibility = async () => evaluate(`(() => {
      const visible = el => el.getClientRects().length > 0;
      const controls = [...document.querySelectorAll('button,input,select,textarea,a')].filter(visible);
      const unnamed = controls.filter(el => !(el.getAttribute('aria-label') || el.textContent.trim() ||
        document.querySelector('label[for="' + el.id + '"]')?.textContent.trim())).map(el => el.id || el.tagName);
      const rgb = color => color.match(/[\\d.]+/g).slice(0,3).map(Number);
      const light = values => values.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4})
        .reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
      const failures = [], ratios = [];
      for (const el of document.querySelectorAll('h1,h2,h3,p,button,label,summary,.line-no,.code-text,.token-keyword,.token-string,.token-comment,.token-number,.example-tag')) {
        if (!visible(el) || el.disabled || !el.textContent.trim()) continue;
        const style = getComputedStyle(el);
        let ancestor = el, background = 'rgb(255,255,255)';
        while (ancestor) { const c=getComputedStyle(ancestor).backgroundColor; if(c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent') {background=c;break;} ancestor=ancestor.parentElement; }
        const a=light(rgb(style.color)), b=light(rgb(background));
        const ratio=(Math.max(a,b)+.05)/(Math.min(a,b)+.05);
        ratios.push(ratio);
        if(ratio<4.5 && parseFloat(style.fontSize)<24) failures.push({text:el.textContent.slice(0,35),ratio:+ratio.toFixed(2),color:style.color,background});
      }
      return {unnamed,contrastFailures:failures,minimumTextContrast:+Math.min(...ratios).toFixed(2),
        primaryHitArea:document.getElementById('start-real').getBoundingClientRect().height};
    })()`);
    const accessibility = await measureAccessibility();
    assert.deepEqual(accessibility.unnamed, []);
    assert.deepEqual(accessibility.contrastFailures, []);
    // 下面是实际服务回放与受控显示状态，不请求真实模型。
    await call('Emulation.setDeviceMetricsOverride', {width:1440,height:900,deviceScaleFactor:1,mobile:false});
    await call('Page.navigate', {url:'http://127.0.0.1:8765/'});
    await until(() => evaluate("typeof state !== 'undefined' && !!state.selected"), 'ready for interactions');
    await evaluate("document.getElementById('start-replay').click()");
    await until(() => evaluate('state.busy && document.getElementById("status").textContent === "审查中"'), 'replay loading');
    await screenshot('loading');
    await until(() => evaluate('!!state.result && !state.busy'), 'replay completion', 20000);
    assert.equal(await evaluate('state.job.mode'), 'replay');
    assert.ok(await evaluate('state.result.findings.length > 0'));
    await screenshot('success');
    // 手机端实际报告页，以及恢复后的代码位置。
    await call('Emulation.setDeviceMetricsOverride', {width:390,height:844,deviceScaleFactor:1,mobile:false});
    await evaluate("document.getElementById('review-panel').scrollIntoView({block:'start'})");
    await screenshot('mobile-report');
    assert.equal(await evaluate('document.documentElement.scrollWidth > innerWidth'), false);
    await call('Emulation.setDeviceMetricsOverride', {width:1440,height:900,deviceScaleFactor:1,mobile:false});
    await evaluate('window.scrollTo(0,0)');
    await evaluate("document.querySelector('.finding .location').click()");
    assert.ok(await evaluate("!!document.querySelector('.code-row.highlight')"));
    assert.equal(await evaluate('state.view'), 'source');
    const reportURL = await evaluate("document.getElementById('download').href");
    const download = await fetch(reportURL);
    assert.equal(download.status, 200);
    await fs.writeFile(path.join(output, 'replay-report.md'), await download.text());
    // 从真实回放结果构造不完整状态，检查它不会变成“未发现问题”。
    await evaluate(`finish({...state.job,status:'limited',result:{...state.result,complete:false,findings:[],summary:'受控界面检查：一个批次未完成，候选待核实。',
      candidate_reviews:[{candidate:{title:'动态调用条件待核实'},verdict:'uncertain',status:'not_run',rationale:'受控界面检查：证据不足',limitations:['没有运行仓库代码'],supporting_evidence:[],counterevidence:[]}],
      verification_summary:{supported:0,refuted:0,uncertain:1},coverage:{completed_batches:1,batches:2,completed_files:['checkout.py'],code_files:['checkout.py','other.py'],pending_files:['other.py'],context_only_files:[]}}})`);
    assert.ok(await evaluate("document.getElementById('report').textContent.includes('审查未完成') && !document.getElementById('report').textContent.includes('未发现可确认的问题')"));
    await screenshot('partial');
    // 页面配置边界模拟：仅拦截测试页的创建请求，不发送给真实模型。
    await evaluate(`selectExample(state.examples[0].id); document.getElementById('model-status').textContent='未配置模型密钥 · 可先体验回放';
      document.getElementById('model-dot').classList.remove('ready'); globalThis.realFetch = fetch; globalThis.fetch = (url, options) =>
      url === '/api/reviews' ? Promise.resolve({ok:false,json:async()=>({error:'未找到模型密钥，请配置本机 .env 后重启服务。'})}) : realFetch(url,options);`);
    await evaluate("document.getElementById('start-real').click()");
    await until(() => evaluate("document.getElementById('error').textContent.includes('未找到模型密钥')"), 'configuration boundary');
    await screenshot('permission');
    await evaluate("globalThis.fetch = realFetch; document.getElementById('model-status').textContent='真实模型密钥已配置'; document.getElementById('model-dot').classList.add('ready')");
    // 错误状态走真实的本地无效路径校验，不修改仓库。
    await evaluate("switchSource('git'); document.getElementById('repo-path').value='E:/__code_review_missing_repository__'; document.getElementById('repo-form').requestSubmit()");
    await until(() => evaluate("!state.loading && !document.getElementById('error').hidden"), 'invalid repository feedback');
    await screenshot('error');
    await evaluate("switchSource('example'); selectExample(state.examples[2].id)");
    assert.ok(await evaluate("document.getElementById('trace-empty').textContent.includes('尚未开始审查')"));
    await screenshot('empty');
    await evaluate("document.getElementById('start-replay').click()");
    await until(() => evaluate('!!state.result && !state.busy'), 'normal replay completion', 20000);
    assert.equal(await evaluate('state.result.findings.length'), 0);
    assert.ok(await evaluate("!!document.querySelector('.no-findings')"));
    await screenshot('completed-no-findings');
    // 真实本地 Git 预览，不开始模型审查。资料和配置固定于预览编号。
    await evaluate("switchSource('git'); document.getElementById('repo-path').value='E:/des/folder/项目/DKNet'; document.getElementById('repo-form').requestSubmit()");
    await until(() => evaluate('!state.loading && !!state.repoInfo'), 'actual repository commits');
    await evaluate("document.getElementById('preview-git').click()");
    await until(() => evaluate('!state.loading && !!state.gitPreview'), 'actual Git preview', 20000);
    await screenshot('git-preview');
    const readingLayout = typeof await evaluate('globalThis.reviewLayout') !== 'undefined';
    assert.ok(readingLayout,'Reading layout controller loaded');
    let layoutChecks = null;
    if (readingLayout) {
      const measure = () => evaluate(`(() => {
        const size=id=>{const r=document.getElementById(id).getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};};
        return {left:size('sidebar'),right:size('review-panel'),code:size('code-scroll'),workspace:size('workspace'),
          contextHidden:document.getElementById('editor-context').hidden,focus:document.getElementById('toggle-focus').getAttribute('aria-pressed')};
      })()`);
      const compact = await measure();
      assert.equal(compact.contextHidden,true,'Git metadata collapsed by default');
      await evaluate("document.getElementById('toggle-context').click()");
      const expanded = await measure();
      assert.ok(compact.code.height > expanded.code.height + 70,'Collapse releases code height');
      await screenshot('git-expanded');
      await evaluate("document.getElementById('toggle-context').click()");
      const dragHandle = async (id,dx,dy) => {
        // 视口改变会触发响应式布局和resize回调，等待渲染稳定再定位拖动柄。
        await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(true))))');
        const box=await evaluate(`(() => {const r=document.getElementById('${id}').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`);
        await call('Input.dispatchMouseEvent',{type:'mouseMoved',x:box.x,y:box.y});
        await call('Input.dispatchMouseEvent',{type:'mousePressed',x:box.x,y:box.y,button:'left',clickCount:1});
        assert.ok(await evaluate(`document.body.classList.contains('${id==='height-resizer'?'resizing-height':'resizing-width'}')`),'Pointer captured by '+id);
        for(let step=1;step<=4;step++) await call('Input.dispatchMouseEvent',{type:'mouseMoved',x:box.x+dx*step/4,y:box.y+dy*step/4,button:'left',buttons:1});
        await call('Input.dispatchMouseEvent',{type:'mouseReleased',x:box.x+dx,y:box.y+dy,button:'left',clickCount:1});
      };
      await dragHandle('sidebar-resizer',65,0);
      const widerLeft = await measure();
      assert.ok(widerLeft.left.width > compact.left.width + 40);
      await dragHandle('review-resizer',80,0);
      const narrowerRight = await measure();
      assert.ok(narrowerRight.right.width < compact.right.width - 50);
      await dragHandle('height-resizer',0,70);
      const taller = await measure();
      assert.ok(taller.workspace.height > compact.workspace.height + 40);
      await screenshot('git-resized');
      await evaluate("document.getElementById('toggle-sidebar').click()");
      assert.equal((await measure()).left.width,0);
      await evaluate("document.getElementById('toggle-sidebar').click(); document.getElementById('toggle-review').click()");
      assert.equal((await measure()).right.width,0);
      await evaluate("document.getElementById('toggle-review').click(); document.getElementById('reset-layout').click()");
      const normal = await measure();
      await evaluate("document.getElementById('toggle-focus').click()");
      const focused = await measure();
      assert.equal(focused.left.width,0);assert.equal(focused.right.width,0);
      assert.ok(focused.code.width > normal.code.width + 500);
      await screenshot('git-focus');
      await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
      await call('Input.dispatchKeyEvent',{type:'keyUp',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
      const restored=await measure();
      assert.equal(restored.left.width,normal.left.width);assert.equal(restored.right.width,normal.right.width);
      // 强行拖向边缘也保留代码最小宽度。
      await dragHandle('review-resizer',-900,0);
      assert.ok((await measure()).code.width >= 279);
      await evaluate("document.getElementById('reset-layout').click()");
      await call('Emulation.setDeviceMetricsOverride',{width:1024,height:768,deviceScaleFactor:1,mobile:false});
      await dragHandle('review-resizer',-800,0);
      const constrained=await measure();
      assert.ok(constrained.code.width >= 279 && constrained.code.width <= 282);
      assert.ok(constrained.right.width > 400);
      assert.equal(await evaluate('document.documentElement.scrollWidth > innerWidth'),false);
      await screenshot('laptop-resized');
      await evaluate("document.getElementById('reset-layout').click()");
      await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:false});
      await evaluate("document.getElementById('toggle-focus').click(); window.scrollTo(0,0)");
      assert.equal((await measure()).left.width,0);assert.equal((await measure()).right.width,0);
      assert.equal(await evaluate('document.documentElement.scrollWidth > innerWidth'),false);
      await screenshot('mobile-focus');
      await evaluate("document.getElementById('toggle-focus').click()");
      await call('Emulation.setDeviceMetricsOverride',{width:1440,height:900,deviceScaleFactor:1,mobile:false});
      layoutChecks={compact,expanded,taller,focused,pointerResize:true,panelToggles:true,escapeRestore:true,minimumCodeWidth:true};
    }
    assert.equal(await evaluate("document.getElementById('start-real').disabled"), false);
    assert.ok(await evaluate("document.getElementById('trace-empty').textContent.includes('尚未开始审查')"));
    await evaluate("document.getElementById('repo-settings').open=true; if(document.getElementById('editor-context')?.hidden) document.getElementById('toggle-context').click(); document.querySelector('.scope-details').open=true; document.querySelector('.project-options').open=true");
    await screenshot('git-scope');
    const formAccessibility = await measureAccessibility();
    assert.deepEqual(formAccessibility.unnamed, []);
    assert.deepEqual(formAccessibility.contrastFailures, []);
    await call('Emulation.setDeviceMetricsOverride', {width:390,height:844,deviceScaleFactor:1,mobile:false});
    await evaluate('window.scrollTo(0,0)');
    await screenshot('mobile-git');
    assert.equal(await evaluate('document.documentElement.scrollWidth > innerWidth'), false);
    // 受控长文件名与长中文说明：只改变测试页的内存内容，不写仓库。
    await evaluate(`switchSource('example'); applySelection({...state.selected,
      title:'受控长内容检查：跨文件接口变更与异常处理的完整审查',
      description:'用于检查中文说明换行和阅读宽度。'.repeat(5),
      files:state.selected.files.map((file,index)=>index===0?{...file,path:'src/'+('deeply_nested_module_'.repeat(7))+'/checkout.py'}:file)})`);
    await screenshot('mobile-long-content');
    assert.equal(await evaluate('document.documentElement.scrollWidth > innerWidth'), false);
    await call('Emulation.setDeviceMetricsOverride', {width:1440,height:900,deviceScaleFactor:1,mobile:false});
    await evaluate("selectExample(state.examples[0].id); window.scrollTo(0,0)");
    // 实际 CDP 键盘事件，检查标签页方向键和可见焦点。
    await evaluate("document.getElementById('trace-tab').focus()");
    await call('Input.dispatchKeyEvent', {type:'keyDown',key:'ArrowRight',code:'ArrowRight',windowsVirtualKeyCode:39});
    await call('Input.dispatchKeyEvent', {type:'keyUp',key:'ArrowRight',code:'ArrowRight',windowsVirtualKeyCode:39});
    assert.equal(await evaluate("document.activeElement.id"), 'report-tab');
    assert.equal(await evaluate("document.getElementById('report-tab').getAttribute('aria-selected')"), 'true');
    const focus = await evaluate("({width:getComputedStyle(document.activeElement).outlineWidth,style:getComputedStyle(document.activeElement).outlineStyle})");
    assert.notEqual(focus.style, 'none');
    await screenshot('keyboard-focus');
    await evaluate("document.getElementById('start-real').focus()");
    await call('Input.dispatchKeyEvent', {type:'keyDown',key:'Tab',code:'Tab',windowsVirtualKeyCode:9});
    await call('Input.dispatchKeyEvent', {type:'keyUp',key:'Tab',code:'Tab',windowsVirtualKeyCode:9});
    assert.equal(await evaluate('document.activeElement.id'), 'start-replay');
    await call('Emulation.setEmulatedMedia', {features:[{name:'prefers-reduced-motion',value:'reduce'}]});
    const reduced = await evaluate("getComputedStyle(document.getElementById('start-real')).transitionDuration");
    assert.ok(reduced.split(',').every(value=>parseFloat(value) <= 0.01));
    if (readingLayout) {
      await evaluate("document.getElementById('reset-layout').click(); document.getElementById('sidebar-resizer').focus()");
      await call('Input.dispatchKeyEvent',{type:'keyDown',key:'ArrowRight',code:'ArrowRight',windowsVirtualKeyCode:39});
      await call('Input.dispatchKeyEvent',{type:'keyUp',key:'ArrowRight',code:'ArrowRight',windowsVirtualKeyCode:39});
      const saved=await evaluate("JSON.parse(localStorage.getItem('code-review-layout-v1'))");
      assert.ok(saved.left > 248);
      await call('Page.reload');
      await until(()=>evaluate("typeof state !== 'undefined' && !!state.selected"),'layout reload');
      assert.equal(await evaluate("document.getElementById('sidebar').getBoundingClientRect().width"),saved.left);
      layoutChecks.keyboardResize=true;layoutChecks.persistedAfterReload=true;
      await evaluate("document.getElementById('reset-layout').click()");
    }
    await fs.writeFile(path.join(output,'interaction-checks.json'),JSON.stringify({replay:true,reportDownload:true,evidenceLocation:true,
      partialControlled:true,missingKeyControlled:true,invalidRepository:true,keyboardTabs:true,focus,reducedMotion:reduced,
      accessibility,formAccessibility,normalCompletedNoFindings:true,actualGitPreview:true,mobileReport:true,mobileLongContent:true,keyboardActionOrder:true,
      layoutChecks,realModelCalls:0},null,2));
    console.log(JSON.stringify({viewports:observations,interactions:'passed',realModelCalls:0}));
  } finally {
    if (socket) socket.close();
    chrome.kill();
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
