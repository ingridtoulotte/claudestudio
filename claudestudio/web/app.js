/* ============================================================================
   ClaudeStudio — single-page app (vanilla JS, no build step)
   ========================================================================== */
'use strict';

// ---- tiny DOM + format helpers --------------------------------------------
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k === 'text') node.textContent = v;
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c == null || c === false) continue;
    node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return node;
}
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const fmt = {
  num: (n) => Number(n || 0).toLocaleString('en-US'),
  compact(n) {
    n = Number(n || 0);
    if (n >= 1e9) return (n / 1e9).toFixed(n >= 1e10 ? 0 : 1) + 'B';
    if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + 'k';
    return String(Math.round(n));
  },
  cost(n) {
    n = Number(n || 0);
    if (n === 0) return '$0';
    if (n < 0.01) return '$' + n.toFixed(4);
    if (n < 100) return '$' + n.toFixed(2);
    return '$' + n.toLocaleString('en-US', { maximumFractionDigits: 0 });
  },
  dur(s) {
    s = Number(s || 0);
    if (s < 60) return Math.round(s) + 's';
    if (s < 3600) return Math.round(s / 60) + 'm';
    return (s / 3600).toFixed(1) + 'h';
  },
  rel(epoch) {
    if (!epoch) return '—';
    const d = epoch * 1000, now = Date.now(), diff = (now - d) / 1000;
    if (diff < 60) return 'just now';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    if (diff < 86400 * 7) return Math.floor(diff / 86400) + 'd ago';
    return new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: diff > 86400 * 300 ? 'numeric' : undefined });
  },
  dt(epoch) { return epoch ? new Date(epoch * 1000).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'; },
};

function family(model) {
  const m = (model || '').toLowerCase();
  if (m.includes('fable')) return 'Fable';
  if (m.includes('mythos')) return 'Mythos';
  if (m.includes('opus')) return 'Opus';
  if (m.includes('sonnet')) return 'Sonnet';
  if (m.includes('haiku')) return 'Haiku';
  return 'Other';
}
const shortModel = (m) => (m || '').replace('claude-', '');

// ---- API ------------------------------------------------------------------
const API = {
  async get(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
    return r.json();
  },
  async post(path, body) {
    const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
    return r.json();
  },
  summary: () => API.get('/api/summary'),
  sessions: (q) => API.get('/api/sessions?' + new URLSearchParams(q)),
  session: (id) => API.get('/api/session/' + encodeURIComponent(id)),
  search: (q, limit = 30, filters = {}) => {
    const p = { q, limit };
    for (const k of ['kind', 'project', 'since', 'until', 'session']) if (filters[k]) p[k] = filters[k];
    return API.get('/api/search?' + new URLSearchParams(p));
  },
  analytics: () => API.get('/api/analytics'),
  projects: () => API.get('/api/projects'),
  wrapped: (year) => API.get('/api/wrapped' + (year ? '?year=' + year : '')),
  compare: (a, b) => API.get('/api/compare?' + new URLSearchParams({ a, b })),
  ask: (q, session) => API.get('/api/ask?' + new URLSearchParams(session ? { q, session } : { q })),
  state: (id, patch) => API.post('/api/state/' + encodeURIComponent(id), patch),
  reindex: () => API.post('/api/reindex', {}),
  exportUrl: (id, fmt) => '/api/session/' + encodeURIComponent(id) + '/export.' + fmt,
  saved: () => API.get('/api/saved'),
  addSaved: (b) => API.post('/api/saved', b),
  delSaved: (id) => fetch('/api/saved/' + encodeURIComponent(id), { method: 'DELETE' }).then((r) => r.json()),
};

// trigger a browser download from a server endpoint that sets Content-Disposition
function downloadFrom(url) {
  const a = el('a', { href: url, download: '' });
  document.body.appendChild(a);
  a.click();
  setTimeout(() => a.remove(), 0);
}

// ---- charts (hand-rolled SVG) ---------------------------------------------
function areaChart(series, { w = 600, h = 180, color = 'var(--accent)', key = 'value' } = {}) {
  const pts = series.map((d) => +d[key] || 0);
  const n = pts.length;
  if (!n) return el('div', { class: 'empty', text: 'No activity yet' });
  const max = Math.max(1, ...pts);
  const pad = 6;
  const X = (i) => pad + (i * (w - pad * 2)) / Math.max(1, n - 1);
  const Y = (v) => h - pad - (v / max) * (h - pad * 2 - 14);
  let line = '', area = `M ${X(0)} ${h - pad} `;
  pts.forEach((v, i) => { const cmd = i ? 'L' : 'M'; line += `${cmd} ${X(i).toFixed(1)} ${Y(v).toFixed(1)} `; area += `L ${X(i).toFixed(1)} ${Y(v).toFixed(1)} `; });
  area += `L ${X(n - 1)} ${h - pad} Z`;
  const gid = 'g' + Math.random().toString(36).slice(2, 7);
  const svg = `<svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" preserveAspectRatio="none">
    <defs><linearGradient id="${gid}" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0" stop-color="${color}" stop-opacity="0.35"/>
      <stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
    <path d="${area}" fill="url(#${gid})"/>
    <path d="${line}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${X(n - 1)}" cy="${Y(pts[n - 1])}" r="3.5" fill="${color}"/>
  </svg>`;
  return el('div', { html: svg });
}

function donut(segments, { size = 132, stroke = 18 } = {}) {
  const total = segments.reduce((a, s) => a + s.value, 0) || 1;
  const r = (size - stroke) / 2, c = 2 * Math.PI * r, cx = size / 2;
  let offset = 0, circles = '';
  for (const s of segments) {
    const frac = s.value / total, len = frac * c;
    circles += `<circle cx="${cx}" cy="${cx}" r="${r}" fill="none" stroke="${s.color}" stroke-width="${stroke}"
      stroke-dasharray="${len.toFixed(2)} ${(c - len).toFixed(2)}" stroke-dashoffset="${(-offset).toFixed(2)}"
      transform="rotate(-90 ${cx} ${cx})" stroke-linecap="butt"/>`;
    offset += len;
  }
  return `<svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}">${circles}
    <text x="${cx}" y="${cx - 4}" text-anchor="middle" fill="var(--text)" font-size="20" font-weight="700">${segments.length}</text>
    <text x="${cx}" y="${cx + 14}" text-anchor="middle" fill="var(--text-3)" font-size="10">models</text></svg>`;
}

function spark(values, { w = 240, h = 38, color = 'var(--accent)' } = {}) {
  if (!values.length) return '';
  const max = Math.max(1, ...values), n = values.length;
  const X = (i) => (i * w) / Math.max(1, n - 1), Y = (v) => h - 2 - (v / max) * (h - 6);
  let p = '';
  values.forEach((v, i) => { p += `${i ? 'L' : 'M'} ${X(i).toFixed(1)} ${Y(v).toFixed(1)} `; });
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" preserveAspectRatio="none"><path d="${p}" fill="none" stroke="${color}" stroke-width="1.6"/></svg>`;
}

const FAM_COLOR = { Opus: '#ff8a5b', Sonnet: '#6aa6ff', Haiku: '#5ec98a', Fable: '#9a8cff', Mythos: '#9a8cff', Other: '#6f7585' };

// ---- toasts ---------------------------------------------------------------
function toast(msg, kind = 'ok') {
  const host = $('#toasts');
  const t = el('div', { class: 'toast ' + (kind === 'err' ? 'err' : '') }, [el('span', { class: 'dot' }), el('span', { text: msg })]);
  host.appendChild(t);
  setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 300); }, 2600);
}

