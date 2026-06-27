/* ============================================================================
   ClaudeStudio — first-run guided tour (v0.6.3)
   A tiny, zero-dependency step-by-step overlay. No library: it builds its own
   spotlight + tooltip, walks the five onboarding steps from the server, and
   records completion in the `preferences` table (key `tour_completed`).
   Replay any time with the `?tour=1` URL param.
   ========================================================================== */
'use strict';

const Tour = (() => {
  let steps = [];
  let idx = 0;
  let overlay = null;

  function tourEl(tag, attrs = {}, kids = []) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === 'class') n.className = v;
      else if (k === 'text') n.textContent = v;
      else if (k.startsWith('on') && typeof v === 'function') n.addEventListener(k.slice(2), v);
      else if (v != null) n.setAttribute(k, v);
    }
    for (const c of [].concat(kids)) if (c != null) n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    return n;
  }

  async function start(force) {
    let status = {};
    try { status = await fetch('/api/onboarding/status').then((r) => r.json()); } catch { /* offline → still allow forced replay */ }
    if (!force && status && status.tour_completed) return;
    try {
      const data = await fetch('/api/onboarding/tour').then((r) => r.json());
      steps = (data && data.steps) || [];
    } catch { steps = []; }
    if (!steps.length) return;
    idx = 0;
    render();
  }

  function teardown() {
    if (overlay) { overlay.remove(); overlay = null; }
    document.removeEventListener('keydown', onKey);
  }

  async function complete() {
    teardown();
    try {
      await fetch('/api/preferences', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tour_completed: 1 }),
      });
    } catch { /* best-effort */ }
  }

  function onKey(e) {
    if (e.key === 'Escape') { e.preventDefault(); complete(); }
    else if (e.key === 'ArrowRight' || e.key === 'Enter') { e.preventDefault(); next(); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); prev(); }
  }

  function next() { if (idx >= steps.length - 1) return complete(); idx += 1; render(); }
  function prev() { if (idx > 0) { idx -= 1; render(); } }

  function render() {
    teardownOverlayOnly();
    const step = steps[idx];
    overlay = tourEl('div', { class: 'tour-overlay', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Guided tour' });

    // spotlight the target element, if any
    let rect = null;
    if (step.target) {
      const target = document.querySelector(step.target);
      if (target) {
        rect = target.getBoundingClientRect();
        const spot = tourEl('div', { class: 'tour-spotlight' });
        spot.style.left = (rect.left - 8) + 'px';
        spot.style.top = (rect.top - 8) + 'px';
        spot.style.width = (rect.width + 16) + 'px';
        spot.style.height = (rect.height + 16) + 'px';
        overlay.appendChild(spot);
      }
    }

    const card = tourEl('div', { class: 'tour-card' }, [
      tourEl('div', { class: 'tour-step', text: `Step ${idx + 1} of ${steps.length}` }),
      tourEl('h3', { class: 'tour-title', text: step.title }),
      tourEl('p', { class: 'tour-body', text: step.body }),
      tourEl('div', { class: 'tour-dots' }, steps.map((_, i) =>
        tourEl('span', { class: 'tour-dot' + (i === idx ? ' on' : '') }))),
      tourEl('div', { class: 'tour-actions' }, [
        tourEl('button', { class: 'tour-skip', text: 'Skip', onclick: complete }),
        idx > 0 ? tourEl('button', { class: 'tour-back', text: 'Back', onclick: prev }) : null,
        tourEl('button', { class: 'tour-next', text: step.cta || (idx === steps.length - 1 ? 'Done' : 'Next'), onclick: next }),
      ]),
    ]);

    // place the card near the spotlight, else centre it
    if (rect) {
      const below = rect.bottom + 16;
      card.style.position = 'fixed';
      card.style.top = Math.min(below, window.innerHeight - 240) + 'px';
      card.style.left = Math.min(Math.max(12, rect.left), window.innerWidth - 360) + 'px';
    } else {
      card.classList.add('tour-card-center');
    }
    overlay.appendChild(card);
    document.body.appendChild(overlay);
    document.addEventListener('keydown', onKey);
    const nx = card.querySelector('.tour-next');
    if (nx) nx.focus();
  }

  function teardownOverlayOnly() {
    if (overlay) { overlay.remove(); overlay = null; }
  }

  // boot: replay if ?tour=1, otherwise show only on a fresh (untoured) install
  function boot() {
    const force = new URLSearchParams(location.search).get('tour') === '1';
    // give the SPA a beat to render its nav so targets exist
    setTimeout(() => start(force), force ? 200 : 900);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();

  return { start, replay: () => start(true) };
})();

window.Tour = Tour;
