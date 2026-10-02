/* Show / hide password toggles.

   One small file rather than a function in main.js, because the sign-in and
   first-run setup pages do not load main.js — pulling it in for one toggle
   would also bring the alert-fading behaviour to screens whose only message is
   an error worth reading. Every page with a password field loads this instead.

   A toggle is a `button.reveal-toggle` whose `aria-controls` names the input.
   It ships `hidden` and is un-hidden here, because without JavaScript it could
   do nothing and a dead control is worse than none. */
(function () {
  function wire(button) {
    var field = document.getElementById(button.getAttribute('aria-controls'));
    if (!field || button.dataset.revealReady) return;
    button.dataset.revealReady = 'true';
    button.hidden = false;

    button.addEventListener('click', function () {
      var shown = field.type === 'text';
      field.type = shown ? 'password' : 'text';
      button.classList.toggle('revealed', !shown);
      button.setAttribute('aria-pressed', String(!shown));
      var label = shown ? 'Show password' : 'Hide password';
      button.setAttribute('aria-label', label);
      button.title = label;
      field.focus();
    });
  }

  function init() {
    Array.prototype.forEach.call(
      document.querySelectorAll('button.reveal-toggle[aria-controls]'), wire);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