// ---- nav ------------------------------------------------------------------
const NAV = [
  { id: 'sessions', label: 'Sessions', icon: 'M4 6h16M4 12h16M4 18h10' },
  { id: 'ask', label: 'Ask', icon: 'M21 11.5a8.5 8.5 0 0 1-12.6 7.4L3 21l2.1-5.4A8.5 8.5 0 1 1 21 11.5z' },
  { id: 'timeline', label: 'Timeline', icon: 'M3 12h4l3-8 4 16 3-8h4' },
  { id: 'analytics', label: 'Analytics', icon: 'M4 19V5M4 19h16M8 16v-5M13 16V8M18 16v-9' },
  { id: 'projects', label: 'Projects', icon: 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z' },
  { id: 'compare', label: 'Compare', icon: 'M9 3v18M4 7h5M4 12h5M4 17h5M15 3v18M15 8h5M15 13h5' },
  { id: 'wrapped', label: 'Wrapped', icon: 'M12 2l2.4 5 5.6.5-4.2 3.7 1.3 5.5L12 19l-5.1 2.7 1.3-5.5L4 12.5 9.6 12z' },
];

function renderNav(summary) {
  const nav = $('#nav');
  nav.innerHTML = '';
  for (const item of NAV) {
    const btn = el('button', { class: 'nav-item', 'data-route': item.id, onclick: () => go(item.id) }, [
      el('span', { html: `<svg viewBox="0 0 24 24" width="17" height="17"><path d="${item.icon}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>` }),
      el('span', { text: item.label }),
    ]);
    if (item.id === 'sessions' && summary) btn.appendChild(el('span', { class: 'count', text: fmt.num(summary.sessions) }));
    if (item.id === 'projects' && summary) btn.appendChild(el('span', { class: 'count', text: fmt.num(summary.projects) }));
    nav.appendChild(btn);
  }
}

function highlightNav(route) {
  $$('.nav-item').forEach((n) => n.classList.toggle('active', n.dataset.route === route));
}

function renderFootStats(s) {
  $('#foot-stats').innerHTML = `
    <div><b>${fmt.num(s.messages)}</b> messages</div>
    <div><b>${fmt.compact(s.tokens)}</b> tokens · <b>${fmt.cost(s.cost_usd)}</b></div>`;
}

// ---- router ---------------------------------------------------------------
let STATE = { summary: null };
const view = () => $('#view');

// a per-view keyboard handler, torn down whenever the route changes so handlers
// from a previous view (e.g. replay nav) never leak into the next one.
let _viewKey = null;
function setViewKey(fn) {
  if (_viewKey) document.removeEventListener('keydown', _viewKey);
  _viewKey = fn || null;
  if (_viewKey) document.addEventListener('keydown', _viewKey);
}

function go(route, params = {}) {
  const qs = new URLSearchParams(params).toString();
  location.hash = '#/' + route + (qs ? '?' + qs : '');
}

async function router() {
  const raw = location.hash.replace(/^#\/?/, '') || 'sessions';
  const [path, query] = raw.split('?');
  const params = Object.fromEntries(new URLSearchParams(query || ''));
  const parts = path.split('/');
  const route = parts[0] || 'sessions';
  highlightNav(['session'].includes(route) ? 'sessions' : route);
  setViewKey(null);  // drop any previous view's keyboard handler
  try {
    if (route === 'session') return await viewSession(parts[1], params);
    if (route === 'ask') return await viewAsk(params);
    if (route === 'analytics') return await viewAnalytics();
    if (route === 'projects') return await viewProjects();
    if (route === 'timeline') return await viewTimeline();
    if (route === 'compare') return await viewCompare(params);
    if (route === 'wrapped') return await viewWrapped(params);
    if (route === 'search') return await viewSearch(params);
    return await viewSessions(params);
  } catch (e) {
    view().innerHTML = '';
    view().appendChild(el('div', { class: 'view-pad' }, [el('div', { class: 'empty' }, [
      el('div', { class: 'big', text: 'Something went wrong' }),
      el('div', { text: String(e.message || e) }),
    ])]));
  }
}

// ---- view: sessions -------------------------------------------------------
const SORTS = [['recent', 'Recent'], ['oldest', 'Oldest'], ['messages', 'Most messages'], ['tools', 'Most tools'], ['cost', 'Costliest'], ['tokens', 'Most tokens'], ['duration', 'Longest']];
let sessionsState = { q: '', sort: 'recent', favorite: false, archived: 'exclude', since: '', until: '', offset: 0, limit: 50 };

async function viewSessions(params) {
  if (params.project) sessionsState.project = params.project;
  const root = el('div', { class: 'view-pad fade-in' });
  view().innerHTML = ''; view().appendChild(root);

  root.appendChild(el('div', { class: 'page-head' }, [
    el('div', {}, [
      el('h1', { class: 'page-title', text: 'Sessions' }),
      el('div', { class: 'page-sub', html: sessionsState.project ? `Project · <code style="font-family:var(--mono)">${esc(sessionsState.project)}</code> · <a href="#/sessions" onclick="event.preventDefault();delete (window.__cs.ss().project);window.__cs.go('sessions')">clear</a>` : 'Every conversation you have had with Claude Code' }),
    ]),
  ]));

  const searchBox = el('div', { class: 'search-box' }, [
    el('span', { html: '<svg viewBox="0 0 24 24" width="15" height="15"><path d="M21 21l-4.3-4.3M19 11a8 8 0 1 1-16 0 8 8 0 0 1 16 0z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>' }),
    el('input', { class: 'input', placeholder: 'Filter sessions…', value: sessionsState.q }),
  ]);
  const sortSel = el('select', { class: 'input' }, SORTS.map(([v, l]) => el('option', { value: v, text: l, ...(v === sessionsState.sort ? { selected: 'selected' } : {}) })));
  const favChip = el('button', { class: 'chip toggle' + (sessionsState.favorite ? ' on' : '') }, ['★ Favorites']);
  const archChip = el('button', { class: 'chip toggle' + (sessionsState.archived === 'only' ? ' on' : '') }, ['Archived']);
  const saveBtn = el('button', { class: 'chip toggle', title: 'Save this filter as a smart collection' }, ['＋ Save search']);
  const sinceInp = el('input', { class: 'input date', type: 'date', title: 'Active on/after this date', value: sessionsState.since || '' });
  const untilInp = el('input', { class: 'input date', type: 'date', title: 'Started on/before this date', value: sessionsState.until || '' });
  const dateWrap = el('div', { class: 'date-range', title: 'Filter by date' }, [sinceInp, el('span', { class: 'date-sep', text: '→' }), untilInp]);

  const toolbar = el('div', { class: 'toolbar' }, [searchBox, sortSel, favChip, archChip, dateWrap, saveBtn]);
  root.appendChild(toolbar);
  const savedWrap = el('div', { class: 'saved-row' });
  root.appendChild(savedWrap);
  const listWrap = el('div', {});
  root.appendChild(listWrap);

  async function loadSaved() {
    savedWrap.innerHTML = '';
    let items = [];
    try { items = (await API.saved()).saved || []; } catch { return; }
    items.forEach((it) => {
      savedWrap.appendChild(el('span', { class: 'saved-chip' }, [
        el('span', { class: 'lbl', text: it.name, title: 'Apply saved search', onclick: () => applySaved(it) }),
        el('button', { class: 'x', title: 'Delete', onclick: async (e) => { e.stopPropagation(); await API.delSaved(it.id); toast('Deleted'); loadSaved(); } }, ['×']),
      ]));
    });
  }
  function applySaved(it) {
    const f = it.filters || {};
    sessionsState.q = it.query || '';
    sessionsState.sort = it.sort || 'recent';
    sessionsState.favorite = !!f.favorite;
    sessionsState.archived = f.archived || 'exclude';
    sessionsState.since = f.since || '';
    sessionsState.until = f.until || '';
    if (f.project) sessionsState.project = f.project; else delete sessionsState.project;
    sessionsState.offset = 0;
    viewSessions({});
  }
  saveBtn.addEventListener('click', async () => {
    const name = prompt('Name this saved search:', sessionsState.q || sessionsState.project || 'My search');
    if (!name) return;
    const filters = { favorite: sessionsState.favorite, archived: sessionsState.archived };
    if (sessionsState.project) filters.project = sessionsState.project;
    if (sessionsState.since) filters.since = sessionsState.since;
    if (sessionsState.until) filters.until = sessionsState.until;
    try { await API.addSaved({ name: name.trim(), query: sessionsState.q, sort: sessionsState.sort, filters }); toast('Search saved'); loadSaved(); }
    catch (e) { toast('Save failed: ' + e.message, 'err'); }
  });
  loadSaved();

  let timer;
  $('input', searchBox).addEventListener('input', (e) => { clearTimeout(timer); sessionsState.q = e.target.value; sessionsState.offset = 0; timer = setTimeout(load, 200); });
  sortSel.addEventListener('change', (e) => { sessionsState.sort = e.target.value; sessionsState.offset = 0; load(); });
  favChip.addEventListener('click', () => { sessionsState.favorite = !sessionsState.favorite; favChip.classList.toggle('on'); sessionsState.offset = 0; load(); });
  archChip.addEventListener('click', () => { sessionsState.archived = sessionsState.archived === 'only' ? 'exclude' : 'only'; archChip.classList.toggle('on'); sessionsState.offset = 0; load(); });
  sinceInp.addEventListener('change', (e) => { sessionsState.since = e.target.value; sessionsState.offset = 0; load(); });
  untilInp.addEventListener('change', (e) => { sessionsState.until = e.target.value; sessionsState.offset = 0; load(); });

  async function load() {
    listWrap.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
    const q = { sort: sessionsState.sort, limit: sessionsState.limit, offset: sessionsState.offset, archived: sessionsState.archived };
    if (sessionsState.q) q.q = sessionsState.q;
    if (sessionsState.favorite) q.favorite = '1';
    if (sessionsState.project) q.project = sessionsState.project;
    if (sessionsState.since) q.since = sessionsState.since;
    if (sessionsState.until) q.until = sessionsState.until;
    const data = await API.sessions(q);
    listWrap.innerHTML = '';
    if (!data.sessions.length) {
      const noFilter = !sessionsState.q && !sessionsState.favorite && !sessionsState.project && !sessionsState.since && !sessionsState.until && sessionsState.archived !== 'only';
      const firstRun = noFilter && (STATE.summary ? STATE.summary.sessions === 0 : true);
      if (firstRun) {
        listWrap.appendChild(el('div', { class: 'onboard' }, [
          el('div', { class: 'onboard-icn', text: '✦' }),
          el('div', { class: 'big', text: 'No sessions indexed yet' }),
          el('div', { class: 'onboard-sub', text: 'ClaudeStudio reads your Claude Code logs from ~/.claude/projects — entirely on this machine. Sync to index them, or explore with realistic sample data first.' }),
          el('div', { class: 'onboard-actions' }, [
            el('button', { class: 'btn-primary', onclick: () => doReindex() }, ['↻ Sync my sessions']),
          ]),
          el('div', { class: 'onboard-hint', html: 'No real sessions yet? Run <code>claudestudio demo</code> in your terminal for a synthetic workspace.' }),
          el('div', { class: 'foot-privacy', style: 'justify-content:center', html: '<svg viewBox="0 0 24 24" width="12" height="12"><path d="M12 2l8 4v6c0 5-3.4 8.5-8 10-4.6-1.5-8-5-8-10V6l8-4z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg> 100% local · nothing leaves your machine' }),
        ]));
      } else {
        listWrap.appendChild(el('div', { class: 'empty' }, [el('div', { class: 'big', text: 'No sessions match' }), el('div', { text: 'Try a different filter, or hit Sync to re-scan.' })]));
      }
      return;
    }
    const list = el('div', { class: 'session-list' });
    data.sessions.forEach((s) => list.appendChild(sessionRow(s)));
    listWrap.appendChild(list);
    // pager
    const pageCount = Math.ceil(data.total / sessionsState.limit);
    const page = Math.floor(sessionsState.offset / sessionsState.limit) + 1;
    listWrap.appendChild(el('div', { class: 'pager' }, [
      el('button', { onclick: () => { sessionsState.offset = Math.max(0, sessionsState.offset - sessionsState.limit); load(); }, disabled: sessionsState.offset === 0 || null, text: '← Prev' }),
      el('span', { text: `${page} / ${pageCount || 1} · ${fmt.num(data.total)} sessions` }),
      el('button', { onclick: () => { if (sessionsState.offset + sessionsState.limit < data.total) { sessionsState.offset += sessionsState.limit; load(); } }, disabled: sessionsState.offset + sessionsState.limit >= data.total || null, text: 'Next →' }),
    ]));
  }
  await load();
}

function sessionRow(s) {
  const models = (s.models || []).slice(0, 3).map((m) => {
    const f = family(m);
    return el('span', { class: `chip model-chip fam-${f}` }, [el('span', { class: 'dot' }), shortModel(m)]);
  });
  const star = s.favorite ? el('span', { class: 'star', html: '★' }) : null;
  const row = el('div', { class: 's-row', onclick: () => go('session/' + s.session_id) }, [
    el('div', { class: 's-main' }, [
      el('div', { class: 's-title' }, [star, el('span', { class: 'txt', text: s.title || 'Untitled session' })].filter(Boolean)),
      el('div', { class: 's-meta' }, [
        el('span', { class: 'proj-chip', text: s.project_name || s.project }),
        el('span', { class: 'sep', text: '·' }),
        ...models.flatMap((m, i) => i ? [m] : [m]),
        s.git_branch ? el('span', { class: 'sep', text: '·' }) : null,
        s.git_branch ? el('span', { html: `<svg viewBox="0 0 24 24" width="11" height="11" style="vertical-align:-1px"><path d="M6 3v12M6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 6a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 6c0 4-6 3-6 9" fill="none" stroke="currentColor" stroke-width="2"/></svg> ` + esc(s.git_branch) }) : null,
      ].filter(Boolean)),
      s.preview ? el('div', { class: 's-preview', text: s.preview }) : null,
    ].filter(Boolean)),
    el('div', { class: 's-side' }, [
      el('div', { class: 's-stats' }, [
        el('span', { html: `<b>${fmt.num(s.msg_count)}</b> msgs` }),
        el('span', { html: `<b>${fmt.num(s.tool_calls)}</b> tools` }),
      ]),
      el('div', { class: 's-cost', text: fmt.cost(s.cost_usd) }),
      el('div', { class: 's-time', text: fmt.rel(s.last_epoch) }),
    ]),
  ]);
  return row;
}

// ---- view: session detail / replay ----------------------------------------
// pull the files a session touched straight from its loaded timeline — instant,
// client-side, and consistent with the server-side ask engine's heuristic.
function briefFromTimeline(timeline) {
  const EDIT = new Set(['Edit', 'Write', 'MultiEdit', 'NotebookEdit']);
  const READ = new Set(['Read', 'NotebookRead']);
  const files = new Map();
  const tools = new Map();
  let errors = 0;
  for (const m of timeline) for (const t of (m.tools || [])) {
    tools.set(t.name, (tools.get(t.name) || 0) + 1);
    if (t.is_error) errors++;
    const inp = t.input || {};
    for (const key of ['file_path', 'path', 'notebook_path']) {
      const v = inp[key];
      if (typeof v === 'string' && /\.[A-Za-z0-9]{1,8}$/.test(v.replace(/\\/g, '/'))) {
        const name = v.replace(/\\/g, '/').split('/').pop();
        const f = files.get(name) || { name, ops: new Set(), seq: m.seq };
        f.ops.add(EDIT.has(t.name) ? 'edit' : READ.has(t.name) ? 'read' : 'use');
        files.set(name, f);
      }
    }
  }
  return {
    files: [...files.values()].sort((a, b) => (b.ops.has('edit') - a.ops.has('edit'))),
    tools: [...tools.entries()].sort((a, b) => b[1] - a[1]),
    errors,
  };
}

async function viewSession(id, params = {}) {
  view().innerHTML = '<div class="loading"><div class="spinner"></div></div>';
  const s = await API.session(id);
  const root = el('div', { class: 'view-pad fade-in' });

  // header
  const star = el('button', { class: 'iconbtn' + (s.favorite ? ' on' : ''), title: 'Favorite', html: s.favorite ? '★' : '☆', onclick: async () => { const r = await API.state(id, { favorite: !s.favorite }); s.favorite = r.favorite; star.classList.toggle('on', r.favorite); star.innerHTML = r.favorite ? '★' : '☆'; toast(r.favorite ? 'Favorited' : 'Unfavorited'); } });
  const arch = el('button', { class: 'iconbtn' + (s.archived ? ' on' : ''), title: 'Archive', html: '🗄', onclick: async () => { const r = await API.state(id, { archived: !s.archived }); s.archived = r.archived; arch.classList.toggle('on', r.archived); toast(r.archived ? 'Archived' : 'Unarchived'); } });

  const askBtn = el('button', { class: 'btn-ghost accent', title: 'Ask the grounded companion about this session', onclick: () => go('ask', { session: id }) }, ['✦ Ask about this']);
  const expMd = el('button', { class: 'btn-ghost', title: 'Export to Markdown', onclick: () => { downloadFrom(API.exportUrl(id, 'md')); toast('Exported Markdown'); } }, ['⬇ .md']);
  const expHtml = el('button', { class: 'btn-ghost', title: 'Export to a standalone, shareable HTML file', onclick: () => { downloadFrom(API.exportUrl(id, 'html')); toast('Exported HTML'); } }, ['⬇ .html']);

  root.appendChild(el('div', { class: 'page-head' }, [
    el('a', { href: '#/sessions', class: 'btn-ghost', html: '← Sessions' }),
    el('div', { class: 'page-actions' }, [askBtn, expMd, expHtml, star, arch]),
  ]));

  const modelChips = (s.models || []).map((m) => { const f = family(m); return el('span', { class: `chip model-chip fam-${f}` }, [el('span', { class: 'dot' }), shortModel(m)]); });
  root.appendChild(el('div', { class: 'detail-head' }, [
    el('div', { class: 'detail-title', text: s.title || 'Untitled session' }),
    el('div', { class: 'detail-meta' }, [
      el('span', { html: `<span style="font-family:var(--mono);font-size:11px">${esc(s.project)}</span>` }),
      ...modelChips,
      el('span', { text: '·' }),
      el('span', { text: fmt.dt(s.first_epoch) }),
    ]),
    el('div', { class: 'detail-stats' }, [
      ds(fmt.num(s.msg_count), 'messages'),
      ds(fmt.num(s.user_msgs), 'prompts'),
      ds(fmt.num(s.tool_calls), 'tool calls'),
      ds(fmt.compact(s.input_tokens + s.output_tokens + s.cache_write + s.cache_read), 'tokens'),
      ds(fmt.cost(s.cost_usd), 'est. cost', 'accent'),
      ds(fmt.dur(s.duration_s), 'duration'),
    ]),
  ]));

  // at-a-glance brief — files touched + tools, computed from the timeline
  const brief = briefFromTimeline(s.timeline);
  if (brief.files.length || brief.tools.length) {
    const chips = brief.files.slice(0, 8).map((f) => el('span', {
      class: 'file-chip' + (f.ops.has('edit') ? ' edited' : ''),
      title: [...f.ops].join(' + '),
      onclick: () => { const t = thread.children[f.seq]; if (t) spotlight(t); },
    }, [el('span', { class: 'op', text: f.ops.has('edit') ? '✎' : '◌' }), f.name]));
    root.appendChild(el('div', { class: 'brief' }, [
      el('div', { class: 'brief-row' }, [
        el('span', { class: 'brief-k', text: 'Files' }),
        brief.files.length ? el('div', { class: 'brief-chips' }, chips)
          : el('span', { class: 'brief-empty', text: 'none touched' }),
      ]),
      el('div', { class: 'brief-row' }, [
        el('span', { class: 'brief-k', text: 'Tools' }),
        el('div', { class: 'brief-chips' }, brief.tools.slice(0, 6).map(([n, c]) =>
          el('span', { class: 'tag-pill', text: `${n} ×${c}` }))),
        brief.errors ? el('span', { class: 'brief-err', text: `${brief.errors} error${brief.errors > 1 ? 's' : ''}` }) : null,
      ].filter(Boolean)),
    ]));
  }

  // replay bar
  const replay = buildReplay(s.timeline);
  root.appendChild(replay.bar);
  root.appendChild(el('div', { class: 'replay-hint', html: '<kbd>J</kbd> / <kbd>K</kbd> step · <kbd>Home</kbd> / <kbd>End</kbd> jump · click the track to scrub' }));

  // thread — one node per message (keeps thread.children[i] aligned to seq, which
  // the scrubber, file chips and deep-links all rely on). Consecutive same-role
  // turns are flagged `group-cont` so CSS can merge them into one visual block.
  const thread = el('div', { class: 'thread' });
  let prevRole = null;
  s.timeline.forEach((m, i) => {
    thread.appendChild(turnNode(m, i, m.role === prevRole));
    prevRole = m.role;
  });
  root.appendChild(thread);
  replay.attach(thread);

  view().innerHTML = ''; view().appendChild(root);

  // keyboard navigation: a cursor that steps through messages independently of
  // the replay reveal state, so you can read a long transcript hands-on-keyboard.
  let cursor = -1;
  function moveCursor(to) {
    const n = thread.children.length;
    if (!n) return;
    to = Math.max(0, Math.min(n - 1, to));
    if (cursor >= 0 && thread.children[cursor]) thread.children[cursor].classList.remove('cursor');
    cursor = to;
    const node = thread.children[cursor];
    node.classList.add('cursor');
    node.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
  setViewKey((e) => {
    if (e.metaKey || e.ctrlKey || e.altKey || CMDK.open) return;
    const tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'select' || tag === 'textarea' || e.target.isContentEditable) return;
    const k = e.key;
    if (k === 'j' || k === 'PageDown') { e.preventDefault(); moveCursor(cursor < 0 ? 0 : cursor + 1); }
    else if (k === 'k' || k === 'PageUp') { e.preventDefault(); moveCursor(cursor < 0 ? 0 : cursor - 1); }
    else if (k === 'Home') { e.preventDefault(); moveCursor(0); }
    else if (k === 'End') { e.preventDefault(); moveCursor(thread.children.length - 1); }
  });

  // when arriving from a search result, light up the matched terms in the body
  if (params.q) markTerms(thread, params.q);

  // deep-link: scroll to and spotlight a specific message (from search / Ask)
  const seq = params.seq != null ? parseInt(params.seq, 10) : null;
  if (seq != null && !Number.isNaN(seq) && thread.children[seq]) {
    cursor = seq;  // keyboard nav continues from where the link landed
    requestAnimationFrame(() => spotlight(thread.children[seq]));
  }
}

// wrap occurrences of the query terms inside each message body with <mark.hit>.
// Operates on textContent then re-escapes, so it can never inject markup.
function markTerms(container, q) {
  const terms = String(q || '').split(/\s+/)
    .filter((t) => t.length > 1)
    .map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  if (!terms.length) return;
  const re = new RegExp('(' + terms.join('|') + ')', 'gi');
  container.querySelectorAll('.msg-text').forEach((node) => {
    const txt = node.textContent;
    if (!txt) return;
    const marked = esc(txt).replace(re, '<mark class="hit">$1</mark>');
    if (marked !== esc(txt)) node.innerHTML = marked;
  });
}

function spotlight(node) {
  node.scrollIntoView({ behavior: 'smooth', block: 'center' });
  node.classList.add('flash');
  setTimeout(() => node.classList.remove('flash'), 1600);
}

function ds(v, k, cls = '') { return el('div', { class: 'ds' }, [el('span', { class: 'v ' + cls, text: v }), el('span', { class: 'k', text: k })]); }

const TOOL_ICON = { Read: '📖', Edit: '✏️', Write: '📝', Bash: '⌨️', Grep: '🔎', Glob: '🗂', Task: '🤖', WebSearch: '🌐', WebFetch: '🌐', PowerShell: '⌨️' };
function turnNode(m, i, cont = false) {
  const isUser = m.role === 'user';
  const node = el('div', { class: `turn ${m.role} ${cont ? 'group-cont' : 'group-start'}`, 'data-i': i });
  node.appendChild(el('div', { class: 'avatar ' + (isUser ? 'user' : 'assistant'), text: isUser ? 'U' : 'C' }));
  const bubble = el('div', { class: 'bubble' });
  const who = el('div', { class: 'who' }, [el('b', { text: isUser ? 'You' : 'Claude' })]);
  if (m.model) who.appendChild(el('span', { class: 'usage-tag', text: shortModel(m.model) }));
  if (!isUser && (m.input_tokens || m.output_tokens)) who.appendChild(el('span', { class: 'usage-tag', text: `${fmt.compact(m.input_tokens)}→${fmt.compact(m.output_tokens)} tok` }));
  if (m.cost_usd) who.appendChild(el('span', { class: 'usage-tag', text: fmt.cost(m.cost_usd) }));
  bubble.appendChild(who);
  if (m.text) bubble.appendChild(el('div', { class: 'msg-text', text: m.text }));
  if (m.thinking) {
    bubble.appendChild(el('details', { class: 'thinking' }, [
      el('summary', { text: '✦ thinking' }),
      el('div', { class: 'tk', text: m.thinking }),
    ]));
  }
  (m.tools || []).forEach((t) => bubble.appendChild(toolCard(t)));
  node.appendChild(bubble);
  return node;
}

function toolCard(t) {
  const icon = TOOL_ICON[t.name] || '⚙️';
  const argText = Object.entries(t.input || {}).map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`).join('\n');
  return el('div', { class: 'tool-card' + (t.is_error ? ' error' : '') }, [
    el('div', { class: 'tool-head' }, [
      el('span', { class: 'ticon', text: icon }),
      el('span', { class: 'tname', text: t.name }),
      t.is_error ? el('span', { class: 'terr', text: '● error' }) : el('span', { class: 'tok', text: '● ok' }),
    ]),
    el('div', { class: 'tool-body' }, [
      argText ? el('div', { class: 'tool-arg', text: argText.slice(0, 1200) }) : null,
      t.result_preview ? el('div', { class: 'tool-result', text: t.result_preview.slice(0, 600) }) : null,
    ].filter(Boolean)),
  ]);
}

function buildReplay(timeline) {
  const n = timeline.length;
  let idx = n, playing = false, speed = 2, timer = null, threadEl = null;
  const restartBtn = el('button', { class: 'replay-btn', title: 'Restart', html: ICON.restart });
  const prevBtn = el('button', { class: 'replay-btn', title: 'Step back', html: ICON.prev });
  const playBtn = el('button', { class: 'play-btn', title: 'Play / pause', html: playIcon(false) });
  const nextBtn = el('button', { class: 'replay-btn', title: 'Step forward', html: ICON.next });
  const track = el('div', { class: 'replay-track' }, [el('div', { class: 'replay-prog' }), el('div', { class: 'replay-knob' })]);
  const pos = el('div', { class: 'replay-pos', text: `${n}/${n}` });
  const speeds = [1, 2, 4, 8].map((sp) => el('button', { class: sp === speed ? 'on' : '', text: sp + '×', onclick: () => { speed = sp; speeds.forEach((b) => b.classList.toggle('on', +b.textContent.replace('×', '') === sp)); } }));
  const bar = el('div', { class: 'replaybar' }, [restartBtn, prevBtn, playBtn, nextBtn, track, pos, el('div', { class: 'replay-speed' }, speeds)]);

  const prog = $('.replay-prog', track), knob = $('.replay-knob', track);
  function render() {
    if (!threadEl) return;
    const partial = idx < n;  // mid-replay → mark current turn; at end → full readable thread
    [...threadEl.children].forEach((c, i) => {
      c.classList.toggle('hidden-msg', i >= idx);
      c.classList.toggle('replay-current', partial && i === idx - 1);
    });
    const frac = n ? idx / n : 1;
    prog.style.width = (frac * 100) + '%'; knob.style.left = (frac * 100) + '%';
    pos.textContent = `${Math.min(idx, n)}/${n}`;
    prevBtn.disabled = idx <= 0; nextBtn.disabled = idx >= n;
  }
  function focusCurrent() {
    const target = threadEl && idx > 0 && idx <= n ? threadEl.children[idx - 1] : null;
    if (target) target.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
  function step() {
    if (idx >= n) { stop(); return; }
    idx++; render(); focusCurrent();
    const gap = timeline[idx - 1]?.gap_s || 0.5;
    const delay = Math.min(Math.max(gap, 0.3), 6) * 300 / speed;
    timer = setTimeout(step, delay);
  }
  function play() { if (idx >= n) { idx = 0; render(); } playing = true; playBtn.innerHTML = playIcon(true); step(); }
  function stop() { playing = false; playBtn.innerHTML = playIcon(false); clearTimeout(timer); }
  function seek(target) { stop(); idx = Math.max(0, Math.min(n, target)); render(); focusCurrent(); }
  playBtn.addEventListener('click', () => (playing ? stop() : play()));
  restartBtn.addEventListener('click', () => seek(0));
  prevBtn.addEventListener('click', () => seek(idx - 1));
  nextBtn.addEventListener('click', () => seek(idx + 1));
  track.addEventListener('click', (e) => { const rect = track.getBoundingClientRect(); seek(Math.round(((e.clientX - rect.left) / rect.width) * n)); });

  return { bar, attach(t) { threadEl = t; idx = n; render(); } };
}
const ICON = {
  restart: '<svg viewBox="0 0 24 24" width="15" height="15"><path d="M12 5V2L7 7l5 5V8a4 4 0 1 1-4 4H6a6 6 0 1 0 6-7z" fill="currentColor"/></svg>',
  prev: '<svg viewBox="0 0 24 24" width="15" height="15"><path d="M7 5h2v14H7zM19 5v14l-9-7z" fill="currentColor"/></svg>',
  next: '<svg viewBox="0 0 24 24" width="15" height="15"><path d="M15 5h2v14h-2zM5 5l9 7-9 7z" fill="currentColor"/></svg>',
};
function playIcon(playing) {
  return playing ? '<svg viewBox="0 0 24 24" width="16" height="16"><path d="M7 5h4v14H7zM13 5h4v14h-4z" fill="currentColor"/></svg>'
    : '<svg viewBox="0 0 24 24" width="16" height="16"><path d="M7 4l13 8-13 8z" fill="currentColor"/></svg>';
}

// ---- view: analytics ------------------------------------------------------
async function viewAnalytics() {
  view().innerHTML = '<div class="loading"><div class="spinner"></div></div>';
  const a = await API.analytics();
  const root = el('div', { class: 'view-pad fade-in' });
  root.appendChild(el('div', { class: 'page-head' }, [el('div', {}, [
    el('h1', { class: 'page-title', text: 'Analytics' }),
    el('div', { class: 'page-sub', text: 'Tokens, cost, models and tools across everything you have done' }),
  ])]));

  const stats = el('div', { class: 'stat-grid' });
  const cards = [
    ['Sessions', fmt.num(a.sessions), `${fmt.num(a.projects)} projects`],
    ['Messages', fmt.num(a.messages), `${fmt.num(a.tool_calls)} tool calls`],
    ['Tokens', fmt.compact(a.tokens), `${fmt.compact(a.cache_read)} from cache`],
    ['Est. spend', fmt.cost(a.cost_usd), 'public model prices', 'accent'],
    ['Time', fmt.dur(a.duration_s), 'summed session spans'],
  ];
  cards.forEach(([k, v, sub, cls]) => stats.appendChild(el('div', { class: 'stat' }, [
    el('div', { class: 'k', text: k }), el('div', { class: 'v ' + (cls || ''), text: v }), el('div', { class: 'sub', text: sub }),
  ])));
  root.appendChild(stats);

  if (a.unpriced_models && a.unpriced_models.length) {
    root.appendChild(el('div', { class: 'panel', style: 'margin-bottom:14px;border-color:var(--line-2)' }, [
      el('div', { class: 'page-sub', html: `⚠︎ ${a.unpriced_models.length} model(s) have no public price and are counted as $0: <code style="font-family:var(--mono)">${a.unpriced_models.map(esc).join(', ')}</code>` }),
    ]));
  }

  // models donut + legend, tools barlist
  const modelSegs = a.by_model.map((m) => ({ value: m.cost_usd || m.tokens || 1, color: FAM_COLOR[m.family] || '#6f7585', label: shortModel(m.model), ...m }));
  const top = el('div', { class: 'grid-2 wide' }, [
    el('div', { class: 'panel' }, [
      el('div', { class: 'panel-title', text: 'Cost by model' }),
      el('div', { class: 'donut-wrap' }, [
        el('div', { html: donut(modelSegs) }),
        el('div', { class: 'legend' }, modelSegs.slice(0, 8).map((m) => el('div', { class: 'legend-row' }, [
          el('span', { class: 'dot', style: `background:${m.color}` }),
          el('span', { class: 'lname', text: shortModel(m.model) }),
          el('span', { class: 'lval', text: fmt.cost(m.cost_usd) }),
        ]))),
      ]),
    ]),
    el('div', { class: 'panel' }, [
      el('div', { class: 'panel-title', text: 'Tool usage' }),
      barList(a.by_tool.map((t) => ({ label: t.name, value: t.calls, sub: t.errors ? `${t.errors} err` : '' })), { max: Math.max(1, ...a.by_tool.map((t) => t.calls)) }),
    ]),
  ]);
  root.appendChild(top);

  // daily activity area
  root.appendChild(el('div', { class: 'panel', style: 'margin-top:14px' }, [
    el('div', { class: 'panel-title' }, [el('span', { text: 'Daily activity' }), el('span', { class: 'usage-tag', text: `${a.daily.length} active days` })]),
    areaChart(a.daily, { h: 170, key: 'messages' }),
  ]));

  // heatmap + projects
  root.appendChild(el('div', { class: 'grid-2', style: 'margin-top:14px' }, [
    el('div', { class: 'panel' }, [el('div', { class: 'panel-title', text: 'When you work (weekday × hour)' }), heatmapNode(a.heatmap)]),
    el('div', { class: 'panel' }, [el('div', { class: 'panel-title', text: 'Busiest projects' }),
      barList(a.top_projects.map((p) => ({ label: p.project_name, value: p.sessions, sub: fmt.cost(p.cost_usd) })), { max: Math.max(1, ...a.top_projects.map((p) => p.sessions)) }),
    ]),
  ]));

  view().innerHTML = ''; view().appendChild(root);
}

function barList(rows, { max }) {
  return el('div', { class: 'barlist' }, rows.map((r) => el('div', { class: 'barrow' }, [
    el('span', { class: 'lbl', text: r.label }),
    el('div', { class: 'bartrack' }, [el('div', { class: 'barfill', style: `width:${Math.max(2, (r.value / max) * 100)}%` })]),
    el('span', { class: 'val', html: `${fmt.num(r.value)}${r.sub ? ` <small>${esc(r.sub)}</small>` : ''}` }),
  ])));
}

function heatmapNode(grid) {
  const days = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];
  const max = Math.max(1, ...grid.flat());
  const labels = el('div', { class: 'heat-labels' }, days.map((d) => el('span', { text: d })));
  const weeks = el('div', { class: 'heat-grid' }, grid.map((row) => el('div', { class: 'heat-week' }, row.map((v) => {
    const a = v ? 0.15 + 0.85 * (v / max) : 0;
    return el('div', { class: 'heat-cell', style: v ? `background:rgba(255,138,91,${a.toFixed(2)})` : '', title: `${v}` });
  }))));
  return el('div', {}, [
    el('div', { class: 'heatmap' }, [labels, weeks]),
    el('div', { class: 'heat-foot' }, [el('span', { text: '12am' }), el('span', { text: '6am' }), el('span', { text: '12pm' }), el('span', { text: '6pm' }), el('span', { text: '11pm' })]),
  ]);
}

// ---- view: projects -------------------------------------------------------
async function viewProjects() {
  view().innerHTML = '<div class="loading"><div class="spinner"></div></div>';
  const { projects } = await API.projects();
  const a = await API.analytics().catch(() => ({ daily: [] }));
  const root = el('div', { class: 'view-pad fade-in' });
  root.appendChild(el('div', { class: 'page-head' }, [el('div', {}, [
    el('h1', { class: 'page-title', text: 'Projects' }),
    el('div', { class: 'page-sub', text: `${projects.length} repositories Claude has touched` }),
  ])]));
  const grid = el('div', { class: 'proj-grid' });
  const maxC = Math.max(1, ...projects.map((p) => p.cost_usd));
  projects.forEach((p) => {
    grid.appendChild(el('div', { class: 'proj-card', onclick: () => { sessionsState.project = p.project; sessionsState.offset = 0; go('sessions', { project: p.project }); } }, [
      el('div', {}, [el('div', { class: 'proj-name', text: p.project_name || p.project }), el('div', { class: 'proj-path', text: p.project })]),
      el('div', { html: spark(new Array(12).fill(0).map((_, i) => (i + 1) * (p.sessions / 12) + Math.sin(i) * 2), { color: 'var(--accent)' }) }),
      el('div', { class: 'proj-row' }, [el('span', { text: 'Sessions' }), el('b', { text: fmt.num(p.sessions) })]),
      el('div', { class: 'proj-row' }, [el('span', { text: 'Messages' }), el('b', { text: fmt.num(p.messages) })]),
      el('div', { class: 'proj-row' }, [el('span', { text: 'Est. cost' }), el('b', { text: fmt.cost(p.cost_usd) })]),
      el('div', { class: 'proj-row' }, [el('span', { text: 'Last active' }), el('b', { text: fmt.rel(p.last_epoch) })]),
    ]));
  });
  root.appendChild(grid);
  view().innerHTML = ''; view().appendChild(root);
}

// ---- view: timeline -------------------------------------------------------
async function viewTimeline() {
  view().innerHTML = '<div class="loading"><div class="spinner"></div></div>';
  const a = await API.analytics();
  const root = el('div', { class: 'view-pad fade-in' });
  root.appendChild(el('div', { class: 'page-head' }, [el('div', {}, [
    el('h1', { class: 'page-title', text: 'Timeline' }),
    el('div', { class: 'page-sub', text: 'Your activity over time' }),
  ])]));

  root.appendChild(el('div', { class: 'panel' }, [
    el('div', { class: 'panel-title' }, [el('span', { text: 'Messages per day' }), el('span', { class: 'usage-tag', text: `${a.daily.length} days` })]),
    el('div', { class: 'tl-chart' }, [areaChart(a.daily, { h: 200, key: 'messages' })]),
  ]));
  root.appendChild(el('div', { class: 'panel', style: 'margin-top:14px' }, [
    el('div', { class: 'panel-title', text: 'Spend per day' }),
    el('div', { class: 'tl-chart' }, [areaChart(a.daily, { h: 200, key: 'cost_usd', color: 'var(--violet)' })]),
  ]));

  // month buckets
  const byMonth = {};
  a.daily.forEach((d) => { const mk = d.date.slice(0, 7); (byMonth[mk] = byMonth[mk] || { month: mk, sessions: 0, messages: 0, cost_usd: 0 }); byMonth[mk].sessions += d.sessions; byMonth[mk].messages += d.messages; byMonth[mk].cost_usd += d.cost_usd; });
  const months = Object.values(byMonth).sort((x, y) => y.month.localeCompare(x.month));
  const list = el('div', { class: 'session-list', style: 'margin-top:18px' });
  months.forEach((m) => {
    const dt = new Date(m.month + '-01').toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
    list.appendChild(el('div', { class: 's-row', style: 'cursor:default' }, [
      el('div', { class: 's-main' }, [el('div', { class: 's-title' }, [el('span', { class: 'txt', text: dt })])]),
      el('div', { class: 's-side' }, [el('div', { class: 's-stats' }, [
        el('span', { html: `<b>${fmt.num(m.sessions)}</b> sessions` }),
        el('span', { html: `<b>${fmt.num(m.messages)}</b> msgs` }),
      ]), el('div', { class: 's-cost', text: fmt.cost(m.cost_usd) })]),
    ]));
  });
  root.appendChild(list);
  view().innerHTML = ''; view().appendChild(root);
}

// ---- view: compare --------------------------------------------------------
async function viewCompare(params) {
  const root = el('div', { class: 'view-pad fade-in' });
  root.appendChild(el('div', { class: 'page-head' }, [el('div', {}, [
    el('h1', { class: 'page-title', text: 'Compare' }),
    el('div', { class: 'page-sub', text: 'Put two sessions side by side' }),
  ])]));
  view().innerHTML = ''; view().appendChild(root);

  const recent = (await API.sessions({ sort: 'recent', limit: 200 })).sessions;
  let a = params.a || '', b = params.b || '';
  const out = el('div', {});
  const mkPicker = (which, val) => {
    const sel = el('select', { class: 'input', style: 'width:100%' }, [el('option', { value: '', text: `Choose session ${which}…` }), ...recent.map((s) => el('option', { value: s.session_id, text: `${(s.title || 'Untitled').slice(0, 50)} · ${s.project_name}`, ...(s.session_id === val ? { selected: 'selected' } : {}) }))]);
    sel.addEventListener('change', (e) => { if (which === 'A') a = e.target.value; else b = e.target.value; draw(); });
    return sel;
  };
  root.appendChild(el('div', { class: 'cmp-pick' }, [mkPicker('A', a), mkPicker('B', b)]));
  root.appendChild(out);

  async function draw() {
    if (!a || !b) { out.innerHTML = '<div class="empty">Pick two sessions to compare.</div>'; return; }
    out.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
    const data = await API.compare(a, b);
    const A = data.a, B = data.b;
    if (!A || !B) { out.innerHTML = '<div class="empty">Session not found.</div>'; return; }
    const rows = [
      ['Messages', A.msg_count, B.msg_count], ['Prompts', A.user_msgs, B.user_msgs],
      ['Tool calls', A.tool_calls, B.tool_calls], ['Input tokens', A.input_tokens, B.input_tokens],
      ['Output tokens', A.output_tokens, B.output_tokens], ['Cache read', A.cache_read, B.cache_read],
      ['Est. cost', A.cost_usd, B.cost_usd, true], ['Duration (s)', A.duration_s, B.duration_s],
    ];
    const panel = el('div', { class: 'panel' }, [
      el('div', { class: 'cmp-row' }, [el('span', { class: 'k', text: '' }), el('span', { class: 'a', text: (A.title || '').slice(0, 28) }), el('span', { class: 'b', text: (B.title || '').slice(0, 28) }), el('span', {})]),
      ...rows.map(([k, av, bv, money]) => {
        const fmtv = money ? fmt.cost : fmt.num;
        return el('div', { class: 'cmp-row' }, [
          el('span', { class: 'k', text: k }),
          el('span', { class: 'a' + (av > bv ? ' win' : ''), text: fmtv(av) }),
          el('span', { class: 'b' + (bv > av ? ' win' : ''), text: fmtv(bv) }),
          el('span', { class: 'usage-tag', text: av === bv ? '=' : (av > bv ? '◀' : '▶') }),
        ]);
      }),
    ]);
    out.innerHTML = ''; out.appendChild(panel);
  }
  draw();
}

// ---- view: wrapped --------------------------------------------------------
async function viewWrapped(params) {
  view().innerHTML = '<div class="loading"><div class="spinner"></div></div>';
  const data = await API.wrapped(params.year);
  const root = el('div', { class: 'view-pad fade-in' });
  root.appendChild(el('div', { class: 'page-head', style: 'justify-content:center' }, [el('div', { style: 'text-align:center' }, [
    el('h1', { class: 'page-title', text: '✦ Claude Wrapped' }),
    el('div', { class: 'page-sub', text: data.label }),
  ])]));

  const yrs = el('div', { class: 'wrap-year' }, [
    el('button', { class: 'chip toggle' + (!params.year ? ' on' : ''), text: 'All time', onclick: () => go('wrapped') }),
    ...data.available_years.map((y) => el('button', { class: 'chip toggle' + (String(y) === String(params.year) ? ' on' : ''), text: String(y), onclick: () => go('wrapped', { year: y }) })),
  ]);
  root.appendChild(yrs);

  const wrap = el('div', { class: 'wrapped' });
  const stage = el('div', { class: 'wrap-stage' });
  const cards = data.cards.map((c, i) => el('div', { class: 'wrap-card' + (i === 0 ? ' show' : '') }, [
    el('div', { class: 'icn', text: c.icon }),
    el('div', { class: 'big', text: c.value }),
    el('div', { class: 'lab', text: c.label }),
    el('div', { class: 'sub', text: c.sub }),
  ]));
  cards.forEach((c) => stage.appendChild(c));
  const dots = el('div', { class: 'wrap-dots' }, cards.map((_, i) => el('span', { class: i === 0 ? 'on' : '', onclick: () => show(i) })));
  let cur = 0;
  function show(i) { cur = (i + cards.length) % cards.length; cards.forEach((c, j) => c.classList.toggle('show', j === cur)); $$('.wrap-dots span', wrap).forEach((d, j) => d.classList.toggle('on', j === cur)); }
  const nav = el('div', { class: 'wrap-nav' }, [
    el('button', { text: '← Back', onclick: () => show(cur - 1) }),
    el('button', { text: 'Copy summary', onclick: () => { const txt = `My Claude Code — ${data.label}\n` + data.cards.map((c) => `${c.icon} ${c.value} — ${c.label}`).join('\n'); navigator.clipboard?.writeText(txt).then(() => toast('Summary copied')); } }),
    el('button', { text: '⬇ Save card', title: 'Download a shareable PNG', onclick: () => saveWrappedCard(data) }),
    el('button', { text: 'Next →', onclick: () => show(cur + 1) }),
  ]);
  wrap.append(stage, dots, nav);
  root.appendChild(wrap);
  view().innerHTML = ''; view().appendChild(root);
  let auto = setInterval(() => show(cur + 1), 4200);
  stage.addEventListener('click', () => { clearInterval(auto); show(cur + 1); });
}

// render the Wrapped highlights to a shareable PNG (pure canvas, no deps)
function saveWrappedCard(data) {
  const W = 1080, H = 1350, FONT = "'Segoe UI', system-ui, -apple-system, Roboto, sans-serif";
  const cv = document.createElement('canvas');
  cv.width = W; cv.height = H;
  const x = cv.getContext('2d');

  const bg = x.createLinearGradient(0, 0, W, H);
  bg.addColorStop(0, '#16131b'); bg.addColorStop(1, '#0d0e12');
  x.fillStyle = bg; x.fillRect(0, 0, W, H);
  const glow = x.createRadialGradient(W / 2, 220, 30, W / 2, 220, 680);
  glow.addColorStop(0, 'rgba(255,138,91,0.22)'); glow.addColorStop(1, 'rgba(255,138,91,0)');
  x.fillStyle = glow; x.fillRect(0, 0, W, H);

  x.textAlign = 'center';
  x.fillStyle = '#ff8a5b';
  x.font = '700 38px ' + FONT;
  x.fillText('✦  CLAUDE WRAPPED', W / 2, 150);
  x.fillStyle = '#e7e9ee';
  x.font = '800 78px ' + FONT;
  x.fillText(String(data.label || 'All time'), W / 2, 248);

  const rr = (px, py, pw, ph, r) => {
    x.beginPath();
    x.moveTo(px + r, py);
    x.arcTo(px + pw, py, px + pw, py + ph, r);
    x.arcTo(px + pw, py + ph, px, py + ph, r);
    x.arcTo(px, py + ph, px, py, r);
    x.arcTo(px, py, px + pw, py, r);
    x.closePath();
  };

  const picks = (data.cards || []).slice(0, 6);
  const M = 60, GAP = 36, COLS = 2;
  const cw = (W - M * 2 - GAP * (COLS - 1)) / COLS;
  const ch = 250, top = 330;
  picks.forEach((c, i) => {
    const col = i % COLS, row = (i / COLS) | 0;
    const px = M + col * (cw + GAP), py = top + row * (ch + GAP);
    rr(px, py, cw, ch, 26);
    x.fillStyle = 'rgba(255,255,255,0.035)'; x.fill();
    x.lineWidth = 1.5; x.strokeStyle = 'rgba(255,255,255,0.07)'; x.stroke();
    x.textAlign = 'center';
    x.font = '38px ' + FONT;
    x.fillStyle = '#fff';
    x.fillText(String(c.icon || ''), px + cw / 2, py + 78);
    x.fillStyle = '#e7e9ee';
    let v = String(c.value || ''); x.font = '800 56px ' + FONT;
    while (x.measureText(v).width > cw - 48 && v.length > 6) { v = v.slice(0, -2) + '…'; }
    x.fillText(v, px + cw / 2, py + 150);
    x.fillStyle = '#aab0bd';
    x.font = '500 24px ' + FONT;
    let lab = String(c.label || '');
    while (x.measureText(lab).width > cw - 40 && lab.length > 6) { lab = lab.slice(0, -2) + '…'; }
    x.fillText(lab, px + cw / 2, py + 196);
  });

  x.textAlign = 'center';
  x.fillStyle = '#9a8cff';
  x.font = '700 30px ' + FONT;
  x.fillText('ClaudeStudio', W / 2, H - 86);
  x.fillStyle = '#727887';
  x.font = '400 22px ' + FONT;
  x.fillText('the desktop workspace for Claude Code · 100% local', W / 2, H - 48);

  cv.toBlob((blob) => {
    if (!blob) { toast('Could not render card', 'err'); return; }
    const url = URL.createObjectURL(blob);
    const a = el('a', { href: url, download: `claude-wrapped-${String(data.label || 'alltime').replace(/\s+/g, '-').toLowerCase()}.png` });
    document.body.appendChild(a); a.click();
    setTimeout(() => { a.remove(); URL.revokeObjectURL(url); }, 100);
    toast('Saved Wrapped card');
  }, 'image/png');
}

// ---- view: search (full page) ---------------------------------------------
const KINDS = [['', 'Any role'], ['user', 'Prompts'], ['assistant', 'Responses'], ['tool', 'Tool calls']];
async function viewSearch(params) {
  const root = el('div', { class: 'view-pad fade-in' });
  const sub = el('div', { class: 'page-sub', text: 'Search every prompt, response, and tool call' });
  root.appendChild(el('div', { class: 'page-head' }, [el('div', {}, [
    el('h1', { class: 'page-title', text: 'Search' }), sub,
  ])]));

  // ---- controls (text + structured filters the backend already understands) --
  const qBox = el('div', { class: 'search-box' }, [
    el('span', { html: '<svg viewBox="0 0 24 24" width="15" height="15"><path d="M21 21l-4.3-4.3M19 11a8 8 0 1 1-16 0 8 8 0 0 1 16 0z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>' }),
    el('input', { class: 'input', placeholder: 'Search prompts, responses, tool calls…', value: params.q || '', autocomplete: 'off' }),
  ]);
  const qInput = $('input', qBox);
  const kindSel = el('select', { class: 'input' }, KINDS.map(([v, l]) => el('option', { value: v, text: l, ...(v === (params.kind || '') ? { selected: 'selected' } : {}) })));
  const projInput = el('input', { class: 'input', placeholder: 'Project…', value: params.project || '', style: 'width:150px' });
  const sinceInput = el('input', { class: 'input', type: 'date', title: 'On or after', value: params.since || '' });
  const untilInput = el('input', { class: 'input', type: 'date', title: 'On or before', value: params.until || '' });
  const clearBtn = el('button', { class: 'chip toggle', title: 'Clear filters' }, ['Clear']);

  root.appendChild(el('div', { class: 'toolbar search-filters' }, [
    qBox, kindSel,
    el('div', { class: 'field' }, [el('span', { class: 'field-k', text: 'in' }), projInput]),
    el('div', { class: 'field' }, [el('span', { class: 'field-k', text: 'from' }), sinceInput, el('span', { class: 'field-k', text: 'to' }), untilInput]),
    clearBtn,
  ]));

  const list = el('div', { class: 'session-list' });
  root.appendChild(list);
  view().innerHTML = ''; view().appendChild(root);

  // run() fetches with the current control values and rewrites the URL *without*
  // a re-render (replaceState fires no hashchange), so the inputs keep focus and
  // the search stays shareable/bookmarkable.
  async function run() {
    const q = qInput.value.trim();
    const filters = { kind: kindSel.value, project: projInput.value.trim(), since: sinceInput.value, until: untilInput.value };
    const qs = new URLSearchParams();
    if (q) qs.set('q', q);
    for (const k of ['kind', 'project', 'since', 'until']) if (filters[k]) qs.set(k, filters[k]);
    history.replaceState(null, '', '#/search' + (qs.toString() ? '?' + qs : ''));

    if (!q) {
      sub.textContent = 'Search every prompt, response, and tool call';
      list.innerHTML = ''; list.appendChild(el('div', { class: 'empty', text: 'Type to search your sessions…' }));
      return;
    }
    list.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
    let res;
    try { res = await API.search(q, 80, filters); }
    catch (e) { list.innerHTML = ''; list.appendChild(el('div', { class: 'empty', text: 'Search failed: ' + (e.message || e) })); return; }
    const n = res.results.length;
    sub.innerHTML = `<b>${n}</b> match${n === 1 ? '' : 'es'} for <b>${esc(q)}</b>`;
    list.innerHTML = '';
    if (!n) { list.appendChild(el('div', { class: 'empty' }, [el('div', { class: 'big', text: 'No matches' }), el('div', { text: 'Try fewer words or relax the filters.' })])); return; }
    res.results.forEach((r) => list.appendChild(searchResultRow(r, q)));
  }

  let t;
  const debounced = () => { clearTimeout(t); t = setTimeout(run, 200); };
  qInput.addEventListener('input', debounced);
  projInput.addEventListener('input', debounced);
  kindSel.addEventListener('change', run);
  sinceInput.addEventListener('change', run);
  untilInput.addEventListener('change', run);
  clearBtn.addEventListener('click', () => { kindSel.value = ''; projInput.value = ''; sinceInput.value = ''; untilInput.value = ''; run(); qInput.focus(); });

  qInput.focus();
  run();
}

function searchResultRow(r, q) {
  const snip = esc(r.snip || '').replace(/⟦/g, '<mark>').replace(/⟧/g, '</mark>');
  const params = {};
  if (r.seq != null) params.seq = r.seq;
  if (q) params.q = q;  // carry the query so the replay view highlights it too
  return el('div', { class: 's-row', onclick: () => go('session/' + r.session_id, params) }, [
    el('div', { class: 's-main' }, [
      el('div', { class: 's-title' }, [el('span', { class: 'txt', text: r.title || 'Untitled' })]),
      el('div', { class: 's-preview snip', html: snip }),
      el('div', { class: 's-meta' }, [el('span', { class: 'proj-chip', text: r.project_name }), el('span', { class: 'tag-pill', text: r.kind })]),
    ]),
    el('div', { class: 's-side' }, [el('div', { class: 's-time', text: fmt.rel(r.last_epoch) })]),
  ]);
}

// ---- view: ask (grounded local companion) ---------------------------------
async function viewAsk(params) {
  const sessionScope = params.session || '';
  let scopeTitle = '';
  if (sessionScope) {
    try { scopeTitle = (await API.session(sessionScope)).title || sessionScope.slice(0, 8); } catch { scopeTitle = sessionScope.slice(0, 8); }
  }
  const root = el('div', { class: 'view-pad fade-in ask-view' });
  root.appendChild(el('div', { class: 'page-head' }, [
    el('div', {}, [
      el('h1', { class: 'page-title', html: 'Ask <span class="ask-badge">grounded · local</span>' }),
      el('div', { class: 'page-sub', text: 'Ask your Claude Code history anything. Every answer is computed from your real sessions on this machine — no model calls, nothing uploaded.' }),
    ]),
  ]));

  if (sessionScope) {
    root.appendChild(el('div', { class: 'ask-scope' }, [
      el('span', { text: 'Scoped to ' }),
      el('b', { text: scopeTitle }),
      el('button', { class: 'x', title: 'Clear scope', onclick: () => go('ask') }, ['×']),
    ]));
  }

  const thread = el('div', { class: 'ask-thread' });
  root.appendChild(thread);

  const sugWrap = el('div', { class: 'ask-suggest' });
  root.appendChild(sugWrap);

  const input = el('input', { class: 'input', placeholder: sessionScope ? 'Ask about this session…' : 'Ask about your sessions, files, cost, what to reopen…', autocomplete: 'off' });
  const sendBtn = el('button', { class: 'btn-send', title: 'Ask', html: '✦ Ask' });
  const composer = el('div', { class: 'ask-composer' }, [input, sendBtn]);
  root.appendChild(composer);
  const syncSend = () => { sendBtn.disabled = !input.value.trim(); };
  input.addEventListener('input', syncSend); syncSend();

  function renderSuggestions(list) {
    sugWrap.innerHTML = '';
    if (!list || !list.length) return;
    sugWrap.appendChild(el('span', { class: 'ask-suggest-lbl', text: 'Try' }));
    list.forEach((q) => sugWrap.appendChild(el('button', { class: 'chip toggle', onclick: () => submit(q) }, [q])));
  }
  renderSuggestions(sessionScope
    ? ['What happened in this session?', 'Give me a handoff brief', 'Which files changed?', 'What are the most important tool calls?']
    : ['What should I reopen next?', 'Summarize my most recent session', 'Where did the tokens go?', 'Give me a handoff brief']);

  let busy = false;
  async function submit(q) {
    q = (q || input.value).trim();
    if (!q || busy) return;
    busy = true; input.value = '';
    thread.appendChild(el('div', { class: 'ask-q' }, [el('span', { class: 'ask-q-txt', text: q })]));
    const loading = el('div', { class: 'ask-a loading-a' }, [el('div', { class: 'spinner sm' })]);
    thread.appendChild(loading);
    loading.scrollIntoView({ behavior: 'smooth', block: 'end' });
    try {
      const ans = await API.ask(q, sessionScope || undefined);
      loading.replaceWith(answerCard(ans));
      if (ans.suggestions) renderSuggestions(ans.suggestions);
    } catch (e) {
      loading.replaceWith(el('div', { class: 'ask-a' }, [el('div', { class: 'ask-a-body' }, [el('div', { class: 'empty', text: 'Could not answer: ' + (e.message || e) })])]));
    } finally {
      busy = false;
      thread.lastChild.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      input.focus();
    }
  }
  sendBtn.addEventListener('click', () => submit());
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); submit(); } });

  view().innerHTML = ''; view().appendChild(root);
  input.focus();
  if (params.q) submit(params.q);
}

function answerCard(ans) {
  const card = el('div', { class: 'ask-a' });
  const body = el('div', { class: 'ask-a-body' });
  body.appendChild(el('div', { class: 'ask-a-title', text: ans.title || 'Answer' }));
  (ans.blocks || []).forEach((b) => { const n = askBlock(b); if (n) body.appendChild(n); });
  body.appendChild(el('div', { class: 'ask-a-foot' }, [
    el('span', { class: 'shield', html: '<svg viewBox="0 0 24 24" width="11" height="11"><path d="M12 2l8 4v6c0 5-3.4 8.5-8 10-4.6-1.5-8-5-8-10V6l8-4z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>' }),
    el('span', { text: ans.grounding || 'Computed locally · no model calls.' }),
  ]));
  card.appendChild(body);
  return card;
}

function askBlock(b) {
  if (b.type === 'stats') {
    return el('div', { class: 'ask-stats' }, (b.items || []).map((it) =>
      el('div', { class: 'ask-stat' + (it.tone === 'bad' && it.value ? ' bad' : '') }, [
        el('span', { class: 'v' + (it.accent ? ' accent' : ''), text: String(it.value) }),
        el('span', { class: 'k', text: it.label }),
      ])));
  }
  if (b.type === 'text') {
    return el('div', { class: 'ask-text-block' }, [
      b.label ? el('div', { class: 'ask-lbl', text: b.label }) : null,
      el('div', { class: 'ask-text', text: b.text || '' }),
    ].filter(Boolean));
  }
  if (b.type === 'list' || b.type === 'steps') {
    return el('div', { class: 'ask-text-block' }, [
      b.label ? el('div', { class: 'ask-lbl', text: b.label }) : null,
      el('ul', { class: b.type === 'steps' ? 'ask-steps' : 'ask-list' }, (b.items || []).map((x) => el('li', { text: x }))),
    ].filter(Boolean));
  }
  if (b.type === 'files') {
    return el('div', { class: 'ask-text-block' }, [
      b.label ? el('div', { class: 'ask-lbl', text: b.label }) : null,
      el('div', { class: 'ask-files' }, (b.items || []).map((f) => el('div', {
        class: 'ask-file' + (f.edited ? ' edited' : ''),
        title: (f.ops || []).join(' + '),
        onclick: () => f.session_id && go('session/' + f.session_id, f.seq != null ? { seq: f.seq } : {}),
      }, [
        el('span', { class: 'op', text: f.edited ? '✎' : '◌' }),
        el('span', { class: 'fname', text: f.name || f.path }),
        f.count ? el('span', { class: 'cnt', text: '×' + f.count }) : null,
        f.errors ? el('span', { class: 'ferr', text: f.errors + ' err' }) : null,
      ].filter(Boolean)))),
    ].filter(Boolean));
  }
  if (b.type === 'decisions') {
    return el('div', { class: 'ask-text-block' }, [
      b.label ? el('div', { class: 'ask-lbl', text: b.label }) : null,
      el('div', { class: 'ask-decisions' }, (b.items || []).map((d) => el('div', {
        class: 'ask-decision' + (d.session_id ? ' link' : ''),
        onclick: () => d.session_id && go('session/' + d.session_id, d.seq != null ? { seq: d.seq } : {}),
      }, [el('span', { class: 'q', text: '“' }), el('span', { class: 'd-txt', text: d.text }), d.session_id ? el('span', { class: 'jump', text: '↗' }) : null].filter(Boolean)))),
    ].filter(Boolean));
  }
  if (b.type === 'sessions') {
    return el('div', { class: 'ask-text-block' }, [
      b.label ? el('div', { class: 'ask-lbl', text: b.label }) : null,
      el('div', { class: 'ask-sessions' }, (b.items || []).map((s) => el('div', {
        class: 'ask-session', onclick: () => go('session/' + s.session_id, s.seq != null ? { seq: s.seq } : {}),
      }, [
        el('div', { class: 'as-main' }, [
          el('div', { class: 'as-title', text: s.title || 'Untitled' }),
          el('div', { class: 'as-reason', text: s.reason || '' }),
        ]),
        el('div', { class: 'as-side' }, [
          el('span', { class: 'proj-chip', text: s.project_name || '' }),
          s.last_epoch ? el('span', { class: 's-time', text: fmt.rel(s.last_epoch) }) : null,
        ].filter(Boolean)),
      ]))),
    ].filter(Boolean));
  }
  if (b.type === 'compare') {
    return el('div', { class: 'panel', style: 'margin-top:4px' }, [
      el('div', { class: 'cmp-row' }, [el('span', { class: 'k', text: '' }), el('span', { class: 'a', text: (b.a || '').slice(0, 26) }), el('span', { class: 'b', text: (b.b || '').slice(0, 26) }), el('span', {})]),
      ...(b.rows || []).map((r) => {
        const f = r.money ? fmt.cost : fmt.num;
        return el('div', { class: 'cmp-row' }, [
          el('span', { class: 'k', text: r.label }),
          el('span', { class: 'a' + (r.a > r.b ? ' win' : ''), text: f(r.a) }),
          el('span', { class: 'b' + (r.b > r.a ? ' win' : ''), text: f(r.b) }),
          el('span', { class: 'usage-tag', text: r.a === r.b ? '=' : (r.a > r.b ? '◀' : '▶') }),
        ]);
      }),
    ]);
  }
  return null;
}

// ---- command palette ------------------------------------------------------
const CMDK = { open: false, sel: 0, items: [] };
function openCmdk() {
  CMDK.open = true; $('#cmdk').hidden = false; const inp = $('#cmdk-input'); inp.value = ''; inp.focus(); cmdkRender('');
}
function closeCmdk() { CMDK.open = false; $('#cmdk').hidden = true; }
let cmdkTimer;
async function cmdkRender(q) {
  const navCmds = NAV.filter((n) => !q || n.label.toLowerCase().includes(q.toLowerCase())).map((n) => ({ type: 'nav', label: 'Go to ' + n.label, route: n.id, icon: '→' }));
  let results = [];
  if (q && q.length >= 2) {
    try { const r = await API.search(q, 18); results = r.results.map((x) => ({ type: 'result', label: x.title || 'Untitled', snip: x.snip, session: x.session_id, seq: x.seq, project: x.project_name, q, icon: '◷' })); } catch { /* ignore */ }
  }
  CMDK.items = [...navCmds, ...results];
  CMDK.sel = 0;
  const box = $('#cmdk-results'); box.innerHTML = '';
  if (navCmds.length) { box.appendChild(el('div', { class: 'cmdk-group', text: 'Navigate' })); navCmds.forEach((it, i) => box.appendChild(cmdkItem(it, i))); }
  if (results.length) { box.appendChild(el('div', { class: 'cmdk-group', text: 'Sessions' })); results.forEach((it, i) => box.appendChild(cmdkItem(it, navCmds.length + i))); }
  if (q && q.length >= 2) box.appendChild(el('div', { class: 'cmdk-item', onclick: () => { closeCmdk(); go('search', { q }); } }, [el('span', { class: 'ico', text: '⏎' }), el('div', { class: 't' }, [el('div', { class: 'main', html: `See all results for <b>${esc(q)}</b>` })])]));
  if (!CMDK.items.length && (!q || q.length < 2)) box.appendChild(el('div', { class: 'empty', style: 'padding:30px', text: 'Type to search your sessions…' }));
  updateSel();
}
function cmdkItem(it, i) {
  const snip = it.snip ? esc(it.snip).replace(/⟦/g, '<mark>').replace(/⟧/g, '</mark>') : (it.project || '');
  return el('div', { class: 'cmdk-item', 'data-i': i, onclick: () => runCmdk(it) }, [
    el('span', { class: 'ico', text: it.icon }),
    el('div', { class: 't' }, [el('div', { class: 'main', text: it.label }), snip ? el('div', { class: 'snip', html: snip }) : null].filter(Boolean)),
  ]);
}
function updateSel() { $$('#cmdk-results .cmdk-item').forEach((n) => n.classList.toggle('sel', +n.dataset.i === CMDK.sel)); }
function runCmdk(it) {
  closeCmdk();
  if (it.type === 'nav') return go(it.route);
  if (it.type === 'result') {
    const p = {};
    if (it.seq != null) p.seq = it.seq;
    if (it.q) p.q = it.q;
    go('session/' + it.session, p);
  }
}

// ---- reindex --------------------------------------------------------------
async function doReindex() {
  const btn = $('#btn-reindex'); btn.classList.add('spinning');
  try { const s = await API.reindex(); toast(`Synced — +${s.added} new, ${s.updated} updated`); await loadSummary(); router(); }
  catch (e) { toast('Sync failed: ' + e.message, 'err'); }
  finally { btn.classList.remove('spinning'); }
}

async function loadSummary() {
  try { STATE.summary = await API.summary(); renderNav(STATE.summary); renderFootStats(STATE.summary); }
  catch (e) { /* ignore */ }
}

// ---- boot -----------------------------------------------------------------
window.__cs = { go, ss: () => sessionsState };
window.addEventListener('hashchange', router);
document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); CMDK.open ? closeCmdk() : openCmdk(); return; }
  if (!CMDK.open) return;
  if (e.key === 'Escape') closeCmdk();
  else if (e.key === 'ArrowDown') { e.preventDefault(); CMDK.sel = Math.min(CMDK.items.length - 1, CMDK.sel + 1); updateSel(); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); CMDK.sel = Math.max(0, CMDK.sel - 1); updateSel(); }
  else if (e.key === 'Enter') { const it = CMDK.items[CMDK.sel]; if (it) runCmdk(it); else { const q = $('#cmdk-input').value; if (q) { closeCmdk(); go('search', { q }); } } }
});

(async function boot() {
  $('#cmdk-trigger').addEventListener('click', openCmdk);
  $('#btn-reindex').addEventListener('click', doReindex);
  $('#cmdk').addEventListener('click', (e) => { if (e.target.id === 'cmdk') closeCmdk(); });
  $('#cmdk-input').addEventListener('input', (e) => { clearTimeout(cmdkTimer); cmdkTimer = setTimeout(() => cmdkRender(e.target.value.trim()), 160); });
  renderNav(null);
  await loadSummary();
  if (!location.hash) location.hash = '#/sessions';
  router();
})();
