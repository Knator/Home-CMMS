/* ── Repeatable form rows ──
   Tasks, materials, tools and file attachments are all "a list of rows the user
   can add to, remove from and reorder". One implementation, configured per kind,
   rather than four near-copies.

   Each row's inputs are named <kind>_<index>_<field> and a hidden <kind>_count
   field carries how many there are; the server re-reads them in DOM order, so
   reordering rows is what reorders the saved sequence. */

const ROW_TYPES = {
  task: {
    fields: [
      { name: 'description', type: 'text', placeholder: 'Task description', required: true },
      { name: 'minutes', type: 'number', placeholder: 'Minutes', min: '1' },
    ],
    reorderable: true,
  },
  material: {
    fields: [
      { name: 'description', type: 'text', placeholder: 'Material', required: true },
      { name: 'quantity', type: 'text', placeholder: 'Qty (e.g. 2 rolls)', maxlength: '60' },
    ],
    reorderable: true,
  },
  tool: {
    fields: [
      { name: 'description', type: 'text', placeholder: 'Tool', required: true },
    ],
    reorderable: true,
  },
  attachment: {
    fields: [
      { name: 'file', type: 'file', multiple: true },
      { name: 'name', type: 'text', placeholder: 'Friendly name (optional)', maxlength: '255' },
    ],
    reorderable: false,
  },
};

function rowContainer(kind) {
  return document.getElementById(`${kind}-rows`);
}

function buildRow(kind, index) {
  const config = ROW_TYPES[kind];
  const row = document.createElement('div');
  row.className = 'repeat-row';
  row.dataset.rowKind = kind;

  if (config.reorderable) {
    row.setAttribute('draggable', 'true');
    const handle = document.createElement('span');
    handle.className = 'drag-handle';
    handle.setAttribute('aria-hidden', 'true');
    handle.textContent = '⠿';
    row.appendChild(handle);
  }

  config.fields.forEach((field) => {
    const input = document.createElement('input');
    input.type = field.type;
    input.name = `${kind}_${index}_${field.name}`;
    if (field.placeholder) input.placeholder = field.placeholder;
    if (field.required) input.required = true;
    if (field.min) input.min = field.min;
    if (field.maxlength) input.maxLength = Number(field.maxlength);
    if (field.multiple) input.multiple = true;
    input.setAttribute('aria-label', `${kind} ${field.name}`);
    row.appendChild(input);
  });

  const controls = document.createElement('div');
  controls.className = 'row-controls';
  if (config.reorderable) {
    // Drag needs a pointer; these keep reordering reachable from the keyboard.
    controls.appendChild(moveButton('▲', -1, 'Move up'));
    controls.appendChild(moveButton('▼', 1, 'Move down'));
  }
  const remove = document.createElement('button');
  remove.type = 'button';
  remove.className = 'btn-remove-task';
  remove.setAttribute('aria-label', 'Remove row');
  remove.innerHTML = '&times;';
  remove.addEventListener('click', () => removeRow(remove));
  controls.appendChild(remove);
  row.appendChild(controls);

  return row;
}

function moveButton(glyph, direction, label) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'btn-row-move';
  button.textContent = glyph;
  button.setAttribute('aria-label', label);
  button.addEventListener('click', () => moveRow(button, direction));
  return button;
}

function addRow(kind) {
  const container = rowContainer(kind);
  if (!container) return;
  const row = buildRow(kind, container.children.length);
  container.appendChild(row);
  reindexRows(kind);
  initFieldTooltips(row);
  const first = row.querySelector('input');
  if (first) first.focus();
}

function removeRow(button) {
  const row = button.closest('.repeat-row');
  if (!row) return;
  const kind = row.dataset.rowKind;
  row.remove();
  reindexRows(kind);
}

function moveRow(button, direction) {
  const row = button.closest('.repeat-row');
  if (!row) return;
  const target = direction < 0 ? row.previousElementSibling : row.nextElementSibling;
  if (!target) return;
  if (direction < 0) row.parentNode.insertBefore(row, target);
  else row.parentNode.insertBefore(target, row);
  reindexRows(row.dataset.rowKind);
  button.focus();
}

