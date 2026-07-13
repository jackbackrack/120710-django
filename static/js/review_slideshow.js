/* Review Slideshow — inline jury scoring overlay, no dependencies */
(function () {
  'use strict';

  var SCORE_CHOICES = [
    { score: 10, label: 'Weak' },
    { score: 30, label: 'Developing' },
    { score: 50, label: 'Solid' },
    { score: 70, label: 'Strong' },
    { score: 90, label: 'Exceptional' },
  ];

  // ── State ─────────────────────────────────────────────────────────────────
  var reviewDataUrl = '';
  var saveScoreUrl = '';
  var criteria = [];    // [{id, name, description, percentage}]
  var artworks = [];    // [{slug, name, artists, img, thumb, detail_url, scores, rating, body, reviewed}]
  var current = 0;
  var activeSlot = 'a';
  var focusedCritIdx = 0;

  // ── DOM refs ──────────────────────────────────────────────────────────────
  var overlay, imgA, imgB, titleEl, artistsEl, yearEl, mediumEl, dimsEl, criteriaEl,
      bodyInput, flashEl, counterEl, reviewedCountEl, thumbsEl,
      autoAdvanceEl;

  // ── Build overlay DOM (once) ──────────────────────────────────────────────
  function buildOverlay() {
    if (document.getElementById('review-overlay')) return;
    overlay = document.createElement('div');
    overlay.id = 'review-overlay';
    overlay.innerHTML =
      '<div id="rs-topbar">' +
        '<div id="rs-counter-area">' +
          '<span id="rs-counter"></span>' +
          '<span id="rs-reviewed-count"></span>' +
        '</div>' +
        '<div id="rs-right-controls">' +
          '<label id="rs-auto-label" title="Auto-advance to next after all criteria scored">' +
            '<input type="checkbox" id="rs-auto-advance" checked> Auto' +
          '</label>' +
          '<a id="rs-open-link" href="#" target="_blank" rel="noopener" title="Open detail page (D)">&#x2197;</a>' +
          '<button class="rs-topbtn" id="rs-help-btn" title="Keyboard shortcuts (?)">?</button>' +
          '<button class="rs-topbtn" id="rs-close-btn" title="Close (Esc)">&#x2715;</button>' +
        '</div>' +
      '</div>' +
      '<div id="rs-body">' +
        '<button id="rs-prev" aria-label="Previous">&#8249;</button>' +
        '<div id="rs-left">' +
          '<img id="rs-img-a" alt="">' +
          '<img id="rs-img-b" alt="" class="rs-hidden-img">' +
        '</div>' +
        '<div id="rs-right">' +
          '<div id="rs-artwork-info">' +
            '<div id="rs-artwork-title"></div>' +
            '<div id="rs-artwork-artists"></div>' +
            '<div id="rs-artwork-year"></div>' +
            '<div id="rs-artwork-medium"></div>' +
            '<div id="rs-artwork-dims"></div>' +
          '</div>' +
          '<div id="rs-criteria"></div>' +
          '<div id="rs-notes-area">' +
            '<label for="rs-body-input">Notes (optional)</label>' +
            '<textarea id="rs-body-input" rows="3" placeholder="Your notes…"></textarea>' +
          '</div>' +
          '<div id="rs-save-flash"></div>' +
        '</div>' +
        '<button id="rs-next" aria-label="Next">&#8250;</button>' +
      '</div>' +
      '<div id="rs-footer"><div id="rs-thumbs"></div></div>' +
      '<div id="rs-help" style="display:none">' +
        '<div id="rs-help-inner">' +
          '<h3>Review Shortcuts</h3>' +
          '<table>' +
            '<tr><td>&#8592; &#8594;</td><td>Previous / next artwork</td></tr>' +
            '<tr><td>1 &ndash; 5</td><td>Score focused criterion</td></tr>' +
            '<tr><td>Tab</td><td>Move focus to next criterion</td></tr>' +
            '<tr><td>Enter / D</td><td>Open detail page</td></tr>' +
            '<tr><td>Esc</td><td>Close</td></tr>' +
            '<tr><td>?</td><td>This help</td></tr>' +
          '</table>' +
          '<button id="rs-help-close">Close</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);

    imgA             = overlay.querySelector('#rs-img-a');
    imgB             = overlay.querySelector('#rs-img-b');
    titleEl          = overlay.querySelector('#rs-artwork-title');
    artistsEl        = overlay.querySelector('#rs-artwork-artists');
    yearEl           = overlay.querySelector('#rs-artwork-year');
    mediumEl         = overlay.querySelector('#rs-artwork-medium');
    dimsEl           = overlay.querySelector('#rs-artwork-dims');
    criteriaEl       = overlay.querySelector('#rs-criteria');
    bodyInput        = overlay.querySelector('#rs-body-input');
    flashEl          = overlay.querySelector('#rs-save-flash');
    counterEl        = overlay.querySelector('#rs-counter');
    reviewedCountEl  = overlay.querySelector('#rs-reviewed-count');
    thumbsEl         = overlay.querySelector('#rs-thumbs');
    autoAdvanceEl    = overlay.querySelector('#rs-auto-advance');

    overlay.querySelector('#rs-close-btn').addEventListener('click', close);
    overlay.querySelector('#rs-prev').addEventListener('click', prev);
    overlay.querySelector('#rs-next').addEventListener('click', next);
    overlay.querySelector('#rs-help-btn').addEventListener('click', showHelp);
    overlay.querySelector('#rs-help-close').addEventListener('click', hideHelp);
    overlay.querySelector('#rs-help').addEventListener('click', function (e) {
      if (e.target.id === 'rs-help') hideHelp();
    });

    bodyInput.addEventListener('blur', function () {
      var aw = artworks[current];
      if (!aw) return;
      var val = bodyInput.value;
      if (val === (aw.body || '')) return;
      aw.body = val;
      saveToServer({ artwork_slug: aw.slug, body: val });
    });

    // ── Photo-roll swipe: edge-to-edge filmstrip that follows the finger ──────
    var swipePanel = overlay.querySelector('#rs-left');
    var swipeStartX = 0, swipeStartY = 0, swipeStartTime = 0, swipeDragging = false;
    var swTrack = null, swW = 0;

    function swImg(i) {
      var a = artworks;
      if (!a.length) return null;
      i = ((i % a.length) + a.length) % a.length;
      return a[i] ? a[i].img : null;
    }

    function swBuildTrack() {
      if (swTrack) swTrack.remove();
      swW = swipePanel.offsetWidth || window.innerWidth;
      swTrack = document.createElement('div');
      swTrack.style.cssText = 'position:absolute;top:0;left:0;height:100%;display:flex;will-change:transform;z-index:5;';
      swTrack.style.width = (swW * 3) + 'px';
      [current - 1, current, current + 1].forEach(function (idx) {
        var cell = document.createElement('div');
        cell.style.cssText = 'flex:0 0 ' + swW + 'px;height:100%;display:flex;align-items:center;justify-content:center;';
        var url = swImg(idx);
        if (url) {
          var im = document.createElement('img');
          im.src = url;
          im.draggable = false;
          im.style.cssText = 'max-width:100%;max-height:100%;object-fit:contain;pointer-events:none;user-select:none;';
          cell.appendChild(im);
        }
        swTrack.appendChild(cell);
      });
      swTrack.style.transition = 'none';
      swTrack.style.transform = 'translateX(' + (-swW) + 'px)';
      swipePanel.appendChild(swTrack);
    }

    overlay.addEventListener('touchstart', function (e) {
      if (e.touches.length !== 1) return;
      swipeStartX = e.touches[0].clientX;
      swipeStartY = e.touches[0].clientY;
      swipeStartTime = Date.now();
      swipeDragging = true;
    }, { passive: true });

    overlay.addEventListener('touchmove', function (e) {
      if (!swipeDragging) return;
      var dx = e.touches[0].clientX - swipeStartX;
      var dy = Math.abs(e.touches[0].clientY - swipeStartY);
      if (!swTrack && dy > Math.abs(dx) && Math.abs(dx) < 12) { swipeDragging = false; return; } // vertical → allow scroll
      e.preventDefault();
      if (!swTrack) swBuildTrack();
      swTrack.style.transform = 'translateX(' + (-swW + dx) + 'px)';
    }, { passive: false });

    overlay.addEventListener('touchend', function (e) {
      if (!swipeDragging) return;
      swipeDragging = false;
      var dx = e.changedTouches[0].clientX - swipeStartX;
      if (!swTrack) return; // tap or vertical — nothing to settle
      var elapsed = Math.max(Date.now() - swipeStartTime, 1);
      var vel = dx / elapsed;
      var commit = Math.abs(dx) > swW * 0.25 || Math.abs(vel) > 0.4;
      var el = swTrack;
      if (commit) {
        var navDir = dx < 0 ? 1 : -1;
        var targetX = navDir > 0 ? -swW * 2 : 0;
        el.style.transition = 'transform 0.28s cubic-bezier(0.25, 0.46, 0.45, 0.94)';
        el.style.transform = 'translateX(' + targetX + 'px)';
        goTo(current + navDir);
        setTimeout(function () { el.remove(); if (swTrack === el) swTrack = null; }, 320);
      } else {
        el.style.transition = 'transform 0.34s cubic-bezier(0.34, 1.56, 0.64, 1)';
        el.style.transform = 'translateX(' + (-swW) + 'px)';
        setTimeout(function () { el.remove(); if (swTrack === el) swTrack = null; }, 340);
      }
    });
  }

  // ── Open / Close ──────────────────────────────────────────────────────────
  function open(dataUrl, scoreUrl, resumeIndex) {
    reviewDataUrl = dataUrl;
    saveScoreUrl  = scoreUrl;
    buildOverlay();
    overlay.classList.add('rs-open');
    document.body.style.overflow = 'hidden';
    criteriaEl.innerHTML = '<p class="rs-loading">Loading…</p>';
    titleEl.textContent = '';
    artistsEl.textContent = '';
    artistsEl.style.display = '';
    thumbsEl.innerHTML = '';

    fetch(reviewDataUrl)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        criteria = data.criteria;
        artworks = data.artworks;
        buildThumbs();
        var start = (resumeIndex != null) ? resumeIndex : findFirstUnscored_global();
        goTo(start < 0 ? 0 : start);
      })
      .catch(function () {
        criteriaEl.innerHTML = '<p class="rs-error">Failed to load review data.</p>';
      });
  }

  function close() {
    if (!overlay) return;
    overlay.classList.remove('rs-open');
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

    // Crossfade
    var incoming = activeSlot === 'a' ? imgB : imgA;
    var outgoing  = activeSlot === 'a' ? imgA : imgB;
    if (aw.img) {
      incoming.src = aw.img;
      incoming.alt = aw.name;
      incoming.classList.remove('rs-hidden-img');
      outgoing.classList.add('rs-hidden-img');
      activeSlot = activeSlot === 'a' ? 'b' : 'a';
    } else {
      imgA.classList.add('rs-hidden-img');
      imgB.classList.add('rs-hidden-img');
    }

    titleEl.textContent    = aw.name;
    artistsEl.textContent  = aw.artists.join(', ');
    artistsEl.style.display = aw.artists.length ? '' : 'none';
    yearEl.textContent     = aw.year   || '';
    mediumEl.textContent   = aw.medium || '';
    dimsEl.textContent     = aw.dimensions || '';
    bodyInput.value        = aw.body || '';
    overlay.querySelector('#rs-open-link').href = aw.detail_url || '#';
    counterEl.textContent  = (current + 1) + ' / ' + artworks.length;

    focusedCritIdx = findFirstUnscoredFor(aw);
    renderPanel(aw);
    updateThumbs();
    updateReviewedCount();
    preloadAdjacent();
  }

  function next() { goTo(current + 1); }
  function prev() { goTo(current - 1); }

  // ── Scoring logic ─────────────────────────────────────────────────────────
  function isFullyScored(aw) {
    if (criteria.length === 0) return aw.rating != null;
    for (var i = 0; i < criteria.length; i++) {
      if (aw.scores[criteria[i].id] == null) return false;
    }
    return true;
  }

  function findFirstUnscoredFor(aw) {
    if (criteria.length === 0) return 0;
    for (var i = 0; i < criteria.length; i++) {
      if (aw.scores[criteria[i].id] == null) return i;
    }
    return 0;
  }

  function findFirstUnscored_global() {
    for (var i = 0; i < artworks.length; i++) {
      if (!isFullyScored(artworks[i])) return i;
    }
    return -1;
  }

  function findNextUnscored() {
    for (var i = current + 1; i < artworks.length; i++) {
      if (!isFullyScored(artworks[i])) return i;
    }
    for (var i = 0; i < current; i++) {
      if (!isFullyScored(artworks[i])) return i;
    }
    return -1;
  }

  // ── Panel rendering ───────────────────────────────────────────────────────
  function renderPanel(aw) {
    criteriaEl.innerHTML = '';

    var items = criteria.length > 0
      ? criteria.map(function (c) {
          return { id: c.id, name: c.name, pct: c.percentage, currentScore: aw.scores[c.id] };
        })
      : [{ id: 'overall', name: 'Overall Rating', pct: null, currentScore: aw.rating }];

    items.forEach(function (item, idx) {
      var div = document.createElement('div');
      div.className = 'rs-criterion' + (idx === focusedCritIdx ? ' rs-focused' : '');
      div.dataset.critIdx = idx;

      var nameDiv = document.createElement('div');
      nameDiv.className = 'rs-criterion-name';
      nameDiv.textContent = item.name;
      if (item.pct != null) {
        var pctSpan = document.createElement('span');
        pctSpan.className = 'rs-pct';
        pctSpan.textContent = ' ' + item.pct + '%';
        nameDiv.appendChild(pctSpan);
      }
      div.appendChild(nameDiv);

      var row = document.createElement('div');
      row.className = 'rs-score-row';
      SCORE_CHOICES.forEach(function (choice) {
        var btn = document.createElement('button');
        btn.className = 'rs-score-btn' + (choice.score === item.currentScore ? ' rs-selected' : '');
        btn.type = 'button';
        btn.textContent = choice.label;
        btn.dataset.score = choice.score;
        btn.addEventListener('click', (function (critId, score, critIdx) {
          return function () { handleScoreClick(critId, score, critIdx); };
        })(item.id, choice.score, idx));
        row.appendChild(btn);
      });
      div.appendChild(row);
      criteriaEl.appendChild(div);
    });
  }

  function handleScoreClick(critId, score, critIdx) {
    var aw = artworks[current];
    if (!aw) return;

    if (critId === 'overall') {
      aw.rating = score;
    } else {
      aw.scores[critId] = score;
    }
    aw.reviewed = true;

    // Advance focus to next criterion
    var maxIdx = Math.max(0, (criteria.length || 1) - 1);
    focusedCritIdx = critIdx < maxIdx ? critIdx + 1 : critIdx;

    renderPanel(aw);
    updateThumbs();
    updateReviewedCount();
    flashSaved();

    var payload = { artwork_slug: aw.slug };
    if (critId === 'overall') { payload.rating = score; }
    else { payload.criterion_id = critId; payload.score = score; }
    saveToServer(payload);

    if (autoAdvanceEl.checked && isFullyScored(aw)) {
      var nextIdx = findNextUnscored();
      if (nextIdx >= 0) {
        setTimeout(function () { goTo(nextIdx); }, 700);
      }
    }
  }

  // ── Server communication ──────────────────────────────────────────────────
  function getCsrf() {
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : '';
  }

  function saveToServer(payload) {
    fetch(saveScoreUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
      body: JSON.stringify(payload),
    }).catch(function () {});
  }

  // ── Flash ─────────────────────────────────────────────────────────────────
  var flashTimer = null;
  function flashSaved() {
    flashEl.textContent = '✓ saved';
    flashEl.classList.add('rs-flash-show');
    clearTimeout(flashTimer);
    flashTimer = setTimeout(function () { flashEl.classList.remove('rs-flash-show'); }, 1400);
  }

  // ── Thumbnails ────────────────────────────────────────────────────────────
  function buildThumbs() {
    thumbsEl.innerHTML = '';
    artworks.forEach(function (aw, i) {
      var img = document.createElement('img');
      img.className = 'rs-thumb';
      img.src = aw.thumb || '';
      img.alt = '';
      img.title = aw.name;
      img.addEventListener('click', (function (idx) {
        return function () { goTo(idx); };
      })(i));
      thumbsEl.appendChild(img);
    });
    updateThumbs();
  }

  function updateThumbs() {
    var thumbs = thumbsEl.querySelectorAll('.rs-thumb');
    thumbs.forEach(function (t, i) {
      var aw = artworks[i];
      t.classList.toggle('rs-thumb-current',  i === current);
      t.classList.toggle('rs-thumb-done',     aw && isFullyScored(aw));
      t.classList.toggle('rs-thumb-partial',  aw && aw.reviewed && !isFullyScored(aw));
    });
    var active = thumbs[current];
    if (active) active.scrollIntoView({ inline: 'nearest', block: 'nearest', behavior: 'smooth' });
  }

  function updateReviewedCount() {
    var done = artworks.filter(function (a) { return isFullyScored(a); }).length;
    reviewedCountEl.textContent = done + ' / ' + artworks.length + ' scored';
  }

  // ── Preload ───────────────────────────────────────────────────────────────
  function preloadAdjacent() {
    [current - 1, current + 1].forEach(function (idx) {
      idx = ((idx % artworks.length) + artworks.length) % artworks.length;
      if (artworks[idx] && artworks[idx].img) { (new Image()).src = artworks[idx].img; }
    });
  }

  // ── Help ──────────────────────────────────────────────────────────────────
  function showHelp() { overlay.querySelector('#rs-help').style.display = 'flex'; }
  function hideHelp() { overlay.querySelector('#rs-help').style.display = 'none'; }

  // ── Keyboard ──────────────────────────────────────────────────────────────
  document.addEventListener('keydown', function (e) {
    if (!overlay || !overlay.classList.contains('rs-open')) return;
    if (document.activeElement === bodyInput) {
      if (e.key === 'Escape') bodyInput.blur();
      return;
    }
    var helpVisible = overlay.querySelector('#rs-help').style.display !== 'none';
    switch (e.key) {
      case 'ArrowRight': e.preventDefault(); next(); break;
      case 'ArrowLeft':  e.preventDefault(); prev(); break;
      case 'Escape':
        if (helpVisible) hideHelp(); else close();
        break;
      case 'Enter':
      case 'd': case 'D': {
        e.preventDefault();
        var aw = artworks[current];
        if (aw && aw.detail_url) {
          try {
            sessionStorage.setItem('rs_resume', JSON.stringify({
              reviewDataUrl: reviewDataUrl,
              saveScoreUrl: saveScoreUrl,
              index: current,
            }));
          } catch (ex) {}
          close();
          window.location.href = aw.detail_url;
        }
        break;
      }
      case '?': showHelp(); break;
      case 'Tab': {
        e.preventDefault();
        var maxIdx = Math.max(0, (criteria.length || 1) - 1);
        focusedCritIdx = focusedCritIdx < maxIdx ? focusedCritIdx + 1 : 0;
        renderPanel(artworks[current]);
        break;
      }
      default: {
        var num = parseInt(e.key, 10);
        if (num >= 1 && num <= 5) {
          e.preventDefault();
          var choice = SCORE_CHOICES[num - 1];
          var items = criteria.length > 0 ? criteria : [{ id: 'overall' }];
          var focused = items[focusedCritIdx] || items[0];
          handleScoreClick(focused.id !== undefined ? focused.id : 'overall', choice.score, focusedCritIdx);
        }
      }
    }
  });

  // ── Wire launch buttons ───────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.rs-launch-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        open(btn.dataset.reviewDataUrl, btn.dataset.saveScoreUrl);
      });
    });
  });

  // ── Auto-resume on back/forward (pageshow fires for both fresh load and bfcache) ──
  window.addEventListener('pageshow', function () {
    try {
      var saved = sessionStorage.getItem('rs_resume');
      if (!saved) return;
      var state = JSON.parse(saved);
      var btn = document.querySelector(
        '.rs-launch-btn[data-review-data-url="' + state.reviewDataUrl + '"]'
      );
      if (btn) {
        sessionStorage.removeItem('rs_resume');
        open(state.reviewDataUrl, state.saveScoreUrl, state.index);
      }
      // No matching button on this page — leave state for the next page back
    } catch (ex) {}
  });

  // Also expose globally for inline onclick use
  window.openReviewSlideshow = open;

})();
