// Dimuat dari file (bukan <script> inline) agar lolos CSP script-src 'self'.
// `document`, bukan `document.body`: script di <head> berjalan sebelum <body> ada.
document.addEventListener('htmx:beforeSwap', (evt) => {
  const xhr = evt.detail.xhr;
  // 400/422: form dirender ulang dengan pesan error.
  // HX-Retarget: server sengaja mengirim fragment error (toast) untuk status lain (403/404/429/5xx).
  if (xhr.status === 400 || xhr.status === 422 || xhr.getResponseHeader('HX-Retarget')) {
    evt.detail.shouldSwap = true;
    evt.detail.isError = false;
  }
});

// Penyegaran data dijeda saat dialog atau form dipakai, supaya isian dan token yang tampil sekali tidak hilang.
function refreshPaused() {
  if (document.querySelector('dialog[open], [data-no-refresh], details.row-menu[open]')) return true;
  const focused = document.activeElement;
  return !!(focused && focused.closest && focused.closest('form') && focused.matches('input, select, textarea'));
}
document.addEventListener('htmx:beforeRequest', (evt) => {
  if (evt.detail.elt && evt.detail.elt.matches('[data-live]') && refreshPaused()) evt.preventDefault();
});

// Log dan riwayat yang sedang dibuka tetap terbuka setelah data disegarkan.
let openDetails = [];
document.addEventListener('htmx:beforeSwap', (evt) => {
  if (evt.detail.target && evt.detail.target.matches('[data-live]')) {
    openDetails = Array.from(evt.detail.target.querySelectorAll('details[id][open]')).map((el) => el.id);
  }
});
document.addEventListener('htmx:afterSettle', () => {
  openDetails.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.open = true;
  });
});

// Dialog: isi dimuat htmx ke <dialog id="modal">, lalu dibuka. Fokus awal mengikuti autofocus (tombol Batal).
function closeModal() {
  const modal = document.getElementById('modal');
  if (modal && modal.open) modal.close();
  document.querySelectorAll('details.row-menu[open]').forEach((el) => { el.open = false; });
}
document.addEventListener('htmx:afterSwap', (evt) => {
  if (evt.detail.target && evt.detail.target.id === 'modal' && !evt.detail.target.open) {
    evt.detail.target.showModal();
  }
  if (evt.detail.target && evt.detail.target.id === 'toast') scheduleToastHide();
});
document.addEventListener('close-modal', closeModal);
document.addEventListener('click', (evt) => {
  if (evt.target.closest('[data-close-modal]')) closeModal();
  if (evt.target.closest('[data-theme-toggle]')) {
    const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('lumbung-theme', next); } catch (err) { /* cukup untuk halaman ini */ }
  }
});

// Toast
let toastTimer = null;
function scheduleToastHide() {
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { document.getElementById('toast').replaceChildren(); }, 6000);
}
document.addEventListener('show-toast', (evt) => {
  const item = document.createElement('p');
  item.className = 'toast';
  item.setAttribute('role', 'status');
  item.textContent = evt.detail.value || '';
  document.getElementById('toast').replaceChildren(item);
  scheduleToastHide();
});

// Keterangan penyegaran di bar atas: waktu data terakhir dan hitung mundur, atau tanda sedang dijeda.
const REFRESH_SECONDS = 60;
let lastRefresh = Date.now();
function wibTime(date) {
  return date.toLocaleTimeString('id-ID', { timeZone: 'Asia/Jakarta', hour: '2-digit', minute: '2-digit' }).replace('.', ':');
}
function updateRefreshLabel() {
  const label = document.getElementById('refresh');
  if (!label) return;
  if (!document.querySelector('[data-live]') || refreshPaused()) {
    label.textContent = 'Penyegaran dijeda';
    return;
  }
  const left = Math.max(0, REFRESH_SECONDS - Math.floor((Date.now() - lastRefresh) / 1000));
  label.textContent = 'Disegarkan ' + wibTime(new Date(lastRefresh)) + ' WIB · ' + left + ' dtk';
}
document.addEventListener('htmx:afterSettle', (evt) => {
  if (evt.detail.target && evt.detail.target.matches('[data-live]')) lastRefresh = Date.now();
});
document.addEventListener('DOMContentLoaded', () => {
  updateRefreshLabel();
  setInterval(updateRefreshLabel, 1000);
});
