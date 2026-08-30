"""Run the real main.js under a JS engine against a minimal DOM shim.

Server-side tests cannot see the browser code, and three defects have already
shipped in it. These exercise the pure logic directly.
"""
import pathlib

import pytest

dukpy = pytest.importorskip('dukpy', reason='dukpy provides the JS engine')

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHIM = (ROOT / 'tests' / 'js_shim.js').read_text()
MAIN = (ROOT / 'app' / 'static' / 'js' / 'main.js').read_text()


def run(snippet):
    return dukpy.evaljs(SHIM + '\n' + MAIN + '\n' + snippet)


# ── the file itself ────────────────────────────────────────────────────────

def test_main_js_loads_without_error():
    assert run('true') is True


def test_row_types_cover_every_repeatable_list():
    kinds = run('Object.keys(ROW_TYPES)')
    assert set(kinds) == {'task', 'material', 'tool', 'attachment'}


def test_only_ordered_lists_are_reorderable():
    """Attachment order is meaningless, so it gets no drag handle."""
    assert run('ROW_TYPES.task.reorderable') is True
    assert run('ROW_TYPES.material.reorderable') is True
    assert run('ROW_TYPES.tool.reorderable') is True
    assert run('ROW_TYPES.attachment.reorderable') is False


# ── tooltips ───────────────────────────────────────────────────────────────

def test_tooltip_shows_the_field_contents():
    assert run('''
        var f = makeInput('notes', 'A very long value that overflows the box');
        syncFieldTooltip(f);
        f.getAttribute('title');
    ''') == 'A very long value that overflows the box'


def test_tooltip_is_removed_when_the_field_is_emptied():
    assert run('''
        var f = makeInput('notes', 'something');
        syncFieldTooltip(f);
        f.value = '';
        syncFieldTooltip(f);
        f.getAttribute('title');
    ''') is None


def test_whitespace_only_value_gets_no_tooltip():
    assert run('''
        var f = makeInput('notes', '    ');
        syncFieldTooltip(f);
        f.getAttribute('title');
    ''') is None


def test_tooltip_tracks_edits():
    assert run('''
        var f = makeInput('notes', 'first');
        syncFieldTooltip(f);
        f.value = 'second';
        syncFieldTooltip(f);
        f.getAttribute('title');
    ''') == 'second'


def test_password_fields_are_never_tooltipped():
    """A secret must not be surfaced on hover."""
    assert 'password' not in run('TOOLTIP_FIELDS')


# ── row renumbering (what makes drag-to-reorder persist) ───────────────────

def test_reindex_renumbers_inputs_in_dom_order():
    names = run('''
        var c = new FakeEl('div');
        c.appendChild(makeRow('task', {d: 'task_5_description', m: 'task_5_minutes'}));
        c.appendChild(makeRow('task', {d: 'task_2_description', m: 'task_2_minutes'}));
        REGISTRY['task-rows'] = c;
        REGISTRY['task-count'] = new FakeEl('input');
        reindexRows('task');
        c.children.map(function (r) {
          return r.querySelector('[name$="_description"]').name;
        });
    ''')
    assert names == ['task_0_description', 'task_1_description']


def test_reindex_updates_the_hidden_count():
    assert run('''
        var c = new FakeEl('div');
        c.appendChild(makeRow('tool', {d: 'tool_0_description'}));
        c.appendChild(makeRow('tool', {d: 'tool_1_description'}));
        c.appendChild(makeRow('tool', {d: 'tool_2_description'}));
        REGISTRY['tool-rows'] = c;
        var counter = new FakeEl('input');
        REGISTRY['tool-count'] = counter;
        reindexRows('tool');
        counter.value;
    ''') == 3


def test_reindex_toggles_the_empty_hint():
    assert run('''
        var c = new FakeEl('div');
        REGISTRY['material-rows'] = c;
        REGISTRY['material-count'] = new FakeEl('input');
        var empty = new FakeEl('p');
        REGISTRY['material-empty'] = empty;
        reindexRows('material');
        empty.hidden;
    ''') is False


# ── drag insertion point ───────────────────────────────────────────────────

def test_pointer_above_a_row_inserts_before_it():
    assert run('''
        var c = new FakeEl('div');
        var a = makeRow('task', {d: 'task_0_description'});
        var b = makeRow('task', {d: 'task_1_description'});
        a._rect = {top: 0, height: 40};
        b._rect = {top: 40, height: 40};
        c.appendChild(a); c.appendChild(b);
        var after = rowAfterPointer(c, 5);
        after === a;
    ''') is True


def test_pointer_below_everything_appends():
    assert run('''
        var c = new FakeEl('div');
        var a = makeRow('task', {d: 'task_0_description'});
        a._rect = {top: 0, height: 40};
        c.appendChild(a);
        rowAfterPointer(c, 500) === null;
    ''') is True


def test_the_row_being_dragged_is_ignored():
    assert run('''
        var c = new FakeEl('div');
        var a = makeRow('task', {d: 'task_0_description'});
        a.classList.add('dragging');
        a._rect = {top: 0, height: 40};
        c.appendChild(a);
        rowAfterPointer(c, 5) === null;
    ''') is True
