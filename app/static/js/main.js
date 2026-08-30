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