function reindexRows(kind) {
  const container = rowContainer(kind);
  if (!container) return;
  const rows = Array.from(container.querySelectorAll('.repeat-row'));
  rows.forEach((row, i) => {
    row.dataset.index = i;
    ROW_TYPES[kind].fields.forEach((field) => {
      const input = row.querySelector(`[name$="_${field.name}"]`);
      if (input) input.name = `${kind}_${i}_${field.name}`;
    });
  });
  const counter = document.getElementById(`${kind}-count`);
  if (counter) counter.value = rows.length;
  const empty = document.getElementById(`${kind}-empty`);
  if (empty) empty.hidden = rows.length > 0;
}

/* Drag to reorder. The drop position is worked out from the pointer's Y
   against each row's midpoint, so the row follows the cursor as it moves. */
function initRowDragging(container) {
  const kind = container.dataset.rowKind;
  let dragging = null;

  container.addEventListener('dragstart', (e) => {
    const row = e.target.closest('.repeat-row');
    if (!row) return;
    dragging = row;
    row.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    // Firefox will not start a drag without data being set.
    e.dataTransfer.setData('text/plain', '');
  });

  container.addEventListener('dragend', () => {
    if (!dragging) return;
    dragging.classList.remove('dragging');
    dragging = null;
    reindexRows(kind);
  });

  container.addEventListener('dragover', (e) => {
    if (!dragging) return;
    e.preventDefault();
    const after = rowAfterPointer(container, e.clientY);
    if (after == null) container.appendChild(dragging);
    else container.insertBefore(dragging, after);
  });
}

function rowAfterPointer(container, y) {
  const rows = Array.from(container.querySelectorAll('.repeat-row:not(.dragging)'));
  for (const row of rows) {
    const box = row.getBoundingClientRect();
    if (y < box.top + box.height / 2) return row;
  }
  return null;
}

function initRepeatRows() {
  Object.keys(ROW_TYPES).forEach((kind) => {
    const container = rowContainer(kind);
    if (!container) return;
    container.dataset.rowKind = kind;

    // Server-rendered rows need their buttons wired up too.
    container.querySelectorAll('.repeat-row').forEach((row) => {
      row.querySelectorAll('.btn-remove-task').forEach((b) =>
        b.addEventListener('click', () => removeRow(b)));
      row.querySelectorAll('.btn-row-move').forEach((b) =>
        b.addEventListener('click', () => moveRow(b, b.dataset.direction === 'up' ? -1 : 1)));
    });

    if (ROW_TYPES[kind].reorderable) initRowDragging(container);
    reindexRows(kind);
  });
}

/* Kept because the templates call them from inline onclick handlers. */
function addTaskRow() { addRow('task'); }
function addAttachmentRow() { addRow('attachment'); }

function initAlerts(root) {
  (root || document).querySelectorAll('.alert:not([data-dismiss-armed])').forEach((el) => {
    el.dataset.dismissArmed = 'true';
    setTimeout(() => { el.style.opacity = '0'; }, 4000);
    setTimeout(() => el.remove(), 4400);
  });
}

document.addEventListener('DOMContentLoaded', () => {
  initRepeatRows();
  initAlerts();
});

/* ── Work order form: inherit the asset's location ──
   Picking an asset pulls its location across, the way Maximo derives location
   from the asset. Only re-derives when the asset changes, so a location the
   user set by hand afterwards is left alone. */
function initAssetLocationLink() {
  const assetSelect = document.getElementById('asset_id');
  const locationSelect = document.getElementById('location_id');
  const hint = document.getElementById('location-hint');
  if (!assetSelect || !locationSelect) return;

  // The template is url_for('assets.summary', id=0), i.e. ".../assets/0/summary".
  // Swapping the trailing "/0/summary" is unambiguous; a bare "0" replacement
  // would also match a zero elsewhere in the path.
  const template = assetSelect.dataset.summaryUrl || '';
  const buildUrl = (id) => template.replace(/\/0\/summary$/, `/${id}/summary`);

  function setHint(message) {
    if (!hint) return;
    hint.textContent = message || '';
    hint.hidden = !message;
  }

  assetSelect.addEventListener('change', async () => {
    const assetId = assetSelect.value;
    if (!assetId) {
      setHint('');
      return;
    }

    try {
      const response = await fetch(buildUrl(assetId), {
        headers: { 'Accept': 'application/json' },
      });
      if (!response.ok) return;
      const data = await response.json();

      if (!data.location_id) {
        setHint('That asset has no location set.');
        return;
      }

      // The picker only lists Active locations, so an asset sitting in a
      // retired one would have nothing to select — add it rather than silently
      // doing nothing.
      let option = locationSelect.querySelector(`option[value="${data.location_id}"]`);
      if (!option) {
        option = document.createElement('option');
        option.value = data.location_id;
        option.textContent = data.location_name;
        locationSelect.appendChild(option);
      }
      locationSelect.value = String(data.location_id);
      // The location picker may have been upgraded to a searchable combobox,
      // which only re-reads the select when it sees a change event. Setting
      // .value alone would update the form but not what the user sees.
      locationSelect.dispatchEvent(new Event('change', { bubbles: true }));
      setHint(`Location set from asset: ${data.location_path || data.location_name}`);
    } catch (e) {
      /* offline or the request failed: leave the field as the user left it */
    }
  });
}

