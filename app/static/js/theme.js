/* Theme switching. Loaded in <head> so the stored choice is applied before the
   first paint — a deferred script would let the page flash light then go dark.

   Three states: no stored value means "follow the OS", which the CSS handles
   with prefers-color-scheme. Choosing a theme stores it and pins it. */
(function () {
  var STORAGE_KEY = 'theme';

  function stored() {
    try {
      return localStorage.getItem(STORAGE_KEY);
    } catch (e) {
      return null;  // private mode / blocked storage: fall back to the OS
    }
  }

  function apply(theme) {
    if (theme === 'dark' || theme === 'light') {
      document.documentElement.setAttribute('data-theme', theme);
    } else {
      document.documentElement.removeAttribute('data-theme');
    }
  }

  function prefersDark() {
    return !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
  }

  function effective() {
    var choice = stored();
    if (choice === 'dark' || choice === 'light') return choice;
    return prefersDark() ? 'dark' : 'light';
  }

  function syncButtons() {
    var isDark = effective() === 'dark';
    var buttons = document.querySelectorAll('.theme-toggle');
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].setAttribute('aria-pressed', String(isDark));
      buttons[i].setAttribute('title', isDark ? 'Switch to light mode' : 'Switch to dark mode');
    }
  }

  apply(stored());

  window.toggleTheme = function () {
    var next = effective() === 'dark' ? 'light' : 'dark';
    apply(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch (e) { /* the theme still applies for this page view */ }
    syncButtons();
  };

  document.addEventListener('DOMContentLoaded', syncButtons);

  // Keep the button in step with the OS while no explicit choice is stored.
  if (window.matchMedia) {
    var mq = window.matchMedia('(prefers-color-scheme: dark)');
    var onChange = function () { if (!stored()) syncButtons(); };
    if (mq.addEventListener) mq.addEventListener('change', onChange);
    else if (mq.addListener) mq.addListener(onChange);
  }
})();
