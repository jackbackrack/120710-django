/* Curation Slideshow — curator decision overlay, no dependencies */
(function () {
  'use strict';

  var SCORE_LABELS = {10: 'Weak', 30: 'Developing', 50: 'Solid', 70: 'Strong', 90: 'Exceptional'};

  // ── State ─────────────────────────────────────────────────────────────────
  var curationDataUrl = '';
  var saveDecisionUrl = '';
  var criteria = [];
  var artworks = [];
  var current = 0;
  var activeSlot = 'a';

  // ── DOM refs ──────────────────────────────────────────────────────────────
  var overlay, imgA, imgB, titleEl, artistsEl, yearEl, mediumEl, dimsEl,
      scoresEl, decisionAreaEl, counterEl, decisionCountsEl, thumbsEl;

  // ── Build overlay DOM (once) ──────────────────────────────────────────────
  function buildOverlay() {
    if (document.getElementById('cs-overlay')) return;
    overlay = document.createElement('div');
    overlay.id = 'cs-overlay';
    overlay.innerHTML =
      '<div id="cs-topbar">' +
        '<div id="cs-counter-area">' +
          '<span id="cs-counter"></span>' +
          '<span id="cs-decision-counts"></span>' +
        '</div>' +
        '<div id="cs-right-controls">' +
          '<a id="cs-open-link" href="#" title="Open detail page (Enter / D)">&#x2197;</a>' +
          '<button class="cs-topbtn" id="cs-close-btn" title="Close (Esc)">&#x2715;</button>' +
        '</div>' +
      '</div>' +
      '<div id="cs-body">' +
        '<button id="cs-prev" aria-label="Previous">&#8249;</button>' +
        '<div id="cs-left">' +
          '<img id="cs-img-a" alt="">' +
          '<img id="cs-img-b" alt="" class="cs-hidden-img">' +
        '</div>' +
        '<div id="cs-right">' +
          '<div id="cs-artwork-info">' +
            '<div id="cs-artwork-title"></div>' +
            '<div id="cs-artwork-artists"></div>' +
            '<div id="cs-artwork-year"></div>' +
            '<div id="cs-artwork-medium"></div>' +
            '<div id="cs-artwork-dims"></div>' +
          '</div>' +
          '<div id="cs-scores"></div>' +
          '<div id="cs-decision-area">' +
            '<button class="cs-decision-btn cs-btn-undecided" data-decision="undecided">Undecided</button>' +
            '<button class="cs-decision-btn cs-btn-selected" data-decision="selected">Selected</button>' +
            '<button class="cs-decision-btn cs-btn-rejected" data-decision="rejected">Rejected</button>' +
          '</div>' +
        '</div>' +
        '<button id="cs-next" aria-label="Next">&#8250;</button>' +
      '</div>' +
      '<div id="cs-footer"><div id="cs-thumbs"></div></div>';
    document.body.appendChild(overlay);

    imgA            = overlay.querySelector('#cs-img-a');
    imgB            = overlay.querySelector('#cs-img-b');
    titleEl         = overlay.querySelector('#cs-artwork-title');
    artistsEl       = overlay.querySelector('#cs-artwork-artists');
    yearEl          = overlay.querySelector('#cs-artwork-year');
    mediumEl        = overlay.querySelector('#cs-artwork-medium');
    dimsEl          = overlay.querySelector('#cs-artwork-dims');
    scoresEl        = overlay.querySelector('#cs-scores');
    decisionAreaEl  = overlay.querySelector('#cs-decision-area');
    counterEl       = overlay.querySelector('#cs-counter');
    decisionCountsEl = overlay.querySelector('#cs-decision-counts');
    thumbsEl        = overlay.querySelector('#cs-thumbs');

    overlay.querySelector('#cs-close-btn').addEventListener('click', close);
    overlay.querySelector('#cs-prev').addEventListener('click', prev);
    overlay.querySelector('#cs-next').addEventListener('click', next);

    overlay.querySelector('#cs-decision-area').addEventListener('click', function (e) {
      var btn = e.target.closest('.cs-decision-btn');
      if (btn) handleDecision(btn.dataset.decision);
    });

    // Physics swipe — finger follows drag; commit slides off and in; cancel springs back
    var swipePanel = overlay.querySelector('#cs-left');
    var swipeStartX = 0, swipeStartTime = 0, swipeDragging = false;
    var swipeStartY = 0;
    overlay.addEventListener('touchstart', function (e) {
      if (e.touches.length !== 1) return;
      swipeStartX = e.touches[0].clientX;
      swipeStartY = e.touches[0].clientY;
      swipeStartTime = Date.now();
      swipeDragging = true;
      swipePanel.style.transition = 'none';
    }, { passive: true });
    overlay.addEventListener('touchmove', function (e) {
      if (!swipeDragging) return;
      var dx = e.touches[0].clientX - swipeStartX;
      var dy = Math.abs(e.touches[0].clientY - swipeStartY);
      if (dy > Math.abs(dx) && Math.abs(dx) < 12) { swipeDragging = false; swipePanel.style.transform = ''; return; }
      e.preventDefault();
      swipePanel.style.transform = 'translateX(' + dx + 'px)';
    }, { passive: false });
    overlay.addEventListener('touchend', function (e) {
      if (!swipeDragging) return;
      swipeDragging = false;
      var dx = e.changedTouches[0].clientX - swipeStartX;
      var elapsed = Math.max(Date.now() - swipeStartTime, 1);
      var vel = dx / elapsed;
      var W = swipePanel.offsetWidth || window.innerWidth;
      var commit = Math.abs(dx) > W * 0.25 || Math.abs(vel) > 0.4;
      if (commit) {
        var dir = dx < 0 ? -1 : 1;
        swipePanel.style.transition = 'transform 0.2s ease-in';
        swipePanel.style.transform = 'translateX(' + (dir * W * 1.05) + 'px)';
        setTimeout(function () {
          if (dir < 0) next(); else prev();
          swipePanel.style.transition = 'none';
          swipePanel.style.transform = 'translateX(' + (-dir * W * 1.05) + 'px)';
          requestAnimationFrame(function () {
            requestAnimationFrame(function () {
              swipePanel.style.transition = 'transform 0.28s cubic-bezier(0.25, 0.46, 0.45, 0.94)';
              swipePanel.style.transform = 'translateX(0)';
            });
          });
        }, 185);
      } else {
        swipePanel.style.transition = 'transform 0.38s cubic-bezier(0.34, 1.56, 0.64, 1)';
        swipePanel.style.transform = 'translateX(0)';
      }
    });
  }

  // ── Open / Close ──────────────────────────────────────────────────────────
  function open(dataUrl, decisionUrl, resumeIndex) {
    curationDataUrl = dataUrl;
    saveDecisionUrl = decisionUrl;
    buildOverlay();
    overlay.classList.add('cs-open');
    document.body.style.overflow = 'hidden';
    scoresEl.innerHTML = '<p class="cs-loading">Loading…</p>';
    titleEl.textContent = '';
    artistsEl.textContent = '';
    yearEl.textContent = '';
    mediumEl.textContent = '';
    dimsEl.textContent = '';
    thumbsEl.innerHTML = '';

    fetch(curationDataUrl)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        criteria = data.criteria;
        artworks = data.artworks;
        buildThumbs();
        var start = resumeIndex != null ? resumeIndex : 0;
        goTo(Math.min(start, artworks.length - 1));
      })
      .catch(function () {
        scoresEl.innerHTML = '<p class="cs-error">Failed to load data.</p>';
      });
  }

  function close() {
    if (!overlay) return;
    overlay.classList.remove('cs-open');
    document.body.style.overflow = '';
    imgA.src = '';
    imgB.src = '';
  }

  // ── Navigation ────────────────────────────────────────────────────────────
  function goTo(idx) {
    if (!artworks.length) return;
    idx = ((idx % artworks.length) + artworks.length) % artworks.length;
    current = idx;
    var aw = artworks[current];

    var incoming = activeSlot === 'a' ? imgB : imgA;
    var outgoing  = activeSlot === 'a' ? imgA : imgB;
    if (aw.img) {
      incoming.src = aw.img;
      incoming.alt = aw.name;
      incoming.classList.remove('cs-hidden-img');
      outgoing.classList.add('cs-hidden-img');
      activeSlot = activeSlot === 'a' ? 'b' : 'a';
    } else {
      imgA.classList.add('cs-hidden-img');
      imgB.classList.add('cs-hidden-img');
    }

    titleEl.textContent = aw.name;
    artistsEl.textContent = aw.artists.join(', ');
    artistsEl.style.display = aw.artists.length ? '' : 'none';

    yearEl.textContent   = aw.year       || '';
    mediumEl.textContent = aw.medium     || '';
    dimsEl.textContent   = aw.dimensions || '';

    overlay.querySelector('#cs-open-link').href = aw.detail_url || '#';
    counterEl.textContent = (current + 1) + ' / ' + artworks.length;

    renderScores(aw);
    renderDecisionBtns(aw);
    updateThumbs();
    updateDecisionCounts();
    preloadAdjacent();
  }

  function next() { goTo(current + 1); }
  function prev() { goTo(current - 1); }

  // ── Scores panel ──────────────────────────────────────────────────────────
  function scoreLabel(score) {
    return SCORE_LABELS[score] ? SCORE_LABELS[score] + ' (' + score + ')' : String(score);
  }

  function renderScores(aw) {
    scoresEl.innerHTML = '';
    if (!aw.juror_scores || !aw.juror_scores.length) {
      scoresEl.innerHTML = '<p class="cs-no-scores">No reviews yet</p>';
      return;
    }

    // Overall score header
    if (aw.weighted_score != null) {
      var overall = document.createElement('div');
      overall.className = 'cs-overall-score';
      overall.textContent = 'Overall: ' + aw.weighted_score + ' / 100';
      scoresEl.appendChild(overall);
    }

    var header = document.createElement('div');
    header.className = 'cs-scores-header';
    header.textContent = 'Reviews (' + aw.juror_scores.length + ')';
    scoresEl.appendChild(header);

    aw.juror_scores.forEach(function (js) {
      var block = document.createElement('div');
      block.className = 'cs-juror-block';

      var nameRow = document.createElement('div');
      nameRow.className = 'cs-juror-name';
      var nameText = js.name;
      if (js.weighted != null) nameText += ' — ' + js.weighted + '/100';
      else if (!criteria.length && js.rating != null) nameText += ' — ' + js.rating + '/100';
      nameRow.textContent = nameText;
      block.appendChild(nameRow);

      if (criteria.length) {
        criteria.forEach(function (c) {
          var row = document.createElement('div');
          row.className = 'cs-score-row';
          var score = js.criteria[c.id];
          row.innerHTML =
            '<span class="cs-crit-name">' + esc(c.name) + '</span>' +
            '<span class="cs-crit-score' + (score === 10 ? ' cs-score-weak' : '') + '">' +
              (score != null ? scoreLabel(score) : '—') +
            '</span>';
          block.appendChild(row);
        });
      } else if (js.rating != null) {
        var row = document.createElement('div');
        row.className = 'cs-score-row';
        row.innerHTML = '<span class="cs-crit-name">Overall</span><span class="cs-crit-score">' + js.rating + '</span>';
        block.appendChild(row);
      }

      scoresEl.appendChild(block);
    });
  }

  function esc(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  // ── Decision buttons ──────────────────────────────────────────────────────
  function renderDecisionBtns(aw) {
    decisionAreaEl.querySelectorAll('.cs-decision-btn').forEach(function (btn) {
      btn.classList.toggle('cs-decision-active', btn.dataset.decision === aw.decision);
    });
  }

  function handleDecision(decision) {
    var aw = artworks[current];
    if (!aw) return;
    aw.decision = decision;
    renderDecisionBtns(aw);
    updateThumbs();
    updateDecisionCounts();
    saveToServer({submission_id: aw.submission_id, decision: decision});
    // Auto-advance after a brief moment
    setTimeout(function () {
      if (current + 1 < artworks.length) goTo(current + 1);
    }, 500);
  }

  // ── Server communication ──────────────────────────────────────────────────
  function getCsrf() {
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : '';
  }

  function saveToServer(payload) {
    fetch(saveDecisionUrl, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': getCsrf()},
      body: JSON.stringify(payload),
    }).catch(function () {});
  }

  // ── Thumbnails ────────────────────────────────────────────────────────────
  function buildThumbs() {
    thumbsEl.innerHTML = '';
    artworks.forEach(function (aw, i) {
      var img = document.createElement('img');
      img.className = 'cs-thumb';
      img.src = aw.thumb || '';
      img.alt = '';
      img.title = aw.name + (aw.weighted_score != null ? ' — ' + aw.weighted_score + '/100' : '');
      img.addEventListener('click', (function (idx) { return function () { goTo(idx); }; })(i));
      thumbsEl.appendChild(img);
    });
    updateThumbs();
  }

  function updateThumbs() {
    var thumbs = thumbsEl.querySelectorAll('.cs-thumb');
    thumbs.forEach(function (t, i) {
      var aw = artworks[i];
      t.classList.toggle('cs-thumb-current',   i === current);
      t.classList.toggle('cs-thumb-selected',  aw && aw.decision === 'selected');
      t.classList.toggle('cs-thumb-rejected',  aw && aw.decision === 'rejected');
      t.classList.toggle('cs-thumb-undecided', aw && aw.decision === 'undecided');
    });
    var active = thumbs[current];
    if (active) active.scrollIntoView({inline: 'nearest', block: 'nearest', behavior: 'smooth'});
  }

  function updateDecisionCounts() {
    var sel = 0, rej = 0, und = 0;
    artworks.forEach(function (a) {
      if (a.decision === 'selected') sel++;
      else if (a.decision === 'rejected') rej++;
      else und++;
    });
    decisionCountsEl.innerHTML =
      '<span class="cs-count-selected">Selected: ' + sel + '</span>' +
      '<span class="cs-count-sep"> • </span>' +
      '<span class="cs-count-rejected">Rejected: ' + rej + '</span>' +
      '<span class="cs-count-sep"> • </span>' +
      '<span class="cs-count-undecided">Undecided: ' + und + '</span>';
  }

  // ── Preload ───────────────────────────────────────────────────────────────
  function preloadAdjacent() {
    [current - 1, current + 1].forEach(function (idx) {
      idx = ((idx % artworks.length) + artworks.length) % artworks.length;
      if (artworks[idx] && artworks[idx].img) { (new Image()).src = artworks[idx].img; }
    });
  }

  // ── Keyboard ──────────────────────────────────────────────────────────────
  document.addEventListener('keydown', function (e) {
    if (!overlay || !overlay.classList.contains('cs-open')) return;
    switch (e.key) {
      case 'ArrowRight': e.preventDefault(); next(); break;
      case 'ArrowLeft':  e.preventDefault(); prev(); break;
      case 'Escape':     close(); break;
      case 'u': case 'U': e.preventDefault(); handleDecision('undecided'); break;
      case 's': case 'S': e.preventDefault(); handleDecision('selected');  break;
      case 'r': case 'R': e.preventDefault(); handleDecision('rejected');  break;
      case 'Enter':
      case 'd': case 'D': {
        e.preventDefault();
        var aw = artworks[current];
        if (aw && aw.detail_url) {
          try {
            sessionStorage.setItem('cs_resume', JSON.stringify({
              curationDataUrl: curationDataUrl,
              saveDecisionUrl: saveDecisionUrl,
              index: current,
            }));
          } catch (ex) {}
          close();
          window.location.href = aw.detail_url;
        }
        break;
      }
    }
  });

  // ── Wire launch buttons ───────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.cs-launch-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        open(btn.dataset.curationDataUrl, btn.dataset.saveDecisionUrl);
      });
    });
  });

  // ── Auto-resume on back/forward ───────────────────────────────────────────
  window.addEventListener('pageshow', function () {
    try {
      var saved = sessionStorage.getItem('cs_resume');
      if (!saved) return;
      var state = JSON.parse(saved);
      var btn = document.querySelector(
        '.cs-launch-btn[data-curation-data-url="' + state.curationDataUrl + '"]'
      );
      if (btn) {
        sessionStorage.removeItem('cs_resume');
        open(state.curationDataUrl, state.saveDecisionUrl, state.index);
      }
    } catch (ex) {}
  });

  window.openCurationSlideshow = open;
}());
