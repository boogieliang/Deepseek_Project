"""JavaScript injected into each frame for scanning and filling.

The snippets are evaluated in an isolated world, so they cannot collide with
the page's own globals.  They still see and modify the shared DOM, and events
dispatched here are observed by the page's framework listeners (React, Vue,
Angular), which is what makes controlled inputs update correctly.
"""
from __future__ import annotations

SCAN_JS = r"""
(() => {
  const MAX_FIELDS = 800;
  const MAX_OPTIONS = 120;
  const clean = (s) => String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
  const norm = (s) => clean(s).toLowerCase()
      .replace(/[\s\*＊:：\-_/\\|（）()\[\]【】<>《》.,，。;；'"“”‘’]+/g, '');

  const visible = (el) => {
    if (!el || !(el instanceof Element)) return false;
    if (el.closest('[aria-hidden="true"]')) return false;
    if (el.hasAttribute('hidden')) return false;
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') return false;
    if (parseFloat(style.opacity || '1') === 0) return false;
    const rect = el.getBoundingClientRect();
    return rect.width >= 2 && rect.height >= 2;
  };

  const deepQuery = (selector, root = document, acc = []) => {
    if (!root || !root.querySelectorAll) return acc;
    for (const el of root.querySelectorAll(selector)) acc.push(el);
    // Open shadow roots need an explicit walk; a plain querySelectorAll stops
    // at the shadow boundary.
    for (const host of root.querySelectorAll('*')) {
      if (host.shadowRoot) deepQuery(selector, host.shadowRoot, acc);
      if (acc.length > MAX_FIELDS * 3) break;
    }
    return acc;
  };

  const textOf = (node) => {
    if (!node) return '';
    const txt = clean(node.innerText || node.textContent || '');
    return txt.length > 180 ? txt.slice(0, 180) : txt;
  };

  const labelFor = (el) => {
    const pieces = [];
    try {
      if (el.labels && el.labels.length) {
        for (const lab of el.labels) pieces.push(textOf(lab));
      }
    } catch (e) {}
    const labelledBy = el.getAttribute('aria-labelledby');
    if (labelledBy) {
      for (const id of labelledBy.split(/\s+/)) {
        const node = document.getElementById(id);
        if (node) pieces.push(textOf(node));
      }
    }
    const wrapper = el.closest('label');
    if (wrapper) pieces.push(textOf(wrapper));
    if (!pieces.filter(Boolean).length) {
      const prev = el.previousElementSibling;
      if (prev && !/^(INPUT|SELECT|TEXTAREA|BUTTON)$/.test(prev.tagName)) {
        const t = textOf(prev);
        if (t && t.length <= 40) pieces.push(t);
      }
      const cell = el.closest('td, th');
      if (cell && cell.previousElementSibling) {
        const t = textOf(cell.previousElementSibling);
        if (t && t.length <= 60) pieces.push(t);
      }
      const fieldset = el.closest('fieldset');
      if (fieldset) {
        const legend = fieldset.querySelector(':scope > legend');
        if (legend) pieces.push(textOf(legend));
      }
      const dt = el.closest('dt, dd');
      if (dt && dt.previousElementSibling) pieces.push(textOf(dt.previousElementSibling));
    }
    return clean(pieces.filter(Boolean).join(' ')).slice(0, 180);
  };

  const ariaFor = (el) => clean(el.getAttribute('aria-label') || '').slice(0, 180);

  const contextFor = (el) => {
    let node = el.parentElement;
    for (let i = 0; i < 3 && node; i += 1) {
      const txt = textOf(node);
      if (txt && txt.length >= norm(el.getAttribute('placeholder') || '').length) return txt;
      node = node.parentElement;
    }
    return '';
  };

  const optionsFor = (el) => {
    const out = [];
    if (el.tagName === 'SELECT') {
      for (const opt of Array.from(el.options).slice(0, MAX_OPTIONS)) {
        out.push({ value: opt.value, text: clean(opt.text), disabled: !!opt.disabled });
      }
    }
    return out;
  };

  const radioOptions = (name) => {
    if (!name) return [];
    const out = [];
    const nodes = document.getElementsByName(name);
    for (const node of Array.from(nodes)) {
      if (node.tagName !== 'INPUT' || (node.type || '').toLowerCase() !== 'radio') continue;
      let text = '';
      try {
        if (node.labels && node.labels.length) text = textOf(node.labels[0]);
      } catch (e) {}
      if (!text) {
        const lab = node.closest('label');
        if (lab) text = textOf(lab);
      }
      if (!text) text = clean(node.value || node.getAttribute('aria-label') || '');
      out.push({ value: String(node.value || ''), text: text || String(node.value || ''), disabled: !!node.disabled });
    }
    return out.slice(0, MAX_OPTIONS);
  };

  const valueFor = (el, type, tag, groupChecked) => {
    if (type === 'checkbox') return el.checked ? '已勾选' : '';
    if (type === 'radio') return groupChecked || '';
    if (tag === 'select') {
      const opt = el.selectedOptions && el.selectedOptions[0];
      if (!opt || !opt.value) return '';
      return clean(opt.text || opt.value);
    }
    if (el.isContentEditable || el.getAttribute('role') === 'textbox') return clean(el.innerText || '').slice(0, 300);
    return clean(el.value || '').slice(0, 300);
  };

  const fields = deepQuery('input, textarea, select, [contenteditable="true"], [contenteditable=""], [role="textbox"], [role="combobox"]');
  const seen = new Set();
  const results = [];
  let seq = 0;

  // Remove markers from earlier scans so the DOM stays tidy.
  for (const old of Array.from(document.querySelectorAll('[data-sff-id], [data-sff-group]'))) {
    old.removeAttribute('data-sff-id');
    old.removeAttribute('data-sff-group');
  }

  for (const el of fields) {
    if (!(el instanceof Element)) continue;
    if (el.closest('template')) continue;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || (tag === 'textarea' ? 'textarea' : 'text')).toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image', 'file', 'range', 'color'].includes(type)) continue;
    if (el.disabled || el.readOnly) continue;
    if (tag === 'input' && el.closest('[role="combobox"]') && el.getAttribute('role') !== 'combobox') {
      // An inner input of a custom combobox is handled by its outer role.
      continue;
    }
    if (tag === 'select' && el.getAttribute('role') === 'combobox') {
      // Native select with a role is still a normal select.
    }
    if (!visible(el)) continue;

    if (type === 'radio') {
      const name = el.getAttribute('name') || '';
      const groupKey = 'radio:' + (name || el.getAttribute('data-sff-group') || String(seq));
      if (seen.has(groupKey)) continue;
      seen.add(groupKey);
      const groupMembers = name
        ? Array.from(document.getElementsByName(name)).filter((n) => n.type === 'radio')
        : [el];
      const visibleMembers = groupMembers.filter((n) => visible(n));
      if (!visibleMembers.length) continue;
      const first = visibleMembers[0];
      const gid = 'sff-g-' + (++seq);
      for (const member of visibleMembers) member.setAttribute('data-sff-group', gid);
      const checked = visibleMembers.find((n) => n.checked);
      const options = radioOptions(name);
      let checkedLabel = '';
      if (checked) {
        try {
          checkedLabel = checked.labels && checked.labels.length ? textOf(checked.labels[0]) : '';
        } catch (e) {}
        if (!checkedLabel) checkedLabel = clean(checked.value || '');
      }
      results.push({
        id: gid,
        tag: 'input',
        type: 'radio',
        role: first.getAttribute('role') || '',
        name: name,
        element_id: first.id || '',
        autocomplete: first.getAttribute('autocomplete') || '',
        placeholder: '',
        aria_label: ariaFor(first),
        label: labelFor(first),
        title: first.getAttribute('title') || '',
        context: contextFor(first),
        value: checkedLabel,
        checked: !!checked,
        required: visibleMembers.some((n) => n.required),
        max_length: 0,
        pattern: '',
        input_mode: '',
        options: options,
        frame_url: location.href,
      });
      if (results.length >= MAX_FIELDS) break;
      continue;
    }

    if (type === 'checkbox') {
      const key = 'check:' + (el.id || el.name || String(seq));
      if (seen.has(key)) continue;
      seen.add(key);
    }

    const id = 'sff-' + (++seq);
    el.setAttribute('data-sff-id', id);
    const options = optionsFor(el);
    results.push({
      id: id,
      tag: tag,
      type: type,
      role: el.getAttribute('role') || '',
      name: el.getAttribute('name') || '',
      element_id: el.id || '',
      autocomplete: el.getAttribute('autocomplete') || '',
      placeholder: clean(el.getAttribute('placeholder') || '').slice(0, 180),
      aria_label: ariaFor(el),
      label: labelFor(el),
      title: clean(el.getAttribute('title') || '').slice(0, 180),
      context: contextFor(el),
      value: valueFor(el, type, tag, ''),
      checked: type === 'checkbox' ? !!el.checked : null,
      required: !!el.required,
      max_length: Number(el.maxLength || 0) || 0,
      pattern: String(el.getAttribute('pattern') || '').slice(0, 120),
      input_mode: String(el.getAttribute('inputmode') || ''),
      options: options,
      frame_url: location.href,
    });
    if (results.length >= MAX_FIELDS) break;
  }

  return { ok: true, url: location.href, title: document.title, fields: results };
})()
"""


