// ThemeManager — dark / light / system / high-contrast themes for ClaudeStudio.
//
// Themes are expressed as CSS custom-property overrides injected into :root, so
// the whole SPA restyles instantly with no reflow of structure. The choice is
// persisted to localStorage under 'cs:theme' and mirrored to the server
// (/api/preferences) so it follows a synced index across machines. 'system'
// tracks prefers-color-scheme live. Keyboard shortcut `T` cycles themes.
//
// Public API: ThemeManager.init(), ThemeManager.set(name), ThemeManager.get(),
//             ThemeManager.cycle().
(function (global) {
  'use strict';

  var STORAGE_KEY = 'cs:theme';
  var ORDER = ['dark', 'light', 'system', 'high-contrast'];

  // The 11 design tokens every view reads. 'system' resolves to dark/light.
  var THEMES = {
    dark: {
      '--cs-bg': '#0d0d14', '--cs-surface': '#16161f', '--cs-surface2': '#1d1d2a',
      '--cs-border': '#2a2a3a', '--cs-text': '#e7e9f3', '--cs-text-muted': '#9aa0b4',
      '--cs-accent': '#9a8cff', '--cs-accent-dim': '#6b5fd0', '--cs-danger': '#ff6b6b',
      '--cs-success': '#5ec98a', '--cs-warning': '#e6b450'
    },
    light: {
      '--cs-bg': '#f6f7fb', '--cs-surface': '#ffffff', '--cs-surface2': '#eef0f6',
      '--cs-border': '#d6d9e6', '--cs-text': '#1b1d2a', '--cs-text-muted': '#5b6076',
      '--cs-accent': '#6b5fd0', '--cs-accent-dim': '#9a8cff', '--cs-danger': '#d63b3b',
      '--cs-success': '#2f9e60', '--cs-warning': '#b07a16'
    },
    'high-contrast': {
      '--cs-bg': '#000000', '--cs-surface': '#0a0a0a', '--cs-surface2': '#141414',
      '--cs-border': '#ffffff', '--cs-text': '#ffffff', '--cs-text-muted': '#e0e0e0',
      '--cs-accent': '#c5b8ff', '--cs-accent-dim': '#a594ff', '--cs-danger': '#ff8a8a',
      '--cs-success': '#7dffae', '--cs-warning': '#ffd86b'
    }
  };

  function prefersDark() {
    try {
      return global.matchMedia &&
        global.matchMedia('(prefers-color-scheme: dark)').matches;
    } catch (e) { return true; }
  }

  function resolve(name) {
    if (name === 'system') return prefersDark() ? 'dark' : 'light';
    return THEMES[name] ? name : 'dark';
  }

  function get() {
    try {
      var v = global.localStorage && global.localStorage.getItem(STORAGE_KEY);
      return ORDER.indexOf(v) >= 0 ? v : 'dark';
    } catch (e) { return 'dark'; }
  }

  function announce(name) {
    var host = document.querySelector('[role="status"]');
    if (host) host.textContent = 'Theme: ' + name;
  }

  function applyVars(resolvedName) {
    var vars = THEMES[resolvedName] || THEMES.dark;
    var root = document.documentElement;
    for (var k in vars) {
      if (Object.prototype.hasOwnProperty.call(vars, k)) {
        root.style.setProperty(k, vars[k]);
      }
    }
    root.setAttribute('data-theme', resolvedName);
  }

  function persistRemote(name) {
    try {
      if (!global.fetch) return;
      global.fetch('/api/preferences', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ theme: name })
      }).catch(function () { /* offline / local-only: ignore */ });
    } catch (e) { /* ignore */ }
  }

  function set(name) {
    if (ORDER.indexOf(name) < 0) name = 'dark';
    try { global.localStorage.setItem(STORAGE_KEY, name); } catch (e) { /* ignore */ }
    applyVars(resolve(name));
    announce(name);
    persistRemote(name);
    return name;
  }

  function cycle() {
    var i = ORDER.indexOf(get());
    return set(ORDER[(i + 1) % ORDER.length]);
  }

  function init() {
    applyVars(resolve(get()));
    // Re-apply when the OS theme flips while on 'system'.
    try {
      var mq = global.matchMedia('(prefers-color-scheme: dark)');
      var onChange = function () { if (get() === 'system') applyVars(resolve('system')); };
      if (mq.addEventListener) mq.addEventListener('change', onChange);
      else if (mq.addListener) mq.addListener(onChange);
    } catch (e) { /* matchMedia unsupported: fine */ }
    // `T` cycles themes (ignore while typing in a field).
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'T' && e.key !== 't') return;
      var tag = (e.target && e.target.tagName) || '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || e.metaKey || e.ctrlKey) return;
      e.preventDefault();
      cycle();
    });
    var btn = document.getElementById('theme-toggle');
    if (btn) btn.addEventListener('click', function () { cycle(); });
  }

  var ThemeManager = { init: init, set: set, get: get, cycle: cycle,
                       resolve: resolve, THEMES: THEMES, ORDER: ORDER };
  global.ThemeManager = ThemeManager;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})(typeof window !== 'undefined' ? window : this);
