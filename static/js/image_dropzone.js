/* Progressive drag-and-drop for file inputs.
 * Enhances every visible <input type="file"> so you can drop a file onto its
 * field (in addition to the normal click-to-choose). Loaded globally, so it
 * covers all the image upload forms (artwork, artist, site, show, event, …).
 * Non-invasive: no DOM restructuring, just events + a hint + a filename readout. */
(function () {
  'use strict';

  function label(files) {
    if (!files || !files.length) return '';
    return files.length === 1 ? files[0].name : files.length + ' files selected';
  }

  function isHidden(el) {
    return el.offsetParent === null && getComputedStyle(el).display === 'none';
  }

  function enhance(input) {
    if (input.type !== 'file' || input.dataset.dzEnhanced || isHidden(input)) return;
    input.dataset.dzEnhanced = '1';

    var zone = input.closest('.mb-3, .form-group') || input.parentElement;
    if (!zone) return;

    var hint = document.createElement('div');
    hint.className = 'dz-hint';
    hint.textContent = '…or drag & drop a file here.';
    var status = document.createElement('div');
    status.className = 'dz-file';
    input.insertAdjacentElement('afterend', status);
    input.insertAdjacentElement('afterend', hint);

    function setFiles(fileList) {
      var dt = new DataTransfer();
      var imgs = Array.prototype.filter.call(fileList, function (f) { return f.type.indexOf('image/') === 0; });
      var use = imgs.length ? imgs : Array.prototype.slice.call(fileList);
      if (!input.multiple) use = use.slice(0, 1);
      use.forEach(function (f) { dt.items.add(f); });
      input.files = dt.files;
      status.textContent = label(input.files);
      input.dispatchEvent(new Event('change', { bubbles: true }));
    }

    ['dragenter', 'dragover'].forEach(function (ev) {
      zone.addEventListener(ev, function (e) {
        e.preventDefault(); e.stopPropagation();
        zone.classList.add('dz-over');
      });
    });
    ['dragleave', 'dragend'].forEach(function (ev) {
      zone.addEventListener(ev, function (e) {
        e.preventDefault();
        if (ev === 'dragleave' && zone.contains(e.relatedTarget)) return;
        zone.classList.remove('dz-over');
      });
    });
    zone.addEventListener('drop', function (e) {
      if (!e.dataTransfer || !e.dataTransfer.files || !e.dataTransfer.files.length) return;
      e.preventDefault(); e.stopPropagation();
      zone.classList.remove('dz-over');
      setFiles(e.dataTransfer.files);
    });
    // Keep the readout in sync when the file is chosen the normal way.
    input.addEventListener('change', function () { status.textContent = label(input.files); });
  }

  function scan() { document.querySelectorAll('input[type="file"]').forEach(enhance); }
  document.addEventListener('DOMContentLoaded', scan);
}());
