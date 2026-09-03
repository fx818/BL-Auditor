(function (window) {
  'use strict';

  function Filter(table, skipCols) {
    this.table = table;
    this.skipCols = skipCols;
    this.thead = table.querySelector('thead');
    this.tbody = table.querySelector('tbody');
    this.activeFilters = new Map();
    this.openDropdown = null;
    this.onChange = null;
  }

  Filter.prototype.init = function () {
    var self = this;
    var ths = this.thead.querySelectorAll('th');
    ths.forEach(function (th, idx) {
      if (self.skipCols.has(idx)) return;
      self._addFilterButton(th, idx);
    });

    document.addEventListener('click', function (e) {
      if (!self.openDropdown) return;
      var target = e.target;
      if (self.openDropdown.contains(target)) return;
      if (target.classList && target.classList.contains('ef-btn')) return;
      self._closeDropdown();
    });

    window.addEventListener('resize', function () { self._closeDropdown(); });
    window.addEventListener('scroll', function () { self._closeDropdown(); }, true);
  };

  Filter.prototype._addFilterButton = function (th, idx) {
    var self = this;
    th.classList.add('ef-th');
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'ef-btn';
    btn.dataset.col = String(idx);
    btn.textContent = '▾';
    btn.title = 'Filter';
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      self._toggleDropdown(idx, btn);
    });
    th.appendChild(btn);
  };

  Filter.prototype._getColumnValues = function (colIdx) {
    var values = new Set();
    this.tbody.querySelectorAll('tr').forEach(function (tr) {
      var cells = tr.children;
      if (cells.length <= colIdx) return;
      var cell = cells[colIdx];
      if (cell.colSpan > 1) return;
      values.add((cell.textContent || '').trim());
    });
    var arr = Array.from(values);
    arr.sort(function (a, b) {
      return a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' });
    });
    return arr;
  };

  Filter.prototype._toggleDropdown = function (idx, btn) {
    if (this.openDropdown && this.openDropdown.dataset.col === String(idx)) {
      this._closeDropdown();
      return;
    }
    this._closeDropdown();
    this._openDropdown(idx, btn);
  };

  Filter.prototype._openDropdown = function (idx, btn) {
    var self = this;
    var values = this._getColumnValues(idx);
    var active = this.activeFilters.get(idx);

    var dd = document.createElement('div');
    dd.className = 'ef-dropdown';
    dd.dataset.col = String(idx);
    dd.innerHTML = ''
      + '<div class="ef-dd-section">'
      +   '<input type="text" class="ef-search" placeholder="Search...">'
      + '</div>'
      + '<div class="ef-dd-section ef-dd-list">'
      +   '<label class="ef-item ef-item-all">'
      +     '<input type="checkbox" class="ef-cb-all"> <span>(Select All)</span>'
      +   '</label>'
      +   '<div class="ef-items"></div>'
      + '</div>'
      + '<div class="ef-dd-section ef-dd-actions">'
      +   '<button type="button" class="ef-clear">Clear</button>'
      +   '<button type="button" class="ef-apply">Apply</button>'
      + '</div>';

    var itemsEl = dd.querySelector('.ef-items');
    values.forEach(function (val) {
      var display = val === '' ? '(Blanks)' : val;
      var lbl = document.createElement('label');
      lbl.className = 'ef-item';
      lbl.dataset.value = val;
      var checked = active ? active.has(val) : true;
      lbl.innerHTML = '<input type="checkbox" class="ef-cb"' + (checked ? ' checked' : '') + '> <span></span>';
      lbl.querySelector('span').textContent = display;
      itemsEl.appendChild(lbl);
    });

    document.body.appendChild(dd);
    var rect = btn.getBoundingClientRect();
    var ddW = dd.offsetWidth;
    var left = Math.min(window.innerWidth - ddW - 8, Math.max(8, rect.right - ddW));
    var top = Math.min(window.innerHeight - dd.offsetHeight - 8, rect.bottom + 4);
    dd.style.left = left + 'px';
    dd.style.top = top + 'px';

    this.openDropdown = dd;

    function visibleItems() {
      return Array.from(dd.querySelectorAll('.ef-items .ef-item')).filter(function (lbl) {
        return lbl.style.display !== 'none';
      });
    }
    function refreshAllState() {
      var vis = visibleItems();
      var checked = vis.filter(function (l) { return l.querySelector('.ef-cb').checked; });
      var cbAll = dd.querySelector('.ef-cb-all');
      cbAll.checked = vis.length > 0 && checked.length === vis.length;
      cbAll.indeterminate = checked.length > 0 && checked.length < vis.length;
    }

    dd.querySelector('.ef-search').addEventListener('input', function (e) {
      var q = e.target.value.toLowerCase();
      dd.querySelectorAll('.ef-items .ef-item').forEach(function (lbl) {
        var text = lbl.querySelector('span').textContent.toLowerCase();
        lbl.style.display = (!q || text.indexOf(q) !== -1) ? '' : 'none';
      });
      refreshAllState();
    });

    dd.querySelector('.ef-cb-all').addEventListener('change', function (e) {
      var on = e.target.checked;
      visibleItems().forEach(function (lbl) {
        lbl.querySelector('.ef-cb').checked = on;
      });
    });

    dd.querySelector('.ef-apply').addEventListener('click', function () {
      var selected = new Set();
      var total = 0;
      dd.querySelectorAll('.ef-items .ef-item').forEach(function (lbl) {
        total++;
        if (lbl.querySelector('.ef-cb').checked) selected.add(lbl.dataset.value);
      });
      if (selected.size === 0) {
        self.activeFilters.set(idx, selected);
        btn.classList.add('ef-active');
      } else if (selected.size === total) {
        self.activeFilters.delete(idx);
        btn.classList.remove('ef-active');
      } else {
        self.activeFilters.set(idx, selected);
        btn.classList.add('ef-active');
      }
      self.applyFilters();
      self._closeDropdown();
    });

    dd.querySelector('.ef-clear').addEventListener('click', function () {
      self.activeFilters.delete(idx);
      btn.classList.remove('ef-active');
      self.applyFilters();
      self._closeDropdown();
    });

    refreshAllState();
    setTimeout(function () { dd.querySelector('.ef-search').focus(); }, 0);
  };

  Filter.prototype._closeDropdown = function () {
    if (this.openDropdown) {
      this.openDropdown.remove();
      this.openDropdown = null;
    }
  };

  Filter.prototype.applyFilters = function () {
    var self = this;
    this.tbody.querySelectorAll('tr').forEach(function (tr) {
      var cells = tr.children;
      var visible = true;
      self.activeFilters.forEach(function (allowed, idx) {
        if (!visible) return;
        if (cells.length <= idx) return;
        var cell = cells[idx];
        if (cell.colSpan > 1) return;
        var text = (cell.textContent || '').trim();
        if (!allowed.has(text)) visible = false;
      });
      tr.dataset.efHidden = visible ? '' : '1';
      self._updateRowVisibility(tr);
    });
    if (typeof this.onChange === 'function') this.onChange();
  };

  Filter.prototype._updateRowVisibility = function (tr) {
    var ef = tr.dataset.efHidden === '1';
    var ext = tr.dataset.extHidden === '1';
    tr.hidden = ef || ext;
  };

  Filter.prototype.setRowExternalHidden = function (tr, hidden) {
    tr.dataset.extHidden = hidden ? '1' : '';
    this._updateRowVisibility(tr);
  };

  Filter.prototype.refresh = function () {
    if (this.activeFilters.size > 0) this.applyFilters();
    else if (typeof this.onChange === 'function') this.onChange();
  };

  Filter.prototype.visibleRowCount = function () {
    var n = 0;
    this.tbody.querySelectorAll('tr').forEach(function (tr) {
      if (!tr.hidden) n++;
    });
    return n;
  };

  var ExcelFilter = {
    attach: function (options) {
      var tableId = options.tableId;
      var skipColumns = options.skipColumns || [];
      var table = document.getElementById(tableId);
      if (!table) return null;
      var inst = new Filter(table, new Set(skipColumns));
      inst.init();
      return inst;
    }
  };

  window.ExcelFilter = ExcelFilter;
})(window);
