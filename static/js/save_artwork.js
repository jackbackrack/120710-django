(function () {
  'use strict';

  function getCsrf() {
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : '';
  }

  function updateButtons(artworkId, saved) {
    document.querySelectorAll('.save-artwork-btn[data-artwork-id="' + artworkId + '"]').forEach(function (btn) {
      btn.classList.toggle('saved', saved);
      btn.title = saved ? 'Unsave' : 'Save';
      btn.setAttribute('aria-pressed', saved ? 'true' : 'false');
    });
  }

  document.addEventListener('click', function (e) {
    var btn = e.target.closest('.save-artwork-btn');
    if (!btn) return;
    e.preventDefault();
    var url = btn.dataset.saveUrl;
    var artworkId = btn.dataset.artworkId;
    if (!url) return;
    fetch(url, {
      method: 'POST',
      headers: { 'X-CSRFToken': getCsrf(), 'X-Requested-With': 'XMLHttpRequest' },
      credentials: 'same-origin',
    })
      .then(function (r) { return r.json(); })
      .then(function (data) { updateButtons(artworkId, data.saved); })
      .catch(function () {});
  });
})();
