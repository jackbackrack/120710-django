/* Infinite scroll for card grids (Artworks / Artists lists).
 * The grid element carries [data-infinite-grid]; each page's cards partial ends
 * with a hidden <span class="infinite-meta" data-page data-has-next>. We fetch
 * ?page=N&partial=1, append the returned cards, and stop when has-next is 0.
 * After appending, we fire a 'cards:appended' event so the slideshow (and any
 * other card-aware code) can wire up the new cards. */
(function () {
  'use strict';
  var grid = document.querySelector('[data-infinite-grid]');
  var sentinel = document.getElementById('infinite-sentinel');
  if (!grid || !sentinel) return;

  var loading = false;

  function meta() { return grid.querySelector('.infinite-meta'); }
  function hasNext() { var m = meta(); return !!(m && m.dataset.hasNext === '1'); }
  function curPage() { var m = meta(); return m ? parseInt(m.dataset.page, 10) || 1 : 1; }

  function loadMore() {
    if (loading || !hasNext()) return;
    loading = true;
    var url = new URL(window.location.href);
    url.searchParams.set('page', curPage() + 1);
    url.searchParams.set('partial', '1');
    fetch(url.toString(), { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (r) { return r.ok ? r.text() : Promise.reject(r.status); })
      .then(function (html) {
        var tmp = document.createElement('div');
        tmp.innerHTML = html.trim();
        var old = meta();
        if (old) old.remove();                       // replace stale marker
        while (tmp.firstChild) grid.appendChild(tmp.firstChild);
        loading = false;
        document.dispatchEvent(new CustomEvent('cards:appended', { detail: { grid: grid } }));
        // If the sentinel is still on screen (short pages), keep going.
        if (hasNext() && sentinel.getBoundingClientRect().top < window.innerHeight + 400) {
          loadMore();
        }
      })
      .catch(function () { loading = false; });
  }

  var io = new IntersectionObserver(function (entries) {
    if (entries[0].isIntersecting) loadMore();
  }, { rootMargin: '600px' });
  io.observe(sentinel);
}());
