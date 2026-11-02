// Dimuat tanpa defer, supaya tema terpasang sebelum halaman digambar. Pilihan hanya kenyamanan per penonton.
(function () {
  var theme = null;
  try { theme = localStorage.getItem('lumbung-theme'); } catch (err) { /* penyimpanan bisa diblokir */ }
  if (theme !== 'dark' && theme !== 'light') {
    theme = window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  document.documentElement.setAttribute('data-theme', theme);
})();