document.addEventListener('DOMContentLoaded', initAssetLocationLink);

/* ── Type-to-filter dropdowns ──
   Progressive enhancement over a normal <select data-searchable>. The select
   stays in the DOM, enabled and named, so it still submits and remains the
   single source of truth; this only adds a text input that filters the options
   on a substring match. Without JS you get the plain select. */
function enhanceSearchableSelect(select) {
  if (select.dataset.comboReady) return;
  select.dataset.comboReady = 'true';

  const wrapper = document.createElement('div');
  wrapper.className = 'combo';
  select.parentNode.insertBefore(wrapper, select);
  wrapper.appendChild(select);

  // Keep the select out of the tab order and the accessibility tree; the input
  // below stands in for it.
  select.classList.add('combo-native');
  select.setAttribute('tabindex', '-1');
  select.setAttribute('aria-hidden', 'true');

  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'combo-input';
  input.setAttribute('role', 'combobox');
  input.setAttribute('aria-expanded', 'false');
  input.setAttribute('aria-autocomplete', 'list');
  input.setAttribute('autocomplete', 'off');
  input.placeholder = select.dataset.placeholder || 'Type to filter…';
  if (select.id) input.setAttribute('aria-label', select.id.replace(/_/g, ' '));

  const list = document.createElement('ul');
  list.className = 'combo-list';
  list.setAttribute('role', 'listbox');
  list.hidden = true;

  wrapper.appendChild(input);
  wrapper.appendChild(list);

  let options = [];
  let matches = [];
  let activeIndex = -1;

  function readOptions() {
    options = Array.from(select.options).map((o) => ({
      value: o.value,
      label: o.textContent.trim(),
    }));
  }

  function selectedLabel() {
    const found = options.find((o) => o.value === select.value);
    return found ? found.label : '';
  }

  function showSelected() {
    input.value = selectedLabel();
  }

  function close() {
    list.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    activeIndex = -1;
  }

  function render(filter) {
    const needle = (filter || '').trim().toLowerCase();
    // "contains", not "starts with" — the point is finding an asset by any word
    // in its name, number or location.
    matches = needle
      ? options.filter((o) => o.label.toLowerCase().includes(needle))
      : options.slice();

    list.innerHTML = '';
    if (!matches.length) {
      const li = document.createElement('li');
      li.className = 'combo-empty';
      li.textContent = 'No matches';
      list.appendChild(li);
    } else {
      matches.forEach((option, i) => {
        const li = document.createElement('li');
        li.className = 'combo-option';
        li.setAttribute('role', 'option');
        li.setAttribute('aria-selected', option.value === select.value ? 'true' : 'false');
        li.textContent = option.label;
        li.addEventListener('mousedown', (e) => {
          e.preventDefault();          // keep focus so blur doesn't fire first
          commit(option.value);
        });
        li.addEventListener('mouseenter', () => setActive(i));
        list.appendChild(li);
      });
    }
    list.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    setActive(matches.length ? 0 : -1);
  }

  function setActive(i) {
    activeIndex = i;
    Array.from(list.querySelectorAll('.combo-option')).forEach((li, idx) => {
      li.classList.toggle('active', idx === i);
    });
    const current = list.querySelector('.combo-option.active');
    if (current) current.scrollIntoView({ block: 'nearest' });
  }

  function commit(value) {
    select.value = value;
    showSelected();
    close();
    // Let anything already listening on the select (the asset -> location
    // lookup, for one) react exactly as it would to a native change.
    select.dispatchEvent(new Event('change', { bubbles: true }));
  }

  input.addEventListener('focus', () => { input.select(); render(''); });
  input.addEventListener('input', () => render(input.value));
  input.addEventListener('blur', () => { showSelected(); close(); });

  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (list.hidden) { render(input.value); return; }
      const step = e.key === 'ArrowDown' ? 1 : -1;
      const next = activeIndex + step;
      if (next >= 0 && next < matches.length) setActive(next);
    } else if (e.key === 'Enter') {
      if (!list.hidden && activeIndex >= 0 && matches[activeIndex]) {
        e.preventDefault();          // don't submit the form on the first Enter
        commit(matches[activeIndex].value);
      }
    } else if (e.key === 'Escape') {
      showSelected();
      close();
    }
  });

  // Another script may set select.value or add an option; stay in step.
  select.addEventListener('change', () => { readOptions(); showSelected(); });

  readOptions();
  showSelected();
}

