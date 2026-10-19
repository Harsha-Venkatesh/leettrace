'use strict';

const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const SAMPLE_CODE = `# Definition for a binary tree node.
# class TreeNode:
#     def __init__(self, val=0, left=None, right=None):
#         self.val = val
#         self.left = left
#         self.right = right
class Solution:
    def checker(self,root,maxval):
        count=0
        if root == None:
            return 0
        if root.val >= maxval:
            count=1
        maxval=max(root.val,maxval)
        count+=self.checker(root.left,maxval)
        count+=self.checker(root.right,maxval)
        return count

    def goodNodes(self, root: TreeNode) -> int:

        return self.checker(root,root.val)
`;
const SAMPLE_TC = '[3,1,4,3,null,1,5]';

const S = {
  trace: null, i: 0, timer: null, code: '', lines: [],
  callMap: new Map(),      // call id -> call record
  nodeCalls: new Map(),    // heap oid of a tree/list node -> calls that received it
  rt: null,                // recursion tree DOM handles
};

/* ================================================================ bridge */

async function runTracer(payload) {
  const body = JSON.stringify(payload);
  const h = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.tracer;
  if (h) return JSON.parse(await h.postMessage(body));
  const r = await fetch('/trace', { method: 'POST', body });
  return r.json();
}

/* ================================================================ helpers */

function store(k, v) { try { localStorage.setItem('leettrace.' + k, v); } catch (e) { /* no storage */ } }
function load(k) { try { return localStorage.getItem('leettrace.' + k); } catch (e) { return null; } }

const KW = new Set('def class return if elif else for while in not and or is import from as pass break continue lambda yield with try except finally raise global nonlocal del assert async await'.split(' '));
const CONST = new Set(['None', 'True', 'False']);
const BUILTIN = new Set('len range max min sum abs sorted reversed enumerate zip map filter list dict set tuple int str float bool print any all isinstance deque defaultdict Counter heappush heappop heapify inf ord chr divmod pow'.split(' '));