FILL_JS = r"""
async (mappings) => {
  const clean = (s) => String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
  const norm = (s) => clean(s).toLowerCase()
      .replace(/[\s\*＊:：\-_/\\|（）()\[\]【】<>《》.,，。;；'"“”‘’]+/g, '')
      .replace(/(请选择|请填写|请输入)/g, '')
      .replace(/(特别行政区|自治区|自治州|省|市|区|县)$/g, '');
  const truthy = (v) => ['1', 'true', 'yes', 'y', 'on', '是', '同意', '已同意', '勾选'].includes(String(v || '').trim().toLowerCase());

  const deepFind = (attr, value, root = document) => {
    if (!root || !root.querySelector) return null;
    try {
      const direct = root.querySelector('[' + attr + '="' + value + '"]');
      if (direct) return direct;
    } catch (e) {}
    for (const host of root.querySelectorAll('*')) {
      if (host.shadowRoot) {
        const found = deepFind(attr, value, host.shadowRoot);
        if (found) return found;
      }
    }
    return null;
  };

  const dispatch = (el, type, bubbles = true) => {
    try { el.dispatchEvent(new Event(type, { bubbles, cancelable: true })); } catch (e) {}
  };

  const setNative = (el, value) => {
    const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype
        : el.tagName === 'SELECT' ? window.HTMLSelectElement.prototype
        : window.HTMLInputElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
    const old = el.value;
    try {
      if (desc && desc.set) desc.set.call(el, value);
      else el.value = value;
    } catch (e) {
      el.value = value;
    }
    if (el._valueTracker) el._valueTracker.setValue(old);
    dispatch(el, 'input');
    dispatch(el, 'change');
    dispatch(el, 'blur');
  };

  const optionLabel = (input) => {
    let text = '';
    try {
      if (input.labels && input.labels.length) text = clean(input.labels[0].innerText || input.labels[0].textContent);
    } catch (e) {}
    if (!text) {
      const lab = input.closest('label');
      if (lab) text = clean(lab.innerText || lab.textContent);
    }
    if (!text) text = clean(input.value || input.getAttribute('aria-label') || '');
    return text;
  };

  const chooseOption = (select, candidates) => {
    const options = Array.from(select.options).filter((o) => !o.disabled);
    const wanted = candidates.map(norm).filter(Boolean);
    for (const want of wanted) {
      for (const opt of options) {
        if (!opt.value) continue;
        if (norm(opt.text) === want || norm(opt.value) === want) return opt;
      }
    }
    for (const want of wanted) {
      if (want.length < 1) continue;
      for (const opt of options) {
        if (!opt.value) continue;
        const text = norm(opt.text);
        const value = norm(opt.value);
        if (text.includes(want) || value.includes(want) || (want.length >= 2 && (want.includes(text) || want.includes(value)))) {
          return opt;
        }
      }
    }
    return null;
  };

  const highlight = (el) => {
    if (!el) return;
    const prevOutline = el.style.outline;
    const prevOffset = el.style.outlineOffset;
    const prevShadow = el.style.boxShadow;
    el.style.outline = '2px solid #22c55e';
    el.style.outlineOffset = '2px';
    el.style.boxShadow = '0 0 0 4px rgba(34,197,94,.18)';
    setTimeout(() => {
      el.style.outline = prevOutline;
      el.style.outlineOffset = prevOffset;
      el.style.boxShadow = prevShadow;
    }, 2600);
  };

  const findOptionElements = () => {
    const selectors = [
      '[role="option"]',
      '.ant-select-item-option', '.el-select-dropdown__item', '.ivu-select-item',
      '.v-select__selections', '.select-option', 'li.option'
    ];
    return Array.from(document.querySelectorAll(selectors.join(',')))
        .filter((el) => el.getBoundingClientRect().height > 0 && el.getBoundingClientRect().width > 0)
        .slice(0, 120);
  };

  const fillCombobox = async (el, mapping) => {
    const candidates = (mapping.candidates || [mapping.value]).filter(Boolean);
    const want = candidates.map(norm).filter(Boolean);
    const input = el.matches('input, textarea') ? el : el.querySelector('input, textarea');
    if (input) {
      setNative(input, mapping.value);
      input.focus();
      dispatch(input, 'keydown');
      dispatch(input, 'keyup');
    }
    try { el.click(); } catch (e) {}
    await new Promise((r) => setTimeout(r, 350));
    const options = findOptionElements();
    let picked = null;
    for (const option of options) {
      const text = norm(option.innerText || option.textContent);
      if (!text) continue;
      if (want.some((w) => text === w || text.includes(w) || w.includes(text))) { picked = option; break; }
    }
    if (picked) {
      try { picked.click(); } catch (e) {}
      await new Promise((r) => setTimeout(r, 150));
      return { ok: true, actual: clean(picked.innerText || picked.textContent), message: '已选择下拉项' };
    }
    if (input) return { ok: true, actual: clean(input.value), message: '已输入，请确认下拉选项' };
    return { ok: false, message: '未找到匹配的下拉选项' };
  };

  const results = [];
  for (const mapping of mappings || []) {
    const id = mapping.id;
    const mappingKind = mapping.kind || 'text';
    let el = null;
    if (mappingKind === 'radio') {
      const group = deepFind('data-sff-group', id);
      if (!group) { results.push({ id, ok: false, message: '页面元素已变化，请重新扫描' }); continue; }
      const name = group.getAttribute('name') || '';
      const members = name ? Array.from(document.getElementsByName(name)).filter((n) => n.type === 'radio') : [group];
      const wanted = (mapping.candidates || [mapping.value]).map(norm).filter(Boolean);
      let target = null;
      for (const member of members) {
        const text = norm(optionLabel(member));
        if (wanted.some((w) => text === w || text.includes(w) || (w.length >= 2 && w.includes(text)))) { target = member; break; }
      }
      if (!target) { results.push({ id, ok: false, message: '没有匹配的选项：' + mapping.value }); continue; }
      if (!target.checked) {
        try { target.click(); } catch (e) { target.checked = true; dispatch(target, 'input'); dispatch(target, 'change'); }
      }
      highlight(target);
      results.push({ id, ok: !!target.checked, actual: optionLabel(target), message: '已选择' });
      continue;
    }

    el = deepFind('data-sff-id', id);
    if (!el) { results.push({ id, ok: false, message: '页面元素已变化，请重新扫描' }); continue; }
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || (tag === 'textarea' ? 'textarea' : 'text')).toLowerCase();

    if (type === 'checkbox') {
      const want = truthy(mapping.value);
      if (mapping.fill_empty_only && el.checked === want) {
        results.push({ id, ok: true, skipped: true, actual: el.checked ? '已勾选' : '', message: '已处于目标状态' });
        continue;
      }
      if (el.checked !== want) {
        try { el.click(); } catch (e) { el.checked = want; dispatch(el, 'input'); dispatch(el, 'change'); }
      }
      highlight(el);
      results.push({ id, ok: el.checked === want, actual: el.checked ? '已勾选' : '', message: '已设置勾选状态' });
      continue;
    }

    if (tag === 'select') {
      if (mapping.fill_empty_only && el.value && el.selectedIndex > 0) {
        results.push({ id, ok: true, skipped: true, actual: clean(el.selectedOptions[0] && el.selectedOptions[0].text), message: '已有内容' });
        continue;
      }
      const opt = chooseOption(el, mapping.candidates || [mapping.value]);
      if (!opt) { results.push({ id, ok: false, message: '下拉框中没有匹配项：' + mapping.value }); continue; }
      const old = el.value;
      try {
        const desc = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value');
        if (desc && desc.set) desc.set.call(el, opt.value); else el.value = opt.value;
      } catch (e) { el.value = opt.value; }
      if (el._valueTracker) el._valueTracker.setValue(old);
      dispatch(el, 'input');
      dispatch(el, 'change');
      highlight(el);
      results.push({ id, ok: true, actual: clean(opt.text), message: '已选择下拉项' });
      continue;
    }

    if (el.isContentEditable || (el.getAttribute('role') === 'textbox' && tag !== 'input' && tag !== 'textarea')) {
      el.focus();
      try {
        const range = document.createRange();
        range.selectNodeContents(el);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        document.execCommand('insertText', false, mapping.value);
      } catch (e) {
        el.textContent = mapping.value;
        dispatch(el, 'input');
      }
      highlight(el);
      results.push({ id, ok: true, actual: clean(el.innerText || el.textContent).slice(0, 200), message: '已输入富文本' });
      continue;
    }

    if (mappingKind === 'combobox') {
      const res = await fillCombobox(el, mapping);
      highlight(el);
      results.push(Object.assign({ id }, res));
      continue;
    }

    if (mapping.fill_empty_only && clean(el.value)) {
      results.push({ id, ok: true, skipped: true, actual: clean(el.value).slice(0, 120), message: '已有内容' });
      continue;
    }
    el.focus();
    setNative(el, mapping.value);
    highlight(el);
    const actual = clean(el.value).slice(0, 200);
    const ok = norm(actual) === norm(mapping.value) || actual.includes(mapping.value) || mapping.value.includes(actual);
    results.push({ id, ok, actual, message: ok ? '已填写' : '已输入，请核对格式' });
  }

  return { ok: true, results };
}
"""


CLEAR_HIGHLIGHTS_JS = r"""
(() => {
  document.querySelectorAll('[data-sff-id], [data-sff-group]').forEach((el) => {
    el.style.outline = '';
    el.style.outlineOffset = '';
    el.style.boxShadow = '';
  });
  return true;
})()
"""
