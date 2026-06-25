/*
 * keyboard.js — full keyboard navigation for ClaudeStudio (Feature 12, v0.5.2).
 *
 * Power users live on the keyboard. `KeyboardNavigator` registers a single global
 * keydown handler that maps keys to high-level intents and re-broadcasts them as
 * custom DOM events (`cs:navigate`, `cs:action`) the SPA's views subscribe to —
 * so view code stays decoupled from raw key codes. It never fires while you're
 * typing in an input/textarea/contenteditable.
 *
 * The `?` key opens a cheat-sheet overlay (pure DOM + CSS) listing every shortcut.
 * Shortcut preferences live in localStorage (web-only — never in the index).
 *
 * This is the v0.5.2 groundwork: the navigator, the event bus and the cheat sheet
 * ship and are wired; individual views opt in to `cs:navigate`/`cs:action` over
 * time (the replay view already has its own rich key handling).
 */
(function () {
  'use strict';

  var STORE_KEY = 'cs.keyboard.shortcuts';

  // Default shortcut map: key -> {event, intent, group, label}.
  var DEFAULTS = {
    'j': { event: 'cs:navigate', intent: 'next', group: 'Navigate', label: 'Next session / message' },
    'k': { event: 'cs:navigate', intent: 'prev', group: 'Navigate', label: 'Previous session / message' },
    'Enter': { event: 'cs:action', intent: 'open', group: 'Navigate', label: 'Open focused item' },
    'Escape': { event: 'cs:action', intent: 'back', group: 'Navigate', label: 'Go back' },
    '/': { event: 'cs:action', intent: 'search', group: 'Global', label: 'Open search' },
    's': { event: 'cs:action', intent: 'star', group: 'Session', label: 'Star / unstar' },
    'e': { event: 'cs:action', intent: 'export', group: 'Session', label: 'Export session' },
    '?': { event: 'cs:action', intent: 'help', group: 'Global', label: 'Show this cheat sheet' },
  };

  function loadShortcuts() {
    try {
      var raw = window.localStorage.getItem(STORE_KEY);
      if (raw) return Object.assign({}, DEFAULTS, JSON.parse(raw));
    } catch (e) { /* ignore corrupt/blocked storage */ }
    return Object.assign({}, DEFAULTS);
  }

  function isTypingTarget(t) {
    if (!t) return false;
    var tag = (t.tagName || '').toLowerCase();
    return tag === 'input' || tag === 'select' || tag === 'textarea' || t.isContentEditable;
  }

  function KeyboardNavigator(opts) {
    opts = opts || {};
    this.shortcuts = loadShortcuts();
    this.focusIndex = -1;          // "focused item" cursor per view
    this.enabled = true;
    this._onKey = this._onKey.bind(this);
  }

  KeyboardNavigator.prototype.start = function () {
    document.addEventListener('keydown', this._onKey);
    return this;
  };

  KeyboardNavigator.prototype.stop = function () {
    document.removeEventListener('keydown', this._onKey);
  };

  KeyboardNavigator.prototype.setShortcut = function (key, def) {
    this.shortcuts[key] = def;
    try { window.localStorage.setItem(STORE_KEY, JSON.stringify(this.shortcuts)); } catch (e) { /* */ }
  };

  KeyboardNavigator.prototype._onKey = function (e) {
    if (!this.enabled || e.metaKey || e.ctrlKey || e.altKey) return;
    if (isTypingTarget(e.target)) return;
    var def = this.shortcuts[e.key];
    if (!def) return;
    if (def.intent === 'help') { e.preventDefault(); toggleCheatSheet(this.shortcuts); return; }
    // Re-broadcast as a high-level intent; views decide what to do (and may
    // preventDefault). We don't preventDefault here so a view can ignore it.
    document.dispatchEvent(new CustomEvent(def.event, { detail: { intent: def.intent, key: e.key } }));
  };

  // ---- cheat sheet overlay ----
  function toggleCheatSheet(shortcuts) {
    var existing = document.getElementById('kbd-cheat');
    if (existing) { existing.remove(); return; }
    var groups = {};
    Object.keys(shortcuts).forEach(function (key) {
      var d = shortcuts[key];
      (groups[d.group] = groups[d.group] || []).push({ key: key, label: d.label });
    });
    var overlay = document.createElement('div');
    overlay.id = 'kbd-cheat';
    overlay.className = 'kbd-cheat';
    overlay.addEventListener('click', function (ev) { if (ev.target === overlay) overlay.remove(); });

    var panel = document.createElement('div');
    panel.className = 'kbd-cheat-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'true');
    var html = '<div class="kbd-cheat-head">Keyboard shortcuts<button class="kbd-cheat-x" aria-label="Close">×</button></div>';
    Object.keys(groups).forEach(function (g) {
      html += '<div class="kbd-cheat-group"><h4>' + g + '</h4>';
      groups[g].forEach(function (row) {
        var k = row.key === ' ' ? 'Space' : row.key;
        html += '<div class="kbd-cheat-row"><kbd>' + k + '</kbd><span>' + row.label + '</span></div>';
      });
      html += '</div>';
    });
    panel.innerHTML = html;
    panel.querySelector('.kbd-cheat-x').addEventListener('click', function () { overlay.remove(); });
    overlay.appendChild(panel);
    document.body.appendChild(overlay);
  }

  // Esc also closes the cheat sheet (so it doesn't swallow the global Escape).
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { var c = document.getElementById('kbd-cheat'); if (c) c.remove(); }
  });

  window.KeyboardNavigator = KeyboardNavigator;
  window.csKeyboard = new KeyboardNavigator().start();
})();
