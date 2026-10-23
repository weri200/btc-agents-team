'use strict';
(() => {
  const system = matchMedia('(prefers-color-scheme: dark)');
  let preference;
  try { preference = localStorage.getItem('futures-desk-theme'); } catch {}
  function apply(theme) {
    document.documentElement.dataset.theme = theme;
    const button = document.getElementById('theme-toggle');
    if (button) button.checked = theme === 'dark';
  }
  apply(['light', 'dark'].includes(preference) ? preference : system.matches ? 'dark' : 'light');
  system.addEventListener('change', () => {
    if (!['light', 'dark'].includes(preference)) apply(system.matches ? 'dark' : 'light');
  });
  document.addEventListener('DOMContentLoaded', () => {
    apply(document.documentElement.dataset.theme);
    document.getElementById('theme-toggle').onchange = () => {
      preference = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      apply(preference);
      try { localStorage.setItem('futures-desk-theme', preference); } catch {}
    };
  });
})();
