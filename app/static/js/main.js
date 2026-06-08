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
