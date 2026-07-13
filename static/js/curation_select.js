/* Bulk multi-select for the curation submissions grid.
   Requires a #curation-select-root element with data-bulk-url set.
   Selection modes:
     - Click card: toggle
     - Shift+click: range-select within section (additive)
     - Drag on grid background: rubber-band region select
     - Shift+drag: additive rubber-band
     - "☑ All" button: select all in section (Shift = additive)
   Action bar slides up from bottom when anything is selected.
*/
(function () {
  'use strict';

  var root = document.getElementById('curation-select-root');
  if (!root) return;
  var BULK_URL = root.dataset.bulkUrl;

  // ── State ──────────────────────────────────────────────────────────────────
  var selected = new Set();  // sub PKs (strings)
  var lastClickPk = null;    // anchor for shift-click range

  function allCards() {
    return Array.from(document.querySelectorAll('.card[data-sub-id]'));
  }

  // ── Visual sync ────────────────────────────────────────────────────────────
  function syncViz() {
    allCards().forEach(function (c) {
      c.classList.toggle('cs-bulk-selected', selected.has(c.dataset.subId));
    });
    updateBar();
  }

  // ── Selection helpers ──────────────────────────────────────────────────────
  function toggle(pk) {
    if (selected.has(pk)) selected.delete(pk); else selected.add(pk);
  }

  function rangeSelect(fromPk, toPk) {
    var fromCard = document.querySelector('.card[data-sub-id="' + fromPk + '"]');
    var toCard   = document.querySelector('.card[data-sub-id="' + toPk + '"]');
    if (!fromCard || !toCard || fromCard.closest('.cards') !== toCard.closest('.cards')) {
      selected.add(toPk);
      return;
    }
    var sCards = Array.from(fromCard.closest('.cards').querySelectorAll('.card[data-sub-id]'));
    var a = sCards.findIndex(function (c) { return c.dataset.subId === fromPk; });
    var b = sCards.findIndex(function (c) { return c.dataset.subId === toPk; });
    var lo = Math.min(a, b), hi = Math.max(a, b);
    for (var i = lo; i <= hi; i++) selected.add(sCards[i].dataset.subId);
  }

  function selectSection(cardsEl, additive) {
    if (!additive) selected.clear();
    cardsEl.querySelectorAll('.card[data-sub-id]').forEach(function (c) {
      selected.add(c.dataset.subId);
    });
    lastClickPk = null;
    syncViz();
  }

  // ── Card click ─────────────────────────────────────────────────────────────
  var rbJustFinished = false;

  function onCardClick(e) {
    if (e.target.closest('a, button, select, input, form')) return;
    if (rbJustFinished) return;
    var pk = e.currentTarget.dataset.subId;
    if (e.shiftKey && lastClickPk) {
      rangeSelect(lastClickPk, pk);
    } else {
      toggle(pk);
      lastClickPk = pk;
    }
    syncViz();
  }

  allCards().forEach(function (card) {
    card.addEventListener('click', onCardClick);
  });

  // ── "☑ All" buttons ────────────────────────────────────────────────────────
  document.querySelectorAll('.bulk-select-all').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      var label = btn.closest('.section-label');
      var cardsEl = label && label.nextElementSibling;
      if (cardsEl && cardsEl.classList.contains('cards')) {
        selectSection(cardsEl, e.shiftKey);
      }
    });
  });

  // ── Rubber-band region select ──────────────────────────────────────────────
  var rbEl = null, rbX0 = 0, rbY0 = 0, rbActive = false, rbShift = false;
  var RB_THRESHOLD = 6;

  function rbStart(e) {
    if (e.button !== 0) return;
    if (e.target.closest('.card')) return;
    if (!e.target.closest('.cards')) return;
    rbX0 = e.clientX; rbY0 = e.clientY;
    rbShift = e.shiftKey;
    rbActive = false;
    document.addEventListener('mousemove', rbMove);
    document.addEventListener('mouseup', rbEnd);
    e.preventDefault();
  }

  function rbMove(e) {
    var dx = e.clientX - rbX0, dy = e.clientY - rbY0;
    if (!rbActive && Math.sqrt(dx * dx + dy * dy) < RB_THRESHOLD) return;
    if (!rbActive) {
      rbActive = true;
      rbEl = document.createElement('div');
      rbEl.id = 'rb-select';
      document.body.appendChild(rbEl);
    }
    var x = Math.min(rbX0, e.clientX), y = Math.min(rbY0, e.clientY);
    rbEl.style.cssText = 'left:' + x + 'px;top:' + y + 'px;width:' + Math.abs(dx) + 'px;height:' + Math.abs(dy) + 'px';
  }

  function rbEnd(e) {
    document.removeEventListener('mousemove', rbMove);
    document.removeEventListener('mouseup', rbEnd);
    if (!rbActive) return;
    rbActive = false;
    rbJustFinished = true;
    var rx = Math.min(rbX0, e.clientX), ry = Math.min(rbY0, e.clientY);
    var rw = Math.abs(e.clientX - rbX0), rh = Math.abs(e.clientY - rbY0);
    if (rbEl) { rbEl.remove(); rbEl = null; }
    if (!rbShift) selected.clear();
    allCards().forEach(function (card) {
      var r = card.getBoundingClientRect();
      var cx = r.left + r.width / 2, cy = r.top + r.height / 2;
      if (cx >= rx && cx <= rx + rw && cy >= ry && cy <= ry + rh) {
        selected.add(card.dataset.subId);
      }
    });
    lastClickPk = null;
    syncViz();
    setTimeout(function () { rbJustFinished = false; }, 50);
  }

  document.querySelectorAll('.cards').forEach(function (grid) {
    grid.addEventListener('mousedown', rbStart);
  });

  // ── Action bar ─────────────────────────────────────────────────────────────
  var bar = document.createElement('div');
  bar.id = 'bulk-action-bar';
  bar.innerHTML =
    '<span id="bulk-count"></span>' +
    '<button class="bulk-btn" data-decision="undecided">→ Undecided</button>' +
    '<button class="bulk-btn bulk-btn-sel" data-decision="selected">→ Selected</button>' +
    '<button class="bulk-btn bulk-btn-rej" data-decision="rejected">→ Rejected</button>' +
    '<button class="bulk-btn bulk-btn-clr" id="bulk-clear">✕ Clear</button>';
  document.body.appendChild(bar);

  function updateBar() {
    var n = selected.size;
    bar.classList.toggle('bulk-bar-visible', n > 0);
    document.getElementById('bulk-count').textContent = n + ' selected';
  }

  document.getElementById('bulk-clear').addEventListener('click', function () {
    selected.clear(); lastClickPk = null; syncViz();
  });

  bar.querySelectorAll('[data-decision]').forEach(function (btn) {
    btn.addEventListener('click', function () { bulkDecide(btn.dataset.decision); });
  });

  // ── Bulk submit ────────────────────────────────────────────────────────────
  function getCsrf() {
    var el = document.querySelector('[name=csrfmiddlewaretoken]');
    if (el) return el.value;
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : '';
  }

  function bulkDecide(decision) {
    if (!selected.size) return;
    var pks = Array.from(selected);
    bar.querySelectorAll('.bulk-btn').forEach(function (b) { b.disabled = true; });
    fetch(BULK_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
      body: JSON.stringify({ pks: pks, decision: decision }),
    }).then(function (r) { return r.json(); }).then(function (data) {
      if (data.ok) {
        var label = decision.charAt(0).toUpperCase() + decision.slice(1);
        sessionStorage.setItem('curation_scroll_section', label);
        location.reload();
      }
    }).catch(function () {
      bar.querySelectorAll('.bulk-btn').forEach(function (b) { b.disabled = false; });
    });
  }
}());