function initSearchableSelects() {
  document.querySelectorAll('select[data-searchable]').forEach(enhanceSearchableSelect);
}

document.addEventListener('DOMContentLoaded', initSearchableSelects);

/* ── Field tooltips ──
   Inputs are often narrower than their contents. Hovering shows the whole value
   as a tooltip, kept in step as the field is edited. An author-supplied title is
   left alone, and password fields are excluded so a secret is never surfaced. */
const TOOLTIP_FIELDS = [
  'input[type="text"]', 'input[type="email"]', 'input[type="number"]',
  'input[type="date"]', 'input[type="search"]', 'input[type="tel"]',
  'input[type="url"]', 'textarea',
].join(', ');

function syncFieldTooltip(field) {
  const value = (field.value || '').trim();
  if (value) field.setAttribute('title', value);
  else field.removeAttribute('title');
}

function initFieldTooltips(root) {
  (root || document).querySelectorAll(TOOLTIP_FIELDS).forEach((field) => {
    if (field.dataset.tooltipReady) return;
    field.dataset.tooltipReady = 'true';

    // Respect a title the template already set; it says something we don't.
    if (field.hasAttribute('title')) return;

    syncFieldTooltip(field);
    field.addEventListener('input', () => syncFieldTooltip(field));
    field.addEventListener('change', () => syncFieldTooltip(field));
  });
}

// Runs last, so fields added by the other initialisers are covered too.
document.addEventListener('DOMContentLoaded', () => initFieldTooltips());

/* ── Off-canvas navigation ──
   Below the layout breakpoint the sidebar is a drawer. Opening it is a body
   class so CSS owns the animation; this only manages state and focus. */
function initMobileNav() {
  const toggle = document.getElementById('nav-toggle');
  const backdrop = document.getElementById('sidebar-backdrop');
  const sidebar = document.getElementById('sidebar');
  if (!toggle || !backdrop || !sidebar) return;

  function setOpen(open) {
    document.body.classList.toggle('nav-open', open);
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
    backdrop.hidden = !open;
    // Stop the page behind the drawer scrolling under it.
    document.body.style.overflow = open ? 'hidden' : '';
  }

  toggle.addEventListener('click', () => {
    setOpen(!document.body.classList.contains('nav-open'));
  });

  backdrop.addEventListener('click', () => setOpen(false));

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && document.body.classList.contains('nav-open')) {
      setOpen(false);
      toggle.focus();
    }
  });

  // Following a link should close the drawer; on a same-page link there is no
  // navigation to do it for us.
  sidebar.querySelectorAll('a').forEach((link) => {
    link.addEventListener('click', () => setOpen(false));
  });

  // Returning to a wide viewport must not leave the drawer state stuck on.
  if (window.matchMedia) {
    const wide = window.matchMedia('(min-width: 861px)');
    const onChange = (e) => { if (e.matches) setOpen(false); };
    if (wide.addEventListener) wide.addEventListener('change', onChange);
    else if (wide.addListener) wide.addListener(onChange);
  }

  setOpen(false);
}

document.addEventListener('DOMContentLoaded', initMobileNav);