function hl(line) {
  const re = /(#.*$)|("(?:\\.|[^"\\])*"?|'(?:\\.|[^'\\])*'?)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_]\w*)|(\s+)|(.)/g;
  let out = '', m, prevWord = '';
  while ((m = re.exec(line))) {
    if (m[1]) out += `<span class="t-com">${esc(m[1])}</span>`;
    else if (m[2]) out += `<span class="t-str">${esc(m[2])}</span>`;
    else if (m[3]) out += `<span class="t-num">${m[3]}</span>`;
    else if (m[4]) {
      const w = m[4];
      let c = '';
      if (prevWord === 'def' || prevWord === 'class') c = 't-def';
      else if (KW.has(w)) c = 't-kw';
      else if (CONST.has(w)) c = 't-const';
      else if (w === 'self') c = 't-self';
      else if (BUILTIN.has(w)) c = 't-bi';
      else if (/^\s*\(/.test(line.slice(re.lastIndex))) c = 't-fn';
      out += c ? `<span class="${c}">${w}</span>` : w;
      prevWord = w;
      continue;
    } else out += esc(m[0]);
    if (!m[5]) prevWord = '';
  }
  return out;
}

const src = ln => S.lines[ln - 1] || '';

/** Short human text for an encoded value. */
function vtext(v, heap, d = 0) {
  if (!v) return '';
  if (v[0] === 'p' || v[0] === 'f') return v[1];
  const o = heap[v[1]];
  if (!o) return '…';
  const items = (arr, f) => arr.slice(0, 8).map(f).join(', ') + ((o.len || arr.length) > 8 ? ', …' : '');
  switch (o.k) {
    case 'tree': case 'll': return 'node ' + vtext(o.val, heap, d + 1);
    case 'list': {
      if (d > 1) return '[…]';
      const body = items(o.i, x => vtext(x, heap, d + 1));
      return o.c === 'tuple' ? `(${body})` : o.c === 'deque' ? `deque([${body}])` : `[${body}]`;
    }
    case 'set': return d > 1 ? '{…}' : `{${items(o.i, x => vtext(x, heap, d + 1))}}`;
    case 'dict': return d > 1 ? '{…}' : `{${items(o.i, ([a, b]) => vtext(a, heap, d + 1) + ': ' + vtext(b, heap, d + 1))}}`;
    case 'obj': return o.c + ' object';
    case 'raw': return o.v;
    default: return '…';
  }
}

function refLabel(o) {
  if (!o) return '';
  switch (o.k) {
    case 'tree': case 'll': return 'node ' + (o.val ? o.val[1] : '');
    case 'list': return `${o.c}[${o.len}]`;
    case 'set': return `set(${o.len})`;
    case 'dict': return `${o.c}(${o.len})`;
    case 'obj': return o.c;
    default: return o.c || '';
  }
}

function isHiddenSelf(k, v, heap) {
  if (k !== 'self' || v[0] !== 'r') return false;
  const o = heap[v[1]];
  return o && o.k === 'obj' && o.a.length === 0;
}

function primClass(t) {
  if (t === 'None') return 'none';
  if (t === 'True' || t === 'False') return 'bool';
  if (/^['"]/.test(t)) return 'str';
  if (/^-?[\d.]/.test(t) || t === 'inf' || t === '-inf') return 'num';
  return '';
}

/** HTML for a value cell. `w` is the arrow weight: top | other | obj. */
function valHTML(v, heap, w) {
  if (v[0] === 'p') return `<span class="prim ${primClass(v[1])}">${esc(v[1])}</span>`;
  if (v[0] === 'f') return `<span class="fnv">${esc(v[1])}</span>`;
  const o = heap[v[1]];
  return `<span class="ref" data-to="${v[1]}" data-w="${w}"><i class="dot"></i><span class="rl">${esc(refLabel(o))}</span></span>`;
}

const fmtArgs = (call, node) => call.a.map(([k, t, isNode]) => node ? (isNode ? '◉' + t.slice(5) : t) : `${k}=${t}`).join(', ');
const fmtCall = call => `${call.n}(${fmtArgs(call)})`;

function changedVars(frame, heap, pframe, pheap) {
  const out = new Map();
  if (!pframe) return out;
  const before = new Map(pframe.v.map(([k, v]) => [k, vtext(v, pheap)]));
  for (const [k, v] of frame.v) {
    if (!before.has(k)) out.set(k, { kind: 'new', now: vtext(v, heap) });
    else {
      const now = vtext(v, heap);
      if (now !== before.get(k)) out.set(k, { kind: 'chg', was: before.get(k), now });
    }
  }
  return out;
}

/* ================================================================ loading a trace */

async function visualize() {
  const code = $('#editor').value;
  const testcase = $('#testcase').value;
  const method = $('#method').hidden ? null : $('#method').value;
  store('code', code); store('tc', testcase);
  stop();
  const btn = $('#b-run');
  btn.disabled = true; btn.textContent = 'Running…';
  let t;
  try { t = await runTracer({ code, testcase, method }); }
  catch (e) { t = { ok: false, error: { msg: 'Could not run Python: ' + e } }; }
  btn.disabled = false; btn.textContent = 'Visualize ▸';
  if (!t.ok) return showError(t.error);
  loadTrace(t, code);
}

function showError(e) {
  setMode('edit');
  document.body.classList.add('idle');
  const ex = $('#explain');
  ex.className = 'explain err';
  ex.innerHTML = `<div class="ex-title"><span class="ex-kind">error</span>${esc(e.msg)}</div>
    <div class="ex-sub">${e.line ? `On line ${e.line} of your code.` : 'Fix it and press Visualize again.'}</div>`;
  $('#output').innerHTML = `<div class="out-row"><span class="out-k">Error</span><span class="out-v err">${esc(e.msg)}${e.line ? ` (line ${e.line})` : ''}</span></div>`;
}

function loadTrace(t, code) {
  S.trace = t; S.code = code; S.lines = code.split('\n');
  S.callMap = new Map(t.calls.map(c => [c.id, c]));
  S.nodeCalls = new Map();
  for (const c of t.calls) {
    if (c.n.startsWith('<') || !c.r.length) continue;
    const id = c.r[0];
    if (!S.nodeCalls.has(id)) S.nodeCalls.set(id, []);
    S.nodeCalls.get(id).push(c);
  }
  const sel = $('#method');
  sel.innerHTML = t.methods.map(m => `<option value="${esc(m)}" ${m === t.entry ? 'selected' : ''}>entry: ${esc(m)}()</option>`).join('');
  sel.hidden = t.methods.length < 2;
  $('#slider').max = t.steps.length - 1;
  document.body.classList.remove('idle');
  buildCodeView();
  buildRTree();
  setMode('view');
  go(0);
}

function setMode(m) {
  const editing = m === 'edit';
  document.body.classList.toggle('editing', editing);
  $('#editor').hidden = !editing;
  $('#codeview').hidden = editing;
  $('#b-edit').hidden = editing || !S.trace;
  $('#code-legend').hidden = editing;
  $('#transport').classList.toggle('off', editing || !S.trace);
  if (editing) $('#editor').focus();
}

/* ================================================================ stepping */

function go(i) {
  const t = S.trace;
  if (!t) return;
  S.i = Math.max(0, Math.min(t.steps.length - 1, i));
  render();
}

function play() {
  if (S.timer) return stop();
  if (S.i >= S.trace.steps.length - 1) go(0);
  $('#b-play').textContent = '❚❚';
  const tick = () => {
    if (S.i >= S.trace.steps.length - 1) return stop();
    go(S.i + 1);
    S.timer = setTimeout(tick, +$('#speed').value);
  };
  S.timer = setTimeout(tick, +$('#speed').value);
}

function stop() {
  clearTimeout(S.timer); S.timer = null;
  $('#b-play').textContent = '▶';
}

function seek(pred, dir) {
  const st = S.trace.steps;
  for (let j = S.i + dir; j >= 0 && j < st.length; j += dir) if (pred(st[j], j)) return go(j);
  go(dir > 0 ? st.length - 1 : 0);
}

function finishCall() {
  const top = S.trace.steps[S.i].s.at(-1);
  const c = S.callMap.get(top.id);
  if (c && c.e != null) go(c.e === S.i ? c.e + 1 : c.e);
  else go(S.trace.steps.length - 1);
}

/* ================================================================ render */

function render() {
  const t = S.trace, st = t.steps[S.i];
  $('#slider').value = S.i;
  $('#stepinfo').textContent = `Step ${S.i + 1} / ${t.steps.length}`;
  renderCode(st);
  renderExplain(st);
  renderMemory(st);
  renderRTree(st);
  renderOutput(st);
}

function buildCodeView() {
  $('#codeview').innerHTML = S.lines.map((l, k) =>
    `<div class="ln" data-ln="${k + 1}"><span class="gut"><span class="num">${k + 1}</span><span class="arw"></span></span><span class="src">${hl(l) || ' '}</span><span class="tags"></span></div>`).join('');
}

/** Line that finished executing right before step i. */
function justRanLine(i) {
  const t = S.trace, st = t.steps[i], prev = t.steps[i - 1];
  if (!prev) return null;
  if (st.e === 'return' || st.e === 'exception') return null; // st.ln itself is the line that ran
  if (prev.e === 'line') return prev.ln;
  if (prev.e === 'return' && prev.s.length >= 2) return prev.s.at(-2).ln;
  return null;
}

function renderCode(st) {
  const view = $('#codeview');
  view.querySelectorAll('.ln.prev,.ln.next,.ln.ret,.ln.err,.ln.wait').forEach(e => e.classList.remove('prev', 'next', 'ret', 'err', 'wait'));
  view.querySelectorAll('.tags').forEach(e => { e.innerHTML = ''; });
  const row = ln => view.querySelector(`.ln[data-ln="${ln}"]`);

  // caller frames paused mid-line
  const waits = new Map();
  st.s.slice(0, -1).forEach(f => { waits.set(f.ln, (waits.get(f.ln) || []).concat(f.n)); });
  for (const [ln, names] of waits) {
    const r = row(ln); if (!r) continue;
    r.classList.add('wait');
    const counts = {};
    names.forEach(n => { counts[n] = (counts[n] || 0) + 1; });
    r.querySelector('.tags').innerHTML = Object.entries(counts)
      .map(([n, c]) => `<span class="tag">⏸ ${esc(n)}${c > 1 ? ' ×' + c : ''} waiting</span>`).join('');
  }

  const prevLn = justRanLine(S.i);
  const isLast = S.i === S.trace.steps.length - 1;
  const cls = st.e === 'return' ? 'ret' : st.e === 'exception' || (isLast && S.trace.error) ? 'err' : 'next';
  if (prevLn && prevLn !== st.ln) row(prevLn)?.classList.add('prev');
  const cur = row(st.ln);
  if (cur) {
    cur.classList.remove('wait');
    cur.classList.add(cls);
    cur.scrollIntoView({ block: 'nearest' });
  }
}

function renderExplain(st) {
  const t = S.trace, i = S.i, prev = t.steps[i - 1];
  const top = st.s.at(-1), caller = st.s.at(-2);
  const call = S.callMap.get(top.id) || { n: top.n, a: [] };
  const depth = st.s.length;
  const sameName = st.s.filter(f => f.n === top.n).length;
  let kind, title, sub = [];

  if (st.e === 'call') {
    kind = 'call';
    title = `<span class="ex-kind">call</span> Calling <code>${esc(fmtCall(call))}</code>`;
    if (caller && caller.n === top.n) sub.push(`<b>Recursive call</b> — ${esc(top.n)} calls itself.`);
    sub.push(caller ? `${esc(caller.n)} pauses at line ${caller.ln} until this call returns.`
      : `LeetCode calls your method with the test-case input.`);
  } else if (st.e === 'return') {
    kind = 'ret';
    const rv = vtext(top.ret, st.h);
    title = `<span class="ex-kind">return</span> <code>${esc(fmtCall(call))}</code> returns <b class="val">${esc(rv)}</b>`;
    sub.push(caller ? `This frame is removed from the stack; ${esc(caller.n)} resumes at line ${caller.ln} with the value ${esc(rv)}.`
      : `That's the final answer handed back to LeetCode.`);
  } else if (st.e === 'exception') {
    kind = 'err';
    title = `<span class="ex-kind">exception</span> ${esc(st.x)}`;
    sub.push(`Raised on line ${st.ln} in ${esc(top.n)}.`);
  } else {
    kind = 'line';
    const ran = justRanLine(i);
    title = `<span class="ex-kind">next</span> Line ${st.ln}: <code>${hl(src(st.ln).trim())}</code>`;
    if (ran) sub.push(`Line ${ran} just ran.`);
  }

  // what changed in the running frame
  if (prev && st.e !== 'call') {
    const pf = prev.s.find(f => f.id === top.id);
    const ch = changedVars(top, st.h, pf, prev.h);
    const chips = [];
    for (const [k, c] of ch) {
      if (isHiddenSelf(k, top.v.find(x => x[0] === k)[1], st.h)) continue;
      chips.push(c.kind === 'new' ? `<span class="chg new">${esc(k)} = ${esc(c.now)}</span>`
        : `<span class="chg">${esc(k)}: ${esc(c.was)} → ${esc(c.now)}</span>`);
    }
    if (chips.length) sub.push(chips.join(' '));
  }
  sub.push(`<span class="chip">stack depth ${depth}</span>`);
  if (sameName > 1) sub.push(`<span class="chip">${sameName} copies of ${esc(top.n)} on the stack</span>`);
  if (i === t.steps.length - 1) {
    if (t.error) { kind = 'err'; sub.unshift(`<b>Stopped with an error:</b> ${esc(t.error.msg)}`); }
    else if (t.truncated) sub.unshift(`<b>Stopped early</b> — ${t.truncated === 'time' ? 'took over 10 s' : `hit the ${t.maxSteps}-step limit`} (infinite loop or big input?).`);
    else sub.unshift(`<b>Done.</b> Answer: <b class="val">${esc(t.result)}</b>`);
  }
  const ex = $('#explain');
  ex.className = 'explain ' + kind;
  ex.innerHTML = `<div class="ex-title">${title}</div><div class="ex-sub">${sub.join(' ')}</div>`;
}

/* ---------------------------------------------------------------- memory */

/** Status of a tree/list node at step i based on the calls that received it. */
function nodeStatus(oid, st) {
  const calls = S.nodeCalls.get(oid);
  if (!calls) return null;
  const topId = st.s.at(-1).id;
  let last = null;
  for (const c of calls) if (c.s <= S.i) last = c;
  if (!last) return null;
  if (last.id === topId) return { cls: 'cur', ret: last.e === S.i ? last.ret : null };
  if (last.e != null && last.e < S.i) return { cls: 'done', ret: last.ret };
  return { cls: 'act', ret: null };
}

function renderMemory(st) {
  const prev = S.trace.steps[S.i - 1];
  const heap = st.h;

  // frames, newest first
  const n = st.s.length;
  let fh = '';
  [...st.s].reverse().forEach((f, idx) => {
    const isTop = idx === 0;
    const call = S.callMap.get(f.id);
    const pf = prev && prev.s.find(x => x.id === f.id);
    const ch = changedVars(f, heap, pf, prev && prev.h);
    const status = isTop
      ? (st.e === 'return' ? 'returning ↩' : st.e === 'call' ? 'just called' : st.e === 'exception' ? 'raising ✕' : `running · line ${f.ln}`)
      : `⏸ paused at line ${f.ln}`;
    const rows = f.v.filter(([k, v]) => !isHiddenSelf(k, v, heap)).map(([k, v]) => {
      const c = ch.get(k);
      return `<tr class="${c ? c.kind : ''}"><td class="vn">${esc(k)}</td><td class="vv">${valHTML(v, heap, isTop ? 'top' : 'other')}</td></tr>`;
    }).join('');
    const ret = f.ret ? `<tr class="retrow"><td class="vn">↩ return</td><td class="vv">${valHTML(f.ret, heap, 'top')}</td></tr>` : '';
    const args = call && call.a.length ? `<div class="fargs">called with ${esc(fmtArgs(call))}</div>` : '';
    fh += `<div class="frame ${isTop ? 'top' : 'waiting'} ${isTop && st.e === 'return' ? 'returning' : ''}">
      <div class="fh"><span class="fn">${esc(f.n)}</span><span class="dep">depth ${n - idx}</span><span class="stat">${status}</span></div>
      ${args}<table>${rows + ret || '<tr><td class="none" colspan="2">no local variables yet</td></tr>'}</table></div>`;
  });
  $('#frames').innerHTML = fh;
  $('#objects').innerHTML = renderObjects(st, prev);
  requestAnimationFrame(drawArrows);
}

function renderObjects(st, prev) {
  const heap = st.h, ph = (prev && prev.h) || {};
  const ids = Object.keys(heap).map(Number).sort((a, b) => a - b);
  const done = new Set();
  const parts = [];
  const fresh = id => (prev && !(id in ph)) ? ' fresh' : '';

  // reference counts (to decide which nested lists can be drawn as a matrix)
  const refc = new Map();
  const bump = v => { if (v && v[0] === 'r') refc.set(v[1], (refc.get(v[1]) || 0) + 1); };
  st.s.forEach(f => { f.v.forEach(([, v]) => bump(v)); if (f.ret) bump(f.ret); });
  ids.forEach(id => {
    const o = heap[id]; if (!o) return;
    if (o.k === 'list' || o.k === 'set') o.i.forEach(bump);
    else if (o.k === 'dict') o.i.forEach(([a, b]) => { bump(a); bump(b); });
    else if (o.k === 'obj') o.a.forEach(([, v]) => bump(v));
    else if (o.k === 'tree') { bump(o.l); bump(o.r); bump(o.val); }
    else if (o.k === 'll') { bump(o.n); bump(o.val); }
  });

  // binary trees
  const treeIds = ids.filter(id => heap[id] && heap[id].k === 'tree');
  const kids = new Set();
  treeIds.forEach(id => [heap[id].l, heap[id].r].forEach(c => { if (c[0] === 'r' && heap[c[1]] && heap[c[1]].k === 'tree') kids.add(c[1]); }));
  treeIds.filter(id => !kids.has(id)).forEach(r => parts.push(renderDTree(r, heap, done, st)));

  // linked lists
  const llIds = ids.filter(id => heap[id] && heap[id].k === 'll');
  const nexts = new Set(llIds.map(id => heap[id].n).filter(v => v[0] === 'r').map(v => v[1]));
  const heads = llIds.filter(id => !nexts.has(id));
  heads.concat(llIds).forEach(h => { if (!done.has(h)) parts.push(renderChain(h, heap, done, st)); });

  // everything else
  for (const id of ids) {
    if (done.has(id)) continue;
    const o = heap[id];
    if (!o || o.k === 'more') continue;
    if (o.k === 'obj' && o.a.length === 0 && o.c === 'Solution') continue;
    parts.push(renderObj(id, o, heap, ph, refc, done, fresh(id)));
  }
  return parts.join('') || '<div class="empty-note">No objects yet — only simple values so far.</div>';
}

function cellsHTML(items, heap, pitems, withIndex) {
  if (!items.length) return '<span class="cell empty">empty</span>';
  return items.map((v, k) => {
    const chg = pitems && JSON.stringify(pitems[k]) !== JSON.stringify(v) ? ' chg' : '';
    return `<span class="cell${chg}">${withIndex ? `<span class="ci">${k}</span>` : ''}<span class="cv">${valHTML(v, heap, 'obj')}</span></span>`;
  }).join('');
}

function renderObj(id, o, heap, ph, refc, done, fresh) {
  done.add(id);
  const p = ph[id];
  const more = o.len > (o.i ? o.i.length : 0) ? `<span class="more">+${o.len - o.i.length} more</span>` : '';
  if (o.k === 'list') {
    // list of plain lists → draw as a grid (DP tables, grids, matrices)
    const rows = o.i.map(v => v[0] === 'r' ? heap[v[1]] : null);
    if (o.i.length && rows.every((r, k) => r && r.k === 'list' && refc.get(o.i[k][1]) === 1 && r.i.every(x => x[0] === 'p'))) {
      const w = Math.max(...rows.map(r => r.i.length));
      let tb = `<tr><th></th>${Array.from({ length: w }, (_, c) => `<th>${c}</th>`).join('')}</tr>`;
      rows.forEach((r, k) => {
        const rid = o.i[k][1]; done.add(rid);
        const pr = ph[rid];
        tb += `<tr id="obj-${rid}"><th>${k}</th>${r.i.map((v, c) => `<td class="${pr && JSON.stringify(pr.i[c]) !== JSON.stringify(v) ? 'chg' : ''}"><span class="prim ${primClass(v[1])}">${esc(v[1])}</span></td>`).join('')}</tr>`;
      });
      return `<div class="obj${fresh}" id="obj-${id}"><div class="olabel">${o.c} ${o.len}×${w}</div><table class="mtx">${tb}</table>${more}</div>`;
    }
    return `<div class="obj${fresh}" id="obj-${id}"><div class="olabel">${o.c}</div><div class="cells">${cellsHTML(o.i, heap, p && p.i, true)}${more}</div></div>`;
  }
  if (o.k === 'set') {
    return `<div class="obj${fresh}" id="obj-${id}"><div class="olabel">${o.c}</div><div class="cells">${cellsHTML(o.i, heap, null, false)}${more}</div></div>`;
  }
  if (o.k === 'dict') {
    const pm = new Map(p && p.i ? p.i.map(([a, b]) => [JSON.stringify(a), JSON.stringify(b)]) : []);
    const rows = o.i.map(([a, b]) => {
      const was = pm.get(JSON.stringify(a));
      const chg = p && was !== JSON.stringify(b) ? 'chg' : '';
      return `<tr class="${chg}"><td class="k">${valHTML(a, heap, 'obj')}</td><td>${valHTML(b, heap, 'obj')}</td></tr>`;
    }).join('');
    return `<div class="obj${fresh}" id="obj-${id}"><div class="olabel">${o.c}</div>${o.i.length ? `<table class="kv">${rows}</table>` : '<span class="cell empty">empty</span>'}${more}</div>`;
  }
  if (o.k === 'obj') {
    const pa = new Map(p && p.a ? p.a.map(([k, v]) => [k, JSON.stringify(v)]) : []);
    const rows = o.a.map(([k, v]) => `<tr class="${p && pa.get(k) !== JSON.stringify(v) ? 'chg' : ''}"><td class="vn">${esc(k)}</td><td>${valHTML(v, heap, 'obj')}</td></tr>`).join('');
    return `<div class="obj objbox${fresh}" id="obj-${id}"><div class="oh">${esc(o.c)}</div><table>${rows || '<tr><td class="vn">no attributes</td></tr>'}</table></div>`;
  }
  return `<div class="obj${fresh}" id="obj-${id}"><div class="olabel">${esc(o.c)}</div><span class="cell"><span class="cv">${esc(o.v)}</span></span></div>`;
}

function renderDTree(rootId, heap, done, st) {
  const pos = new Map();
  let x = 0, maxD = 0;
  const visit = (id, d) => {
    const o = heap[id];
    if (pos.has(id) || !o || o.k !== 'tree') return;
    pos.set(id, { x: 0, d });
    done.add(id);
    maxD = Math.max(maxD, d);
    if (o.l[0] === 'r') visit(o.l[1], d + 1);
    pos.get(id).x = x++;
    if (o.r[0] === 'r') visit(o.r[1], d + 1);
  };
  visit(rootId, 0);
  const DX = 46, DY = 64, R = 17, PAD = 14;
  const cx = p => PAD + R + p.x * DX, cy = p => PAD + 4 + R + p.d * DY;
  const W = PAD * 2 + R * 2 + (x - 1) * DX, H = cy({ d: maxD }) + R + 26;
  let edges = '', nodes = '';
  for (const [id, p] of pos) {
    const o = heap[id];
    const s = nodeStatus(id, st);
    [['l', -1], ['r', 1]].forEach(([side, dir]) => {
      const c = o[side];
      if (c[0] === 'r' && pos.has(c[1])) {
        const q = pos.get(c[1]); const cs = nodeStatus(c[1], st);
        edges += `<line class="${cs ? 'e-' + cs.cls : ''}" x1="${cx(p)}" y1="${cy(p)}" x2="${cx(q)}" y2="${cy(q)}"/>`;
      } else if (c[0] === 'p' && c[1] === 'None' && s) {
        edges += `<text class="nul" x="${cx(p) + dir * 22 - 4}" y="${cy(p) + 30}">∅</text>`;
      }
    });
    nodes += `<div class="tnode ${s ? 'st-' + s.cls : ''}" id="obj-${id}" style="left:${cx(p) - R}px;top:${cy(p) - R}px">${esc(o.val[1])}</div>`;
    if (s && s.ret != null) nodes += `<span class="tret ${s.cls === 'cur' ? 'cur' : ''}" style="left:${cx(p) + R - 6}px;top:${cy(p) + R - 8}px">↩${esc(s.ret)}</span>`;
  }
  return `<div class="obj"><div class="olabel">${esc(heap[rootId].c)} (binary tree)</div><div class="dtree" style="width:${W}px;height:${H}px"><svg width="${W}" height="${H}">${edges}</svg>${nodes}</div></div>`;
}

function renderChain(head, heap, done, st) {
  let cur = head;
  const seen = new Set(), out = [];
  while (cur != null && heap[cur] && heap[cur].k === 'll' && !seen.has(cur) && !done.has(cur)) {
    seen.add(cur); done.add(cur);
    const o = heap[cur];
    const s = nodeStatus(cur, st);
    const nx = o.n;
    const next = nx[0] === 'r' ? nx[1] : null;
    const tail = next == null ? '∅' : (seen.has(next) || done.has(next) ? `<span class="ref" data-to="${next}" data-w="obj"><i class="dot"></i></span>` : '•');
    out.push(`<div class="lnode ${s ? 'st-' + s.cls : ''}" id="obj-${cur}"><span class="lv">${esc(o.val[1])}</span><span class="lp">${tail}</span></div>`);
    if (next != null && !seen.has(next) && !done.has(next)) out.push('<span class="larrow">→</span>');
    cur = next;
  }
  return `<div class="obj"><div class="olabel">${esc(heap[head].c)} (linked list)</div><div class="chain">${out.join('')}</div></div>`;
}

function drawArrows() {
  const inner = $('#mem-inner'), svg = $('#arrows');
  const base = inner.getBoundingClientRect();
  svg.setAttribute('width', inner.scrollWidth);
  svg.setAttribute('height', inner.scrollHeight);
  let paths = '';
  inner.querySelectorAll('.ref[data-to]').forEach(r => {
    const tgt = document.getElementById('obj-' + r.dataset.to);
    if (!tgt) return;
    const d = r.querySelector('.dot').getBoundingClientRect();
    const sx = d.left + d.width / 2 - base.left, sy = d.top + d.height / 2 - base.top;
    const t = tgt.getBoundingClientRect();
    const tl = t.left - base.left, tt = t.top - base.top;
    let tx, ty, c2x, c2y;
    if (tgt.classList.contains('tnode')) {
      const cx = tl + t.width / 2, cy = tt + t.height / 2;
      const a = Math.atan2(sy - cy, sx - cx), rr = t.width / 2 + 3;
      tx = cx + Math.cos(a) * rr; ty = cy + Math.sin(a) * rr;
      c2x = cx + Math.cos(a) * (rr + 50); c2y = cy + Math.sin(a) * (rr + 50);
    } else if (tl > sx + 10) {
      tx = tl - 2; ty = tt + Math.min(16, t.height / 2);
      c2x = tx - Math.max(30, (tx - sx) / 2); c2y = ty;
    } else {
      tx = tl + 14; ty = tt - 2;
      c2x = tx; c2y = ty - 40;
    }
    const c1x = sx + Math.max(30, Math.abs(tx - sx) / 2), c1y = sy;
    const w = r.dataset.w;
    paths += `<path class="a-${w}" d="M${sx},${sy} C${c1x},${c1y} ${c2x},${c2y} ${tx},${ty}" marker-end="url(#ah-${w})"/>`;
  });
  const mk = (n, v) => `<marker id="ah-${n}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" style="fill:var(${v})"/></marker>`;
  svg.innerHTML = `<defs>${mk('top', '--accent')}${mk('other', '--faint')}${mk('obj', '--muted')}</defs>${paths}`;
}

/* ---------------------------------------------------------------- recursion tree */

function buildRTree() {
  const t = S.trace;
  const panel = $('#rt-panel'), box = $('#rtree');
  const calls = t.calls.filter(c => !c.n.startsWith('<'));
  const all = new Map(t.calls.map(c => [c.id, c]));
  const nodes = new Map(calls.map(c => [c.id, { c, kids: [] }]));
  const roots = [];
  for (const c of calls) {
    let p = c.p;
    while (p != null && !nodes.has(p)) p = all.get(p) ? all.get(p).p : null;
    (p != null ? nodes.get(p).kids : roots).push(nodes.get(c.id));
  }
  if (calls.length <= 2 && !calls.some(c => calls.some(d => d.p === c.id && d.n === c.n))) {
    panel.classList.add('single');
    box.innerHTML = `<div class="rt-msg">No recursion in this run — ${calls.map(c => `<code>${esc(c.n)}()</code>`).join(' → ')} ${calls.length > 1 ? 'is a plain call chain' : 'runs as one call'}. Use the stack and objects above to follow it.</div>`;
    S.rt = null;
    return;
  }
  panel.classList.remove('single');

  const W = 118, H = 42, GX = 12, GY = 46, PAD = 16;
  let leaf = 0, maxD = 0;
  const place = (nd, d) => {
    nd.d = d; maxD = Math.max(maxD, d);
    if (!nd.kids.length) nd.x = leaf++;
    else { nd.kids.forEach(k => place(k, d + 1)); nd.x = (nd.kids[0].x + nd.kids.at(-1).x) / 2; }
  };
  roots.forEach(r => place(r, 0));
  const px = nd => PAD + nd.x * (W + GX), py = nd => PAD + nd.d * (H + GY);
  const SW = PAD * 2 + leaf * (W + GX) - GX, SH = PAD * 2 + (maxD + 1) * (H + GY) - GY + 8;
  const clip = (s, n) => s.length > n ? s.slice(0, n - 1) + '…' : s;

  let edges = '', gs = '';
  for (const nd of nodes.values()) {
    const x = px(nd), y = py(nd);
    for (const k of nd.kids) {
      const kx = px(k) + W / 2, ky = py(k);
      edges += `<path class="re" data-id="${k.c.id}" d="M${x + W / 2},${y + H} C${x + W / 2},${y + H + GY / 2} ${kx},${ky - GY / 2} ${kx},${ky}"/>`;
    }
    const args = clip(fmtArgs(nd.c, true), 17);
    const ret = clip(String(nd.c.ret == null ? '' : nd.c.ret), 12);
    const rw = Math.max(26, ret.length * 7 + 14);
    gs += `<g class="rn fut" data-id="${nd.c.id}" transform="translate(${x},${y})">
      <title>${esc(fmtCall(nd.c))}${nd.c.ret != null ? ' → ' + esc(nd.c.ret) : ''}</title>
      <rect width="${W}" height="${H}"/>
      <text class="rname" x="${W / 2}" y="15" text-anchor="middle">${esc(clip(nd.c.n, 18))}</text>
      <text class="rargs" x="${W / 2}" y="32" text-anchor="middle">(${esc(args)})</text>
      <rect class="rretbg" x="${W / 2 - rw / 2}" y="${H - 4}" width="${rw}" height="17"/>
      <text class="rret" x="${W / 2}" y="${H + 8}" text-anchor="middle">↩ ${esc(ret)}</text>
    </g>`;
  }
  box.innerHTML = `<svg width="${SW}" height="${SH}">${edges}${gs}</svg>`;
  S.rt = {
    g: [...box.querySelectorAll('.rn')].map(el => ({ el, c: S.callMap.get(+el.dataset.id) })),
    e: [...box.querySelectorAll('.re')].map(el => ({ el, c: S.callMap.get(+el.dataset.id) })),
  };
  box.querySelectorAll('.rn').forEach(el => el.addEventListener('click', () => { stop(); go(S.callMap.get(+el.dataset.id).s); }));
}

function callState(c, st) {
  if (c.s > S.i) return 'fut';
  if (c.e != null && c.e < S.i) return 'done';
  if (c.e === S.i) return 'retn';
  return c.id === st.s.at(-1).id ? 'cur' : 'act';
}

function renderRTree(st) {
  if (!S.rt) return;
  let curEl = null;
  for (const { el, c } of S.rt.g) {
    const s = callState(c, st);
    el.setAttribute('class', 'rn ' + s);
    if (s === 'cur' || s === 'retn') curEl = el;
  }
  for (const { el, c } of S.rt.e) el.setAttribute('class', 're ' + callState(c, st));
  if (curEl) {
    const box = $('#rtree');
    const b = box.getBoundingClientRect(), r = curEl.getBoundingClientRect();
    if (r.left < b.left || r.right > b.right || r.top < b.top || r.bottom + 20 > b.bottom) {
      box.scrollTo({
        left: box.scrollLeft + (r.left - b.left) - b.width / 2 + r.width / 2,
        top: box.scrollTop + (r.top - b.top) - b.height / 2 + r.height / 2,
        behavior: 'smooth',
      });
    }
  }
}

/* ---------------------------------------------------------------- output */

function renderOutput(st) {
  const t = S.trace;
  const last = S.i === t.steps.length - 1;
  let h = t.args.map(([k, v]) => `<div class="out-row"><span class="out-k">${esc(k)}</span><span class="out-v">${esc(v)}</span></div>`).join('');
  if (t.error) h += `<div class="out-row"><span class="out-k">Error</span><span class="out-v err">${esc(t.error.msg)}${t.error.line ? ` (line ${t.error.line})` : ''}</span></div>`;
  else if (t.result != null) h += `<div class="out-row"><span class="out-k">Output</span><span class="out-v ${last ? 'big' : 'muted'}">${last ? esc(t.result) : `… computed at step ${t.steps.length}`}</span></div>`;
  else if (t.truncated) h += `<div class="out-row"><span class="out-k">Output</span><span class="out-v err">stopped early (${t.truncated === 'time' ? 'time limit' : 'step limit'})</span></div>`;
  const so = t.stdout.slice(0, st.o);
  if (t.stdout) h += `<div class="out-row"><span class="out-k">print()</span><span class="out-v stdout">${esc(so) || '<span class="muted">nothing printed yet</span>'}</span></div>`;
  $('#output').innerHTML = h;
}

/* ================================================================ wiring */

function init() {
  $('#editor').value = load('code') || SAMPLE_CODE;
  $('#testcase').value = load('tc') || SAMPLE_TC;
  document.body.classList.add('idle');
  setMode('edit');

  $('#b-run').onclick = visualize;
  $('#b-edit').onclick = () => { stop(); setMode('edit'); };
  $('#b-first').onclick = () => { stop(); go(0); };
  $('#b-prev').onclick = () => { stop(); go(S.i - 1); };
  $('#b-next').onclick = () => { stop(); go(S.i + 1); };
  $('#b-last').onclick = () => { stop(); go(Infinity); };
  $('#b-play').onclick = play;
  $('#b-ncall').onclick = () => { stop(); seek(s => s.e === 'call', 1); };
  $('#b-pcall').onclick = () => { stop(); seek(s => s.e === 'call', -1); };
  $('#b-nret').onclick = () => { stop(); finishCall(); };
  $('#slider').oninput = e => { stop(); go(+e.target.value); };
  $('#method').onchange = visualize;
  $('#show-future').onchange = e => $('#rtree').classList.toggle('show-future', e.target.checked);
  $('#codeview').ondblclick = () => { stop(); setMode('edit'); };

  // Tab inserts spaces in the editors
  ['#editor', '#testcase'].forEach(sel => $(sel).addEventListener('keydown', e => {
    if (e.key === 'Tab' && !e.metaKey && !e.ctrlKey) {
      e.preventDefault();
      document.execCommand('insertText', false, '    ');
    }
  }));

  document.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); visualize(); return; }
    const zoomer = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.zoom;
    if (e.metaKey && zoomer && (e.key === '=' || e.key === '+')) { e.preventDefault(); zoomer.postMessage(1); return; }
    const typing = /^(TEXTAREA|INPUT|SELECT)$/.test(document.activeElement.tagName) && document.activeElement.type !== 'range';
    if (typing || !S.trace || document.body.classList.contains('editing')) return;
    if (e.key === 'ArrowRight') { stop(); e.shiftKey ? seek(s => s.e === 'call', 1) : go(S.i + 1); e.preventDefault(); }
    else if (e.key === 'ArrowLeft') { stop(); e.shiftKey ? seek(s => s.e === 'call', -1) : go(S.i - 1); e.preventDefault(); }
    else if (e.key === ' ') { play(); e.preventDefault(); }
    else if (e.key === 'Home') { stop(); go(0); }
    else if (e.key === 'End') { stop(); go(Infinity); }
  });
  window.addEventListener('resize', () => { if (S.trace) drawArrows(); });
}

init();
