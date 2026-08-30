/* Minimal DOM stand-in: just enough of the browser API for main.js to load and
   for its pure logic to be exercised under a JS engine with no DOM. */
function FakeClassList(el) {
  this.el = el;
  this.contains = function (c) { return el._classes.indexOf(c) !== -1; };
  this.add = function (c) { if (!this.contains(c)) el._classes.push(c); };
  this.remove = function (c) {
    var i = el._classes.indexOf(c);
    if (i !== -1) el._classes.splice(i, 1);
  };
  this.toggle = function (c, on) { if (on) this.add(c); else this.remove(c); };
}

function FakeEl(tag) {
  this.tagName = (tag || 'div').toUpperCase();
  this.children = [];
  this.dataset = {};
  this._attrs = {};
  this._classes = [];
  this.value = '';
  this.hidden = false;
  this.classList = new FakeClassList(this);
  this._rect = { top: 0, height: 10 };
}

FakeEl.prototype.setAttribute = function (k, v) { this._attrs[k] = String(v); };
FakeEl.prototype.getAttribute = function (k) {
  return Object.prototype.hasOwnProperty.call(this._attrs, k) ? this._attrs[k] : null;
};
FakeEl.prototype.hasAttribute = function (k) {
  return Object.prototype.hasOwnProperty.call(this._attrs, k);
};
FakeEl.prototype.removeAttribute = function (k) { delete this._attrs[k]; };
FakeEl.prototype.addEventListener = function () {};
FakeEl.prototype.appendChild = function (child) { this.children.push(child); return child; };
FakeEl.prototype.getBoundingClientRect = function () { return this._rect; };

/* Selector support is deliberately tiny: '.repeat-row', that plus
   ':not(.dragging)', and '[name$="_field"]' are all main.js asks for. */
FakeEl.prototype.querySelectorAll = function (selector) {
  var wantUndragged = selector.indexOf(':not(.dragging)') !== -1;
  return this.children.filter(function (child) {
    if (selector.indexOf('.repeat-row') !== 0) return false;
    if (!child.classList.contains('repeat-row')) return false;
    if (wantUndragged && child.classList.contains('dragging')) return false;
    return true;
  });
};

FakeEl.prototype.querySelector = function (selector) {
  var m = /\[name\$="([^"]+)"\]/.exec(selector);
  if (m) {
    var suffix = m[1];
    for (var i = 0; i < this.children.length; i++) {
      var name = this.children[i]._attrs.name || this.children[i].name || '';
      if (name.length >= suffix.length && name.slice(-suffix.length) === suffix) {
        return this.children[i];
      }
    }
    return null;
  }
  var all = this.querySelectorAll(selector);
  return all.length ? all[0] : null;
};

var REGISTRY = {};

var document = {
  _ready: [],
  addEventListener: function (name, fn) { if (name === 'DOMContentLoaded') this._ready.push(fn); },
  getElementById: function (id) { return REGISTRY[id] || null; },
  createElement: function (tag) { return new FakeEl(tag); },
  querySelectorAll: function () { return []; },
};

var window = { matchMedia: null };

function makeInput(name, value) {
  var el = new FakeEl('input');
  el.setAttribute('name', name);
  el.name = name;
  el.value = value === undefined ? '' : value;
  return el;
}

function makeRow(kind, fields) {
  var row = new FakeEl('div');
  row.classList.add('repeat-row');
  row.dataset.rowKind = kind;
  Object.keys(fields).forEach(function (field) {
    row.appendChild(makeInput(fields[field], ''));
  });
  return row;
}
