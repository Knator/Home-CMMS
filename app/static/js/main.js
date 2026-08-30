// Dynamic task rows for job plan form
let taskCount = 0;

function initTaskRows() {
  const container = document.getElementById('task-rows');
  if (!container) return;

  const existingRows = container.querySelectorAll('.task-row');
  taskCount = existingRows.length;
  updateTaskCount();
}

function addTaskRow() {
  const container = document.getElementById('task-rows');
  if (!container) return;

  const i = taskCount;
  const row = document.createElement('div');
  row.className = 'task-row';
  row.dataset.index = i;
  row.innerHTML = `
    <input type="text" name="task_${i}_description" placeholder="Task description" required>
    <input type="number" name="task_${i}_minutes" placeholder="Minutes" min="1">
    <button type="button" class="btn-remove-task" onclick="removeTaskRow(this)">×</button>
  `;
  container.appendChild(row);
  taskCount++;
  updateTaskCount();
  row.querySelector('input').focus();
}

function removeTaskRow(btn) {
  const row = btn.closest('.task-row');
  row.remove();
  reindexTaskRows();
}

function reindexTaskRows() {
  const rows = document.querySelectorAll('#task-rows .task-row');
  taskCount = rows.length;
  rows.forEach((row, i) => {
    row.dataset.index = i;
    row.querySelectorAll('input').forEach(input => {
      input.name = input.name.replace(/task_\d+_/, `task_${i}_`);
    });
  });
  updateTaskCount();
}

function updateTaskCount() {
  const input = document.getElementById('task-count');
  if (input) input.value = taskCount;
}

document.addEventListener('DOMContentLoaded', () => {
  initTaskRows();

  // Auto-dismiss alerts after 4 seconds
  document.querySelectorAll('.alert').forEach(el => {
    setTimeout(() => el.style.opacity = '0', 4000);
    setTimeout(() => el.remove(), 4400);
  });
});

/* ── Attachment rows on the work order form ── */
let attachmentCount = 0;

function addAttachmentRow() {
  const container = document.getElementById('attachment-rows');
  if (!container) return;

  const i = attachmentCount;
  const row = document.createElement('div');
  row.className = 'attachment-row';
  row.innerHTML = `
    <input type="file" name="attachment_${i}_file" aria-label="File">
    <input type="text" name="attachment_${i}_name" maxlength="255"
           placeholder="Friendly name (optional)" aria-label="Friendly name">
    <button type="button" class="btn-remove-task" onclick="removeAttachmentRow(this)"
            aria-label="Remove file">&times;</button>
  `;
  container.appendChild(row);
  attachmentCount++;
  syncAttachmentCount();
  row.querySelector('input').focus();
}

function removeAttachmentRow(btn) {
  const row = btn.closest('.attachment-row');
  if (row) row.remove();
  reindexAttachmentRows();
}

function reindexAttachmentRows() {
  const rows = document.querySelectorAll('#attachment-rows .attachment-row');
  attachmentCount = rows.length;
  rows.forEach((row, i) => {
    const file = row.querySelector('input[type="file"]');
    const name = row.querySelector('input[type="text"]');
    if (file) file.name = `attachment_${i}_file`;
    if (name) name.name = `attachment_${i}_name`;
  });
  syncAttachmentCount();
}

function syncAttachmentCount() {
  const input = document.getElementById('attachment-count');
  if (input) input.value = attachmentCount;
}

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