/* ── Image lightbox ──
   Thumbnails link to the full image, so without JS a click opens it in a new
   tab. With JS, view it in place instead. */
function initLightbox() {
  const links = document.querySelectorAll('a[data-lightbox]');
  if (!links.length) return;

  let overlay = null;
  let lastFocus = null;

  function close() {
    if (!overlay) return;
    overlay.remove();
    overlay = null;
    document.body.style.overflow = '';
    if (lastFocus) lastFocus.focus();
  }

  function open(href, caption) {
    close();
    lastFocus = document.activeElement;

    overlay = document.createElement('div');
    overlay.className = 'lightbox';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', caption || 'Image');

    const figure = document.createElement('figure');
    const img = document.createElement('img');
    img.src = href;
    img.alt = caption || '';
    figure.appendChild(img);

    if (caption) {
      const cap = document.createElement('figcaption');
      cap.textContent = caption;
      figure.appendChild(cap);
    }

    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'lightbox-close';
    closeBtn.setAttribute('aria-label', 'Close');
    closeBtn.innerHTML = '&times;';
    closeBtn.addEventListener('click', close);

    overlay.appendChild(closeBtn);
    overlay.appendChild(figure);
    // A click anywhere off the image closes; clicks on it should not.
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
    figure.addEventListener('click', (e) => e.stopPropagation());

    document.body.appendChild(overlay);
    document.body.style.overflow = 'hidden';
    closeBtn.focus();
  }

  links.forEach((link) => {
    link.addEventListener('click', (e) => {
      // Let modified clicks (new tab, save) behave normally.
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
      e.preventDefault();
      open(link.getAttribute('href'), link.dataset.caption || '');
    });
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') close();
  });
}

document.addEventListener('DOMContentLoaded', initLightbox);

/* ── In-place actions on the maintenance page ──
   Progressive enhancement: the forms are ordinary POSTs and work without JS.
   With JS they are submitted by fetch and the server's re-rendered markup is
   swapped in, so running a check does not throw you back to the top of the
   page. The server stays the only thing that renders — no duplicate view logic
   lives here. */
function initAsyncActions() {
  const region = document.getElementById('maintenance');
  if (!region) return;

  function setBusy(el, busy) {
    const buttons = el.matches('form') ? el.querySelectorAll('button') : [el];
    buttons.forEach((b) => {
      if (busy) {
        b.dataset.idleLabel = b.textContent;
        b.textContent = el.dataset.busy || 'Working…';
        b.disabled = true;
      } else if (b.dataset.idleLabel) {
        b.textContent = b.dataset.idleLabel;
        b.disabled = false;
      }
    });
    el.classList.toggle('is-busy', busy);
  }

  function swap(html) {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const fresh = doc.getElementById('maintenance');
    if (!fresh) {
      // Not the page we expected — most likely the session expired and this is
      // the login screen. Let the browser navigate properly.
      window.location.reload();
      return;
    }

    const main = document.querySelector('main.content');
    const current = document.getElementById('maintenance');
    current.replaceWith(fresh);

    // Carry over any flash messages the action produced.
    main.querySelectorAll('.alerts').forEach((el) => el.remove());
    const alerts = doc.querySelector('.alerts');
    if (alerts) main.insertBefore(alerts, fresh);

    initFieldTooltips(main);
    initAlerts(main);
  }

  async function run(el, request) {
    setBusy(el, true);
    try {
      const response = await fetch(request.url, request.options);
      if (!response.ok) {
        window.location.reload();
        return;
      }
      swap(await response.text());
    } catch (err) {
      window.location.reload();
    } finally {
      setBusy(el, false);
    }
  }

  document.addEventListener('submit', (e) => {
    const form = e.target.closest('form[data-async]');
    if (!form) return;
    e.preventDefault();
    if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) return;
    run(form, {
      url: form.action,
      options: {
        method: 'POST',
        body: new FormData(form),
        headers: { 'X-Requested-With': 'fetch' },
      },
    });
  });

  document.addEventListener('click', (e) => {
    const link = e.target.closest('a[data-async]');
    if (!link) return;
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
    e.preventDefault();
    run(link, {
      url: link.href,
      options: { headers: { 'X-Requested-With': 'fetch' } },
    });
  });
}

document.addEventListener('DOMContentLoaded', initAsyncActions);
