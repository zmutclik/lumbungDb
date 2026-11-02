// Build CSP Alpine: hanya referensi properti/method di atribut, tanpa ekspresi JS inline.
document.addEventListener('alpine:init', () => {
  Alpine.data('copyBox', () => ({
    label: 'Salin',
    async copy() {
      const text = this.$refs.source.textContent;
      try {
        await navigator.clipboard.writeText(text);
      } catch (err) {
        // clipboard API hanya ada di HTTPS atau localhost; pilih teksnya supaya bisa disalin manual
        const range = document.createRange();
        range.selectNodeContents(this.$refs.source);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        this.label = 'Tekan Ctrl+C';
        return;
      }
      this.label = 'Tersalin';
      setTimeout(() => { this.label = 'Salin'; }, 2000);
    },
  }));
});
