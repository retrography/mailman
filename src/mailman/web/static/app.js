// mailman — the web interface. Plain JavaScript, no build step.
'use strict';

// ---------------------------------------------------------------- helpers

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'value') el.value = v;
    else if (k === 'checked' || k === 'disabled' || k === 'selected') el[k] = !!v;
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

async function api(method, url, body) {
  const res = await fetch(url, {
    method, headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (e) { /* not JSON */ }
    if (Array.isArray(msg)) msg = 'The server did not understand this request (' + msg.map(m => `${(m.loc || []).slice(-1)[0]}: ${m.msg}`).join('; ') + '). If the app was just updated, reload this page.';
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
  }
  return res.json();
}

function toast(text, ms = 3200) {
  const t = document.getElementById('toast');
  t.textContent = text; t.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { t.hidden = true; }, ms);
}

function modal(content, wide) {
  const o = document.getElementById('overlay');
  o.replaceChildren(h('div', { class: 'modal' + (wide ? ' wide' : '') }, content));
  o.hidden = false;
  o.onclick = e => { if (e.target === o) closeModal(); };
}
function closeModal() { document.getElementById('overlay').hidden = true; }

const fmtTime = ts => ts ? new Date(ts).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' }) : '';
const badge = (text, cls) => h('span', { class: 'badge ' + (cls || String(text).split(/[ :]/)[0]) }, text);
const show = v => v === null || v === undefined ? '— (no value)' : v === '' ? '"" (empty)' : typeof v === 'object' ? JSON.stringify(v) : String(v);
const main = () => document.getElementById('main');
const wrap = fn => async (...a) => { try { return await fn(...a); } catch (e) { toast(e.message, 6000); } };

let CAT = null;   // facts, lists, outcomes, stages, labels, stats
async function catalogue(force) {
  if (!CAT || force) CAT = await api('GET', 'api/catalogue');
  return CAT;
}

// ---------------------------------------------------------------- the YAML editor

// Wherever configuration is shown as text it is this editor: highlighted, and checked to be valid YAML as you
// type. `.value` reads and writes the text; `await .ensureValid()` throws while it is not valid YAML.
function esc(s) { return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

function highlightYaml(text) {
  const token = /("(?:[^"\\]|\\.)*"|'(?:[^']|'')*')|(\{\{.*?\}\})|(\$[A-Za-z_][\w.]*)|((?:^|[{,])\s*(?:- +)*)([A-Za-z_$][\w.$\/ -]*?)(?=:(?:\s|$))|(^\s*(?:- +)+)|\b(true|false|null|yes|no)\b|(-?\b\d+(?:\.\d+)?\b)/g;
  return text.split('\n').map(line => {
    let code = line, comment = '';
    let q = null;
    for (let i = 0; i < line.length; i++) {   // a # outside quotes, at the start or after a blank, starts a comment
      const c = line[i];
      if (q) { if (c === q) q = null; } else if (c === '"' || c === "'") q = c;
      else if (c === '#' && (i === 0 || /\s/.test(line[i - 1]))) { code = line.slice(0, i); comment = line.slice(i); break; }
    }
    let out = '', last = 0, m;
    token.lastIndex = 0;
    while ((m = token.exec(code))) {
      out += esc(code.slice(last, m.index));
      if (m[1]) out += `<span class="y-s">${esc(m[1])}</span>`;
      else if (m[2] || m[3]) out += `<span class="y-p">${esc(m[2] || m[3])}</span>`;
      else if (m[5] !== undefined) out += esc(m[4]).replace(/-/g, '<span class="y-d">-</span>') + `<span class="y-k">${esc(m[5])}</span>`;
      else if (m[6]) out += esc(m[6]).replace(/-/g, '<span class="y-d">-</span>');
      else out += `<span class="y-n">${esc(m[7] || m[8])}</span>`;
      last = m.index + m[0].length;
      if (m[0] === '') token.lastIndex++;
    }
    out += esc(code.slice(last));
    return out + (comment ? `<span class="y-c">${esc(comment)}</span>` : '');
  }).join('\n') + '\n';
}

function yamlEditor({ value = '', rows = 14, onchange } = {}) {
  const pre = h('pre', { class: 'y-view', 'aria-hidden': 'true' });
  const area = h('textarea', { class: 'y-input', spellcheck: 'false', wrap: 'off', rows });
  const msg = h('div', { class: 'y-msg' });
  const box = h('div', { class: 'y-box' }, pre, area);
  const el = h('div', { class: 'y-editor' }, box, msg);
  let timer = null, seq = 0;
  el.valid = true;
  const fit = () => {   // as tall as its text, up to most of the window
    const lines = area.value.split('\n').length + 1;
    area.rows = Math.max(Math.min(rows, lines), Math.min(lines, Math.floor(window.innerHeight * 0.72 / 19.4)));
  };
  const paint = () => { fit(); pre.innerHTML = highlightYaml(area.value); pre.scrollTop = area.scrollTop; pre.scrollLeft = area.scrollLeft; };
  const validate = async () => {
    const mine = ++seq, text = area.value;
    try {
      await api('POST', 'api/yaml/load', { text });
      if (mine !== seq) return el.valid;
      el.valid = true; msg.textContent = ''; el.classList.remove('invalid');
    } catch (e) {
      if (mine !== seq) return el.valid;
      el.valid = false; msg.textContent = e.message.replace(/\s+/g, ' ').slice(0, 260); el.classList.add('invalid');
    }
    return el.valid;
  };
  area.addEventListener('input', () => { paint(); clearTimeout(timer); timer = setTimeout(validate, 300); if (onchange) onchange(); });
  area.addEventListener('scroll', () => { pre.scrollTop = area.scrollTop; pre.scrollLeft = area.scrollLeft; });
  area.addEventListener('keydown', e => {
    if (e.key !== 'Tab') return;
    e.preventDefault();
    const s = area.selectionStart;
    area.setRangeText('  ', s, area.selectionEnd, 'end'); paint();
  });
  Object.defineProperty(el, 'value', { get: () => area.value, set: v => { area.value = v ?? ''; paint(); clearTimeout(timer); validate(); } });
  el.ensureValid = async () => { clearTimeout(timer); if (!(await validate())) throw new Error('This is not valid YAML yet: ' + msg.textContent); return area.value; };
  el.value = value;
  return el;
}

// ---------------------------------------------------------------- forms

// A form is a list of field descriptions rendered over a plain object, which the fields edit in place.
// Field types: text · prose · regex · number · bool · select · smart (a scalar: number, true/false, null, text)
//   strings (a list, one per line) · stringsOrOne · map (key → text) · object {fields} · array {fields | item}
//   cond (a condition tree) · detector / detectors (a fact detector, or a list of them) · yaml (anything)
const isObj = v => v !== null && typeof v === 'object' && !Array.isArray(v);
const smartParse = s => { s = s.trim(); if (s === '') return undefined; if (s === 'true') return true; if (s === 'false') return false; if (s === 'null') return null; return /^-?\d+(\.\d+)?$/.test(s) ? Number(s) : s.replace(/^"(.*)"$/, '$1'); };
const smartShow = v => v === undefined ? '' : v === null ? 'null' : typeof v === 'string' && /^(true|false|null|-?\d+(\.\d+)?)$/.test(v) ? `"${v}"` : String(v);

function put(obj, key, value, keep) {
  const empty = value === undefined || value === '' || (Array.isArray(value) && !value.length) || (isObj(value) && !Object.keys(value).length);
  if (empty && !keep) delete obj[key]; else obj[key] = value;
}

function formFor(fields, obj, ctx) {
  const box = h('div', { class: 'form' });
  for (const f of fields) {
    if (f.show && !f.show(obj)) continue;
    box.append(fieldFor(f, obj, ctx));
  }
  const known = new Set(fields.map(f => f.key));
  const extra = Object.keys(obj).filter(k => !known.has(k) && !(ctx.ignore || []).includes(k));
  if (extra.length) {   // nothing is hidden or lost: keys the form does not know are shown as YAML
    for (const k of extra) box.append(fieldFor({ key: k, label: k + ' (not in the form)', type: 'yaml' }, obj, ctx));
  }
  return box;
}

function fieldFor(f, obj, ctx) {
  const v = obj[f.key];
  const set = (val, redraw) => { put(obj, f.key, val, f.keep); if (ctx.onchange) ctx.onchange(); if (redraw || f.redraw) ctx.redraw(); };
  const label = (control, cls) => h('label', { class: 'f ' + (cls || '') + (f.wide ? ' wide' : '') }, h('span', {}, f.label || f.key, f.help ? h('span', { class: 'hint' }, ' — ' + f.help) : ''), control);
  const dl = options => { const id = 'dl-' + Math.random().toString(36).slice(2); return [id, h('datalist', { id }, options.map(o => h('option', { value: o })))]; };
  switch (f.type) {
    case 'prose':
      return label(h('textarea', { class: 'prose', rows: f.rows || 3, oninput: e => set(e.target.value) }, v ?? ''), 'wide');
    case 'regex':
      return label(h('input', { class: 'mono', value: v ?? '', spellcheck: 'false', onchange: e => set(e.target.value) }), 'wide');
    case 'number':
      return label(h('input', { type: 'number', step: 'any', value: v ?? '', onchange: e => set(e.target.value === '' ? undefined : Number(e.target.value)) }));
    case 'bool':
      return h('label', { class: 'f check' }, h('input', { type: 'checkbox', checked: v === undefined ? !!f.default : !!v, onchange: e => { if (e.target.checked === !!f.default && !f.keep) { delete obj[f.key]; if (ctx.onchange) ctx.onchange(); if (f.redraw) ctx.redraw(); } else set(e.target.checked); } }), h('span', {}, f.label || f.key, f.help ? h('span', { class: 'hint' }, ' — ' + f.help) : ''));
    case 'select': {
      const opts = typeof f.options === 'function' ? f.options(ctx) : f.options;
      return label(h('select', { onchange: e => set(e.target.value === '' ? undefined : e.target.value, true) },
        f.optional ? h('option', { value: '' }, f.optional === true ? '—' : f.optional) : null,
        opts.map(o => { const [val, text] = Array.isArray(o) ? o : [o, o]; return h('option', { value: val, selected: val === (v ?? f.default) }, text); })));
    }
    case 'smart':
      return label(h('input', { value: smartShow(v), placeholder: f.placeholder || 'text, a number, true / false, null, or $fact', onchange: e => { const p = smartParse(e.target.value); if (p === null) { obj[f.key] = null; if (ctx.onchange) ctx.onchange(); } else set(p); } }));
    case 'strings': case 'stringsOrOne': {
      const list = v === undefined ? [] : [].concat(v);
      const [id, d] = f.suggest ? dl(typeof f.suggest === 'function' ? f.suggest(ctx) : f.suggest) : [null, null];
      if (f.inline) {
        return label([h('input', { list: id, value: list.join(', '), placeholder: 'comma-separated', onchange: e => { const a = e.target.value.split(',').map(s => s.trim()).filter(Boolean); set(f.type === 'stringsOrOne' && a.length === 1 ? a[0] : a); } }), d]);
      }
      return label(h('textarea', { class: f.mono ? '' : 'prose', rows: Math.min(8, Math.max(2, list.length + 1)), placeholder: 'one per line', onchange: e => { const a = e.target.value.split('\n').map(s => s.trim()).filter(Boolean); set(f.type === 'stringsOrOne' && a.length === 1 ? a[0] : a); } }, list.join('\n')), 'wide');
    }
    case 'map': {
      const m = isObj(v) ? v : {};
      const rows = h('div', { class: 'rows' });
      const draw = () => rows.replaceChildren(...Object.keys(m).map(k => h('div', { class: 'maprow' },
        h('input', { value: k, class: 'k', onchange: e => { const nk = e.target.value.trim(); if (!nk || nk === k) return; const entries = Object.entries(m).map(([a, b]) => [a === k ? nk : a, b]); for (const a of Object.keys(m)) delete m[a]; for (const [a, b] of entries) m[a] = b; set(m); draw(); } }),
        f.long ? h('textarea', { class: 'prose grow', rows: 2, oninput: e => { m[k] = e.target.value; set(m); } }, isObj(m[k]) || Array.isArray(m[k]) ? JSON.stringify(m[k]) : m[k] ?? '')
          : h('input', { class: 'grow' + (f.mono ? ' mono' : ''), value: Array.isArray(m[k]) ? m[k].join(', ') : isObj(m[k]) ? JSON.stringify(m[k]) : smartShow(m[k]), onchange: e => { m[k] = Array.isArray(m[k]) ? e.target.value.split(',').map(s => s.trim()).filter(Boolean) : f.mono ? e.target.value : smartParse(e.target.value) ?? ''; set(m); } }),
        h('button', { class: 'small danger', onclick: () => { delete m[k]; set(m); draw(); } }, '✕'))),
        h('button', { class: 'small', onclick: () => { let n = 'new', i = 1; while (n in m) n = 'new' + (++i); m[n] = ''; set(m); draw(); } }, '+ ' + (f.add || 'entry')));
      draw();
      return h('fieldset', { class: 'f wide' }, h('legend', {}, f.label || f.key, f.help ? h('span', { class: 'hint' }, ' — ' + f.help) : ''), rows);
    }
    case 'object': {
      if (!isObj(obj[f.key])) return h('div', { class: 'f wide' }, h('button', { class: 'small', onclick: () => { obj[f.key] = {}; if (ctx.onchange) ctx.onchange(); ctx.redraw(); } }, '+ ' + (f.label || f.key)));
      const inner = { ...ctx, onchange: () => { put(obj, f.key, obj[f.key], f.keep); if (ctx.onchange) ctx.onchange(); } };
      return h('fieldset', { class: 'f wide' }, h('legend', {}, f.label || f.key, f.help ? h('span', { class: 'hint' }, ' — ' + f.help) : ''), formFor(typeof f.fields === 'function' ? f.fields(obj[f.key]) : f.fields, obj[f.key], inner));
    }
    case 'array': {
      const list = Array.isArray(v) ? v : [];
      const rows = h('div', { class: 'rows' });
      const commit = () => { set(list); };
      const draw = () => rows.replaceChildren(...list.map((item, i) => {
        const itemCtx = { ...ctx, onchange: commit, redraw: draw };
        const body = f.item ? fieldFor({ ...f.item, key: i, label: f.item.label || '', keep: true }, list, itemCtx) : formFor(typeof f.fields === 'function' ? f.fields(item) : f.fields, item, itemCtx);
        return h('div', { class: 'arrayrow' }, h('div', { class: 'grow' }, body),
          h('div', { class: 'updown' }, h('button', { disabled: i === 0, onclick: () => { list.splice(i - 1, 0, list.splice(i, 1)[0]); commit(); draw(); } }, '▲'),
            h('button', { disabled: i === list.length - 1, onclick: () => { list.splice(i + 1, 0, list.splice(i, 1)[0]); commit(); draw(); } }, '▼'),
            h('button', { class: 'danger', onclick: () => { list.splice(i, 1); commit(); draw(); } }, '✕')));
      }), h('button', { class: 'small', onclick: () => { list.push(f.item ? (f.blank ?? '') : JSON.parse(JSON.stringify(f.blank || {}))); commit(); draw(); } }, '+ ' + (f.add || 'item')));
      draw();
      return h('fieldset', { class: 'f wide' }, h('legend', {}, f.label || f.key, f.help ? h('span', { class: 'hint' }, ' — ' + f.help) : ''), rows);
    }
    case 'cond': {
      const tree = toTree(isObj(v) ? v : {});
      const holder = h('div', {});
      const draw = () => holder.replaceChildren(condEditor(tree, ctx.cat, redraw => { put(obj, f.key, toDict(tree), f.keep); if (ctx.onchange) ctx.onchange(); if (redraw) draw(); }));
      draw();
      return h('fieldset', { class: 'f wide' }, h('legend', {}, f.label || f.key), holder);
    }
    case 'detector':
      return h('fieldset', { class: 'f wide' }, h('legend', {}, f.label || 'detector'), detectorForm(obj[f.key], ctx));
    case 'detectors': {
      const list = Array.isArray(v) ? v : [];
      const rows = h('div', { class: 'rows' });
      const commit = () => set(list);
      const draw = () => rows.replaceChildren(...list.map((item, i) => {
        const itemCtx = { ...ctx, onchange: commit, redraw: draw };
        const isRef = typeof item === 'string';
        const [id, d] = dl(ctx.cat.facts.map(x => x.name));
        return h('div', { class: 'arrayrow' }, h('div', { class: 'grow' },
          h('div', { class: 'row' }, h('select', { onchange: e => { list[i] = e.target.value === 'ref' ? '' : { pattern: '', in: 'subject' }; commit(); draw(); } },
            h('option', { value: 'ref', selected: isRef }, 'another flag or field'), h('option', { value: 'det', selected: !isRef }, 'a detector')),
            isRef ? [h('input', { class: 'grow', list: id, value: item, onchange: e => { list[i] = e.target.value.trim(); commit(); } }), d] : null),
          isRef ? null : detectorForm(list[i], itemCtx)),
          h('div', { class: 'updown' }, h('button', { class: 'danger', onclick: () => { list.splice(i, 1); commit(); draw(); } }, '✕')));
      }), h('button', { class: 'small', onclick: () => { list.push(''); commit(); draw(); } }, '+ ' + (f.add || 'item')));
      draw();
      return h('fieldset', { class: 'f wide' }, h('legend', {}, f.label || f.key, f.help ? h('span', { class: 'hint' }, ' — ' + f.help) : ''), rows);
    }
    case 'yaml': {
      const ed = yamlEditor({ rows: 5, onchange: () => { ed.pending = true; } });
      if (v !== undefined) api('POST', 'api/yaml/dump', { data: v }).then(r => { ed.value = r.text; });
      (ctx.yamlFields = ctx.yamlFields || []).push(async () => { if (!ed.pending) return; const text = await ed.ensureValid(); put(obj, f.key, text.trim() ? (await api('POST', 'api/yaml/load', { text })).data : undefined, f.keep); });
      return h('div', { class: 'f wide' }, h('span', { class: 'flabel' }, f.label || f.key), ed);
    }
    default: {   // text
      const [id, d] = f.suggest ? dl(typeof f.suggest === 'function' ? f.suggest(ctx) : f.suggest) : [null, null];
      return label([h('input', { list: id, class: f.mono ? 'mono' : '', value: v ?? '', placeholder: f.placeholder || '', onchange: e => set(f.raw ? e.target.value : e.target.value.trim(), false) }), d], f.wide ? 'wide' : '');
    }
  }
}

// What a form infers when it has no description: a form for any value, by its shape.
function inferFields(value, labels = {}) {
  return Object.entries(value).map(([key, v]) => {
    const base = { key, label: labels[key] || key.replace(/_/g, ' '), keep: true };
    if (typeof v === 'boolean') return { ...base, type: 'bool' };
    if (typeof v === 'number') return { ...base, type: 'number' };
    if (typeof v === 'string') return { ...base, type: v.length > 70 || v.includes('\n') ? 'prose' : 'text', wide: v.length > 40, raw: true };
    if (Array.isArray(v)) {
      if (v.every(x => isObj(x))) return { ...base, type: 'array', fields: item => inferFields({ ...Object.fromEntries(v.flatMap(x => Object.keys(x)).map(k => [k, ''])), ...item }, labels).map(x => ({ ...x, keep: false })), blank: Object.fromEntries(Object.keys(v[0] || {}).map(k => [k, Array.isArray(v[0][k]) ? [] : ''])), add: 'entry' };
      return { ...base, type: 'strings', wide: true, inline: v.join(', ').length < 90 && v.every(x => typeof x !== 'string' || x.length < 30) };
    }
    if (isObj(v)) {
      if (Object.values(v).every(x => typeof x === 'string') && Object.keys(v).length > 3) return { ...base, type: 'map', mono: true };
      return { ...base, type: 'object', fields: inner => inferFields(inner, labels) };
    }
    return { ...base, type: 'smart' };
  });
}

// ---------------------------------------------------------------- check → review → save

// Every change to a configuration file passes through here: what it would do is shown first.
async function reviewAndSave(file, text, after) {
  modal(h('div', {}, h('h2', {}, 'Checking the change…'), h('p', { class: 'muted' }, 'Running it over the test set.')));
  let r;
  try { r = await api('POST', `api/check/${file}`, { text }); } catch (e) { closeModal(); toast(e.message, 6000); return; }
  const body = h('div', {});
  body.append(h('h2', {}, `Save ${file}.yaml?`));
  if (r.problems.length) {
    body.append(h('div', { class: 'banner bad' }, h('b', {}, 'This cannot be saved:'),
      h('ul', {}, r.problems.map(p => h('li', {}, p)))));
  }
  if (r.stale_questions && r.stale_questions.length) {
    const b = h('div', { class: 'banner warn' },
      `The classifier's stored answers no longer match ${r.stale_questions.length} question(s) as they would now be sent: `,
      h('b', {}, r.stale_questions.join(', ')), '. The comparison below still uses the old answers. ');
    b.append(h('button', { class: 'small', onclick: wrap(async ev => {
      ev.target.disabled = true; ev.target.textContent = 'asking the classifier…';
      const res = await api('POST', `api/reask/${file}`, { text, questions: r.stale_questions });
      toast(`asked ${res.asked} emails (${res.tokens.toLocaleString()} tokens)`);
      reviewAndSave(file, text, after);
    }) }, `Re-ask these on the ${r.total} test emails`));
    body.append(b);
  }
  if (r.expectations_broken && r.expectations_broken.length) {
    body.append(h('div', { class: 'banner bad' }, h('b', {}, `${r.expectations_broken.length} email(s) you marked would not get the outcome you expect:`),
      h('ul', {}, r.expectations_broken.map(x => h('li', {}, `${x.subject} — expected ${x.expected.decision}, gets ${x.after.outcome} (${x.after.rule || 'no rule'})`)))));
  }
  if (!r.problems.length) {
    body.append(h('div', { class: 'banner ' + (r.changes.length ? 'warn' : 'ok') },
      r.changes.length ? `${r.changes.length} of ${r.total} test emails get a different result:` : `No test email changes its result (${r.total} checked).`));
  }
  if (r.changes.length) {
    body.append(h('div', { class: 'tablewrap', style: 'max-height:46vh' }, h('table', {},
      h('thead', {}, h('tr', {}, h('th', {}, 'From'), h('th', {}, 'Subject'), h('th', {}, 'Now'), h('th', {}, ''), h('th', {}, 'After the change'))),
      h('tbody', {}, r.changes.map(c => h('tr', {},
        h('td', { class: 'clip', style: 'width:22%' }, c.sender), h('td', { class: 'clip', style: 'width:30%' }, c.subject),
        h('td', {}, resultCell(c.before)), h('td', { class: 'arrow' }, '→'), h('td', {}, resultCell(c.after))))))));
  }
  body.append(h('div', { class: 'foot' },
    h('button', { onclick: closeModal }, 'Cancel'),
    h('button', { class: 'primary', disabled: !r.ok, onclick: wrap(async () => {
      const s = await api('PUT', `api/config/${file}`, { text });
      if (!s.ok) { toast('not saved: ' + s.problems.join('; '), 7000); return; }
      closeModal(); toast(`${file}.yaml saved`); CAT = null; if (after) after();
    }) }, 'Save')));
  modal(body, true);
}

function resultCell(x) {
  return h('span', {}, badge(x.outcome), ' ', x.labels.map(l => [badge(l, 'label'), ' ']),
    h('div', { class: 'muted', style: 'font-size:12px' }, x.rule || 'no rule'));
}

// ---------------------------------------------------------------- Log

const logState = { q: '', decision: '', rule: '', days: 30, offset: 0 };

async function viewLog() {
  const el = main();
  el.replaceChildren(h('h1', {}, 'Logs'), h('p', { class: 'lead' }, 'Every email mailman handled, and why. Click a row for the flags behind the decision.'));
  const table = h('div', { class: 'tablewrap' });
  const count = h('span', { class: 'muted' });
  const load = wrap(async () => {
    const p = new URLSearchParams({ ...logState, limit: 200 });
    const r = await api('GET', 'api/log?' + p);
    count.textContent = `${r.total.toLocaleString()} entries` + (r.total > 200 ? ` · showing ${logState.offset + 1}–${logState.offset + r.rows.length}` : '');
    table.replaceChildren(h('table', {},
      h('thead', {}, h('tr', {}, ['When', 'Mailbox', 'From', 'Subject', 'Outcome', 'Rule', 'Done'].map(t => h('th', {}, t)))),
      h('tbody', {}, r.rows.map(x => h('tr', { class: 'click', onclick: () => logDetail(x.row) },
        h('td', { style: 'white-space:nowrap' }, fmtTime(x.ts)), h('td', { style: 'white-space:nowrap' }, x.mailbox),
        h('td', { class: 'clip', style: 'width:20%' }, x.sender),
        h('td', { class: 'clip', style: 'width:32%' }, x.subject), h('td', {}, x.decision ? badge(x.decision) : ''),
        h('td', { class: 'clip', style: 'width:18%' }, x.rule), h('td', {}, x.error ? badge('error') : (x.dry_run ? badge(x.action, 'jev') : x.action)))))));
  });
  const deb = fn => { let t; return () => { clearTimeout(t); t = setTimeout(fn, 250); }; };
  const cat = await catalogue();
  el.append(h('div', { class: 'row', style: 'margin-bottom:10px' },
    h('input', { class: 'grow', placeholder: 'Search sender or subject', value: logState.q }),
    h('select', { onchange: e => { logState.decision = e.target.value; logState.offset = 0; load(); } },
      h('option', { value: '' }, 'any outcome'), [...Object.keys(cat.outcomes), 'undo'].map(o => h('option', { value: o, selected: o === logState.decision }, o))),
    h('input', { placeholder: 'Rule contains…', value: logState.rule, oninput: e => { logState.rule = e.target.value; logState.offset = 0; clearTimeout(viewLog.t); viewLog.t = setTimeout(load, 250); } }),
    h('select', { onchange: e => { logState.days = +e.target.value; logState.offset = 0; load(); } },
      [1, 7, 30, 90, 3650].map(d => h('option', { value: d, selected: d === logState.days }, d === 3650 ? 'all time' : `last ${d} day${d > 1 ? 's' : ''}`))),
    h('button', { onclick: () => { logState.offset = Math.max(0, logState.offset - 200); load(); } }, '‹'),
    h('button', { onclick: () => { logState.offset += 200; load(); } }, '›'), count));
  const search = el.querySelector('input');
  search.oninput = () => { logState.q = search.value; logState.offset = 0; clearTimeout(viewLog.t2); viewLog.t2 = setTimeout(load, 250); };
  el.append(table);
  load();
}

function condText(c, indent = '') {
  if (!c) return '';
  return Object.entries(c).map(([k, v]) => {
    if (k === 'all' || k === 'any') return `${indent}${k} of:\n` + v.map(x => condText(x, indent + '  ')).join('\n');
    if (k === 'not') return `${indent}not:\n` + condText(v, indent + '  ');
    return `${indent}${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`;
  }).join('\n');
}

async function logDetail(row) {
  const d = await api('GET', `api/log/${row}`);
  const cat = await catalogue();
  const body = h('div', {});
  body.append(h('h2', {}, d.subject || '(no subject)'),
    h('dl', { class: 'kv' }, h('dt', {}, 'From'), h('dd', {}, d.sender), h('dt', {}, 'When'), h('dd', {}, fmtTime(d.ts)),
      h('dt', {}, 'Outcome'), h('dd', {}, badge(d.decision || '—'), ' ', d.dry_run ? badge('dry run', 'jev') : ''),
      h('dt', {}, 'Rule'), h('dd', {}, d.rule || 'no rule matched'), h('dt', {}, `Done in ${d.mailbox}`), h('dd', {}, d.action),
      d.error ? [h('dt', {}, 'Error'), h('dd', {}, d.error)] : null, h('dt', {}, 'Classifier tokens'), h('dd', {}, d.tokens || 0)));
  if (d.rules.length) {
    body.append(h('h3', {}, 'Why — the rule(s) as they are defined now'));
    for (const r of d.rules) {
      body.append(h('div', { class: 'panel' }, h('b', {}, r.name), ' ', badge(r.stage), ' ', r.then ? badge(r.then) : badge(r.label, 'label'),
        h('div', { class: 'muted' }, r.note || ''),
        h('pre', {}, 'WHEN\n' + condText(r.when, '  ') + (r.unless ? '\nUNLESS\n' + condText(r.unless, '  ') : ''))));
    }
  }
  const facts = Object.entries(d.facts);
  if (facts.length) body.append(h('h3', {}, 'Flags the rules read'), h('dl', { class: 'kv' }, facts.map(([k, v]) => [h('dt', {}, k), h('dd', { class: 'mono' }, show(v))])));
  const jev = Object.entries(d.jev);
  if (jev.length) {
    body.append(h('h3', {}, "The classifier's answers"), h('dl', { class: 'kv' }, jev.map(([k, v]) => [h('dt', {}, k),
      h('dd', {}, typeof v === 'object' && v ? [h('b', {}, v.choice), ` · ${Math.round(v.conf * 100)}%  `, h('span', { class: 'muted' }, Object.entries(v.top || {}).map(([o, p]) => `${o} ${Math.round(p * 100)}%`).join(' · '))] : `p(yes) = ${v}`)])));
  }
  const fb = h('div', { hidden: true, class: 'panel' },
    h('p', { class: 'muted', style: 'margin-top:0' }, 'The email joins the test set with the outcome you expected. Every later change to the configuration is checked against it.'),
    h('div', { class: 'row' },
      h('select', { id: 'fb-outcome' }, Object.keys(cat.outcomes).map(o => h('option', {}, o))),
      h('input', { id: 'fb-labels', class: 'grow', placeholder: 'expected labels, comma-separated (optional)' }),
      h('button', { class: 'primary', onclick: wrap(async () => {
        const labels = document.getElementById('fb-labels').value.split(',').map(s => s.trim()).filter(Boolean);
        await api('POST', `api/log/${row}/feedback`, { decision: document.getElementById('fb-outcome').value, labels, note: '' });
        toast('added to the test set'); closeModal();
      }) }, 'Add to the test set')));
  const spec = d.decision && cat.outcomes[d.decision] !== undefined;
  body.append(fb, h('div', { class: 'foot' },
    h('button', { onclick: () => { fb.hidden = !fb.hidden; } }, 'This was wrong…'),
    h('button', { disabled: !spec || d.dry_run || d.decision === 'keep' && !d.action.startsWith('label'), onclick: wrap(async () => {
      await api('POST', `api/log/${row}/undo`); toast(`undone in ${d.mailbox}`); closeModal(); viewLog();
    }) }, `Undo in ${d.mailbox}`),
    h('button', { class: 'primary', onclick: closeModal }, 'Close')));
  modal(body, true);
}

// ---------------------------------------------------------------- conditions: dict ⇄ tree

function toTree(cond) {   // {fact: want, any: [...]} → {kind:'group', op:'all', kids:[…]}
  const kids = [];
  for (const [k, v] of Object.entries(cond || {})) {
    if (k === 'all') v.forEach(c => kids.push(simplify(toTree(c))));
    else if (k === 'any') kids.push({ kind: 'group', op: 'any', kids: v.map(c => simplify(toTree(c))) });
    else if (k === 'not') kids.push({ kind: 'group', op: 'not', kids: toTree(v).kids });
    else if (k === 'sender_in') kids.push({ kind: 'leaf', fact: 'sender_in', cmp: 'in', value: [].concat(v) });
    else if (v !== null && typeof v === 'object' && !Array.isArray(v)) {
      for (const [op, x] of Object.entries(v)) {
        if (op === 'exists') kids.push({ kind: 'leaf', fact: k, cmp: x ? 'exists' : 'missing', value: '' });
        else kids.push({ kind: 'leaf', fact: k, cmp: op, value: x });
      }
    } else kids.push({ kind: 'leaf', fact: k, cmp: Array.isArray(v) ? 'oneof' : 'is', value: v });
  }
  return { kind: 'group', op: 'all', kids };
}
const simplify = g => g.kind === 'group' && g.op === 'all' && g.kids.length === 1 ? g.kids[0] : g;

function leafDict(n) {
  if (n.fact === 'sender_in') return { sender_in: n.value.length === 1 ? n.value[0] : n.value };
  if (n.cmp === 'is' || n.cmp === 'oneof') return { [n.fact]: n.value };
  if (n.cmp === 'exists' || n.cmp === 'missing') return { [n.fact]: { exists: n.cmp === 'exists' } };
  return { [n.fact]: { [n.cmp]: n.value } };
}
function toDict(n) {
  if (n.kind === 'leaf') return leafDict(n);
  if (n.op === 'any') return { any: n.kids.map(toDict) };
  const merged = {}, extra = [];
  for (const kid of n.kids) {
    for (const [k, v] of Object.entries(toDict(kid))) {
      const plain = x => x !== null && typeof x === 'object' && !Array.isArray(x);
      if (!(k in merged)) merged[k] = v;
      else if (plain(merged[k]) && plain(v) && k !== 'not' && !Object.keys(v).some(o => o in merged[k])) Object.assign(merged[k], v);
      else extra.push({ [k]: v });
    }
  }
  if (extra.length) merged.all = (merged.all || []).concat(extra);
  return n.op === 'not' ? { not: merged } : merged;
}

const CMPS = [['is', 'is'], ['oneof', 'is one of'], ['not', 'is none of'], ['exists', 'has a value'], ['missing', 'has no value'],
  ['min', 'is at least'], ['max', 'is at most'], ['count', 'has this many items'], ['in', "is in the list of fact…"]];

function parseValue(text, list) {
  const one = s => { s = s.trim(); if (s === 'true') return true; if (s === 'false') return false; if (s === 'null') return null; return s !== '' && !isNaN(s) ? Number(s) : s; };
  return list ? text.split(',').map(one).filter(x => x !== '') : one(text);
}

function condEditor(tree, cat, onchange) {
  const facts = cat.facts;
  const dl = h('datalist', { id: 'facts-dl' }, facts.map(f => h('option', { value: f.name }, `${f.source} · ${f.cost}`)), h('option', { value: 'sender_in' }, 'the sender is on a list'));
  function renderLeaf(n, parent) {
    const info = facts.find(f => f.name === n.fact);
    const isList = n.fact === 'sender_in';
    const many = isList || n.cmp === 'oneof' || n.cmp === 'not';
    const noval = n.cmp === 'exists' || n.cmp === 'missing';
    const hints = isList ? cat.lists.map(l => l.name) : info && info.type === 'bool' ? ['true', 'false'] : info && info.values ? info.values : null;
    const id = 'vals-' + Math.random().toString(36).slice(2);
    const val = h('input', { class: 'val', list: hints ? id : null, placeholder: many ? 'values, comma-separated' : 'value',
      value: Array.isArray(n.value) ? n.value.join(', ') : n.value === undefined ? '' : String(n.value),
      onchange: e => { n.value = n.cmp === 'in' && !isList ? e.target.value.trim() : parseValue(e.target.value, many); onchange(); } });
    return h('div', { class: 'cleaf' },
      h('input', { list: 'facts-dl', value: n.fact, placeholder: 'flag', style: 'width:210px', onchange: e => { n.fact = e.target.value.trim(); if (n.fact === 'sender_in') { n.cmp = 'in'; n.value = []; } onchange(true); } }),
      isList ? h('span', { class: 'muted' }, 'one of the lists') : h('select', { onchange: e => { n.cmp = e.target.value; if (['oneof', 'not'].includes(n.cmp) && !Array.isArray(n.value)) n.value = n.value === '' || n.value == null ? [] : [n.value]; if (n.cmp === 'is' && Array.isArray(n.value)) n.value = n.value[0] ?? ''; onchange(true); } },
        CMPS.map(([v, t]) => h('option', { value: v, selected: v === n.cmp }, t))),
      noval ? null : val, hints ? h('datalist', { id }, hints.map(x => h('option', { value: x }))) : null,
      info ? badge(info.cost, info.cost.startsWith('jev') ? 'jev' : info.cost === 'lookup' ? 'lookup' : '') : null,
      h('button', { class: 'small danger', title: 'remove', onclick: () => { parent.kids.splice(parent.kids.indexOf(n), 1); onchange(true); } }, '✕'));
  }
  function renderGroup(g, parent) {
    const box = h('div', { class: 'cgroup' });
    box.append(h('div', { class: 'head' },
      h('select', { onchange: e => { g.op = e.target.value; onchange(true); } },
        [['all', 'all of'], ['any', 'any of'], ['not', 'none of (not all of)']].map(([v, t]) => h('option', { value: v, selected: v === g.op }, t))),
      h('button', { class: 'small', onclick: () => { g.kids.push({ kind: 'leaf', fact: '', cmp: 'is', value: '' }); onchange(true); } }, '+ condition'),
      h('button', { class: 'small', onclick: () => { g.kids.push({ kind: 'group', op: 'any', kids: [] }); onchange(true); } }, '+ group'),
      parent ? h('button', { class: 'small danger', onclick: () => { parent.kids.splice(parent.kids.indexOf(g), 1); onchange(true); } }, '✕ group') : null));
    for (const kid of g.kids) box.append(kid.kind === 'group' ? renderGroup(kid, g) : renderLeaf(kid, g));
    if (!g.kids.length) box.append(h('div', { class: 'muted' }, 'no conditions'));
    return box;
  }
  return h('div', {}, dl, renderGroup(tree, null));
}

// ---------------------------------------------------------------- Rules

async function viewRules() {
  const el = main();
  const [cat, cfg] = await Promise.all([catalogue(true), api('GET', 'api/config/rules')]);
  el.replaceChildren(h('h1', {}, 'Rules'), h('p', { class: 'lead' },
    'Stages run from top to bottom. In a “first” stage the first rule that applies decides; in an “all” stage every rule that applies adds its label. Every change is tried on the test set before it is saved.'));
  for (const st of cfg.data.stages) {
    const rules = cfg.data[st.name] || [];
    el.append(h('h2', {}, st.name, ' ', h('span', { class: 'muted', style: 'font-weight:400;font-size:13px' },
      `${st.match === 'all' ? 'every matching rule applies' : 'first match decides'}${st.on_match === 'stop' ? ' · then stops' : ''}${st.only_if ? ' · only if ' + JSON.stringify(st.only_if) : ''}${st.always ? ' · always runs, replaces the outcome' : ''}`)));
    rules.forEach((r, i) => {
      const move = to => wrap(async () => { const t = await api('POST', 'api/rules/edit', { stage: st.name, op: 'move', index: i, to }); reviewAndSave('rules', t.text, viewRules); });
      el.append(h('div', { class: 'rule' + (r.enabled === false ? ' off' : '') },
        h('div', { class: 'updown' }, h('button', { disabled: i === 0, onclick: move(i - 1) }, '▲'), h('button', { disabled: i === rules.length - 1, onclick: move(i + 1) }, '▼')),
        h('div', {}, h('span', { class: 'name' }, r.name), ' ', r.then ? badge(r.then) : badge(r.label, 'label'), r.leftover ? [' ', badge('leftover')] : '',
          ' ', h('span', { class: 'muted', style: 'font-size:12px' }, cat.stats[r.name] ? `${cat.stats[r.name]} emails in 30 days` : ''),
          h('div', { class: 'note' }, r.note || ''),
          h('div', { class: 'cond' }, 'when  ' + JSON.stringify(r.when) + (r.unless ? '\nunless ' + JSON.stringify(r.unless) : ''))),
        h('div', { class: 'row' },
          h('button', { class: 'small', onclick: wrap(async () => { const t = await api('POST', 'api/rules/edit', { stage: st.name, op: 'toggle', index: i }); reviewAndSave('rules', t.text, viewRules); }) }, r.enabled === false ? 'Enable' : 'Disable'),
          h('button', { class: 'small', onclick: () => ruleEditor(st, i, r, cat) }, 'Edit'),
          h('button', { class: 'small danger', onclick: wrap(async () => { if (!(await ask(`Delete the rule “${r.name}”?`))) return; const t = await api('POST', 'api/rules/edit', { stage: st.name, op: 'delete', index: i }); reviewAndSave('rules', t.text, viewRules); }) }, 'Delete'))));
    });
    el.append(h('button', { onclick: () => ruleEditor(st, null, { name: '', when: {} }, cat) }, `+ Add a rule to ${st.name}`));
  }
  el.append(h('h2', {}, 'The file'), fileEditor('rules', cfg.text, viewRules));
}

function ruleEditor(stage, index, rule, cat) {
  const r = JSON.parse(JSON.stringify(rule));
  let when = toTree(r.when), unless = toTree(r.unless || {});
  let tab = 'builder';
  const body = h('div', {});
  const yamlArea = yamlEditor({ rows: 16 });
  const collect = () => {
    const out = { name: r.name, note: r.note, when: toDict(when) };
    const u = toDict(unless); if (Object.keys(u).length) out.unless = u;
    if (stage.match === 'all') { out.label = r.label; if (r.leftover) out.leftover = true; } else out.then = r.then;
    if (r.enabled === false) out.enabled = false;
    return out;
  };
  const render = () => {
    const builder = h('div', {},
      h('div', { class: 'row' },
        h('label', { class: 'f grow' }, 'Name', h('input', { value: r.name || '', oninput: e => { r.name = e.target.value.trim(); } })),
        stage.match === 'all'
          ? h('label', { class: 'f' }, 'Gmail label', h('input', { value: r.label || '', list: 'labels-dl', oninput: e => { r.label = e.target.value.trim(); } }), h('datalist', { id: 'labels-dl' }, cat.labels.map(l => h('option', { value: l }))))
          : h('label', { class: 'f' }, 'Outcome', h('select', { onchange: e => { r.then = e.target.value; } }, Object.entries(cat.outcomes).map(([o, help]) => h('option', { value: o, selected: o === r.then, title: help }, o)))),
        stage.match === 'all' ? h('label', { class: 'row', style: 'margin-top:8px' }, h('input', { type: 'checkbox', checked: !!r.leftover, onchange: e => { r.leftover = e.target.checked; } }), 'only if no rule above matched') : null),
      h('label', { class: 'f' }, 'Note', h('textarea', { class: 'prose', rows: 2, oninput: e => { r.note = e.target.value; } }, r.note || '')),
      h('h3', {}, 'When'), condEditor(when, cat, redraw => { if (redraw) render(); }),
      h('h3', {}, 'Unless'), condEditor(unless, cat, redraw => { if (redraw) render(); }));
    if (stage.match !== 'all' && !r.then) r.then = Object.keys(cat.outcomes)[0];
    body.replaceChildren(h('h2', {}, index === null ? `New rule in ${stage.name}` : `Rule: ${rule.name}`),
      h('div', { class: 'tabs' },
        h('button', { class: tab === 'builder' ? 'on' : '', onclick: wrap(async () => { if (tab === 'yaml') { const d = (await api('POST', 'api/yaml/load', { text: await yamlArea.ensureValid() })).data; for (const k of Object.keys(r)) delete r[k]; Object.assign(r, d); when = toTree(d.when); unless = toTree(d.unless || {}); } tab = 'builder'; render(); }) }, 'Builder'),
        h('button', { class: tab === 'yaml' ? 'on' : '', onclick: wrap(async () => { yamlArea.value = (await api('POST', 'api/yaml/dump', { data: collect() })).text; tab = 'yaml'; render(); }) }, 'YAML')),
      tab === 'builder' ? builder : yamlArea,
      h('div', { class: 'foot' }, h('button', { onclick: closeModal }, 'Cancel'),
        h('button', { class: 'primary', onclick: wrap(async () => {
          const data = tab === 'yaml' ? (await api('POST', 'api/yaml/load', { text: await yamlArea.ensureValid() })).data : collect();
          if (!data.name) { toast('a rule needs a name'); return; }
          const t = await api('POST', 'api/rules/edit', { stage: stage.name, op: 'set', index, rule: data });
          reviewAndSave('rules', t.text, viewRules);
        }) }, 'Check and save…')));
  };
  render();
  modal(body, true);
}

// ---------------------------------------------------------------- a whole file, as text

function fileEditor(file, text, after) {
  const ed = yamlEditor({ rows: 24 });
  const box = h('details', { class: 'panel', ontoggle: e => { if (e.target.open && !ed.value) ed.value = text; } },
    h('summary', {}, `Edit ${file}.yaml as text`),
    h('p', { class: 'muted' }, 'The same checks apply as for any other change.'), ed,
    h('div', { class: 'foot' }, h('button', { class: 'primary', onclick: wrap(async () => reviewAndSave(file, await ed.ensureValid(), after)) }, 'Check and save…')));
  return box;
}

// ---------------------------------------------------------------- Lists

let currentList = null;

async function viewLists() {
  const el = main();
  const cat = await catalogue(true);
  if (!currentList || !cat.lists.find(l => l.name === currentList)) currentList = cat.lists[0] && cat.lists[0].name;
  const side = h('div', { class: 'side' }, cat.lists.map(l => h('a', { class: l.name === currentList ? 'on' : '', onclick: () => { currentList = l.name; viewLists(); } }, l.name, h('span', { class: 'muted' }, l.count))),
    h('a', { onclick: async () => {
      const name = slug(await ask('Name of the new list (lower-case, no spaces):', true)); if (!name) return;
      api('POST', 'api/lists/edit', { name, body: { description: '', holds: 'senders', entries: [] } }).then(t => reviewAndSave('lists', t.text, () => { currentList = name; viewLists(); }));
    } }, '+ New list'));
  const detail = h('div', {});
  el.replaceChildren(h('h1', {}, 'Lists'), h('p', { class: 'lead' }, 'Any number of lists. A list is usable in rules as soon as it exists: sender_in: <list>, or a fact built on it.'),
    h('div', { class: 'split' }, side, detail));
  if (!currentList) return;
  const d = await api('GET', `api/lists/${currentList}`);
  const body = JSON.parse(JSON.stringify(d.body));
  body.entries = body.entries || [];
  const records = body.holds === 'records';
  const cols = records ? [...new Set(body.entries.flatMap(e => Object.keys(e)))] : ['value', 'note'];
  let filter = '';
  const norm = e => typeof e === 'string' ? { value: e, note: '' } : e;
  const out = e => records ? e : (e.note ? { value: e.value, note: e.note } : e.value);
  const tbody = h('tbody', {});
  const count = h('span', { class: 'muted' });
  const draw = () => {
    const rows = body.entries.map(norm);
    body.entries = rows;
    const shown = rows.filter(e => !filter || JSON.stringify(e).toLowerCase().includes(filter));
    count.textContent = `${rows.length} entries` + (filter ? ` · ${shown.length} shown` : '');
    tbody.replaceChildren(...shown.slice(0, 400).map(e => h('tr', {}, cols.map(c => h('td', {},
      h('input', { style: 'width:100%', value: Array.isArray(e[c]) ? e[c].join(', ') : e[c] ?? '', onchange: ev => { const v = ev.target.value; e[c] = records && Array.isArray(e[c] ?? (c === 'domains' || c === 'languages' ? [] : null)) ? v.split(',').map(s => s.trim()).filter(Boolean) : v.trim(); if (records && e[c] === '') delete e[c]; } }))),
      h('td', { style: 'width:1%' }, h('button', { class: 'small danger', onclick: () => { rows.splice(rows.indexOf(e), 1); draw(); } }, '✕')))));
    if (shown.length > 400) tbody.append(h('tr', {}, h('td', { colspan: cols.length + 1, class: 'muted' }, `… ${shown.length - 400} more — use the filter`)));
  };
  const adv = { ...body }; delete adv.entries; delete adv.description; delete adv.holds;
  const jobsCfg = await api('GET', 'api/config/jobs');
  const advCtx = { cat, jobs: Object.keys(jobsCfg.data.jobs || {}), yamlFields: [], redraw: () => drawAdv() };
  const advBox = h('div', {});
  const drawAdv = () => { advCtx.yamlFields = []; advBox.replaceChildren(formFor(listOptionsFields(advCtx), adv, advCtx)); };
  drawAdv();
  const save = wrap(async () => {
    for (const f of advCtx.yamlFields) await f();
    const extra = adv;
    const next = { description: body.description || '', holds: body.holds || 'senders', ...extra, entries: body.entries.map(out).filter(e => records ? e.name : (typeof e === 'string' ? e : e.value)) };
    const t = await api('POST', 'api/lists/edit', { name: currentList, body: next });
    reviewAndSave('lists', t.text, viewLists);
  });
  const addBox = h('textarea', { rows: 3, placeholder: records ? 'new records are added in the table: use “+ Row”' : 'Add entries: one address or domain per line (a note after # is kept)' });
  const candBox = h('div', {});
  detail.append(
    h('div', { class: 'panel' },
      h('div', { class: 'row' }, h('h2', { style: 'margin:0', class: 'grow' }, currentList), count,
        h('button', { class: 'danger', onclick: wrap(async () => { if (!(await ask(`Delete the list “${currentList}”?`))) return; const t = await api('POST', 'api/lists/edit', { name: currentList, body: null }); reviewAndSave('lists', t.text, viewLists); }) }, 'Delete list'),
        h('button', { class: 'primary', onclick: save }, 'Check and save…')),
      h('label', { class: 'f', style: 'margin-top:10px' }, 'Description', h('input', { value: body.description || '', oninput: e => { body.description = e.target.value; } })),
      h('div', { class: 'row' },
        h('label', { class: 'f' }, 'Holds', h('select', { onchange: e => { body.holds = e.target.value; } }, ['senders', 'patterns', 'records'].map(k => h('option', { selected: k === (body.holds || 'senders') }, k)))),
        h('div', { class: 'muted grow', style: 'font-size:12px' }, 'senders: an address (exact) or a domain (covers subdomains) · patterns: wildcards (*@ns.nl) · records: entries with fields')),
      d.used_by.length ? h('div', { class: 'muted', style: 'font-size:12px' }, 'Used by: ' + d.used_by.join(' · ')) : h('div', { class: 'muted', style: 'font-size:12px' }, 'Not used by any rule or fact yet.')),
    h('div', { class: 'panel' },
      h('div', { class: 'row', style: 'margin-bottom:8px' }, h('input', { class: 'grow', placeholder: 'Filter entries', oninput: e => { filter = e.target.value.toLowerCase(); draw(); } }),
        records ? h('button', { onclick: () => { body.entries.unshift({ name: '', domains: [] }); draw(); } }, '+ Row') : null),
      h('div', { class: 'tablewrap', style: 'max-height:46vh' }, h('table', {}, h('thead', {}, h('tr', {}, cols.map(c => h('th', {}, c)), h('th', {}, ''))), tbody))),
    records ? null : h('div', { class: 'panel' }, h('h3', { style: 'margin-top:0' }, 'Add entries'), addBox,
      h('div', { class: 'foot' }, h('button', { onclick: () => {
        for (const line of addBox.value.split('\n')) { const [v, n] = line.split('#'); if (v.trim() && !body.entries.some(e => norm(e).value.toLowerCase() === v.trim().toLowerCase())) body.entries.push({ value: v.trim(), note: (n || '').trim() }); }
        addBox.value = ''; draw();
      } }, 'Add to the table'))),
    records ? null : h('div', { class: 'panel' }, h('h3', { style: 'margin-top:0' }, 'Find candidates'),
      h('p', { class: 'muted' }, 'Senders of the mail a Gmail search finds (up to 300 messages), to tick and add.'),
      h('div', { class: 'row' }, h('input', { id: 'cand-q', class: 'grow', placeholder: 'e.g. label:Kids   or   subject:(track trace)' }),
        h('button', { onclick: wrap(async ev => {
          ev.target.disabled = true; candBox.textContent = 'searching…';
          try {
            const rows = await api('GET', 'api/candidates?q=' + encodeURIComponent(document.getElementById('cand-q').value));
            const have = new Set(body.entries.map(e => norm(e).value.toLowerCase()));
            candBox.replaceChildren(h('div', { class: 'tablewrap', style: 'max-height:36vh;margin-top:8px' }, h('table', {}, h('tbody', {}, rows.map(c => h('tr', {},
              h('td', { style: 'width:1%' }, have.has(c.address) ? '✓' : h('button', { class: 'small', onclick: e => { body.entries.push({ value: c.address, note: '' }); e.target.replaceWith('✓'); draw(); } }, 'add')),
              h('td', {}, c.address), h('td', { style: 'width:1%' }, c.count), h('td', { class: 'clip', style: 'width:45%' }, c.example)))))));
          } finally { ev.target.disabled = false; }
        }) }, 'Search'))),
    candBox,
    h('details', { class: 'panel' }, h('summary', {}, 'How this list is filled and kept tidy'), advBox));
  draw();
}

// ---------------------------------------------------------------- detectors (facts)

const FIELD_HINTS = ['subject', 'subject.stripped', 'body', 'body.head(1500)', 'body.tail(3000)', 'snippet', 'sender.name', 'sender.address',
  'sender.domain', 'sender.registrable', 'sender.domain_label', 'sender.mailbox', 'reply_to.address', 'to.names', 'to.names.stripped'];
const refs = ctx => [...FIELD_HINTS, ...ctx.cat.facts.filter(f => f.source !== 'field').map(f => f.name)];
const listNames = ctx => ctx.cat.lists.map(l => l.name);

const DETECTOR_TYPES = {
  pattern: { label: 'pattern — a regular expression on a field', blank: '', main: { type: 'regex', label: 'regular expression' }, siblings: [
    { key: 'in', label: 'in', type: 'stringsOrOne', inline: true, suggest: refs, help: 'the field(s) to search' },
    { key: 'flags', type: 'strings', inline: true, suggest: ['ignore_case', 'multiline', 'dotall'] },
    { key: 'returns', label: 'gives', type: 'select', options: [['bool', 'yes / no'], ['first', 'the first match'], ['all', 'every match']], default: 'bool' }] },
  header: { label: 'header — a header is present, or has a value', blank: '', main: { type: 'text', label: 'header name' }, siblings: [
    { key: 'is', label: 'is one of', type: 'strings', inline: true, help: 'empty: the header is present' }] },
  addresses: { label: 'addresses — addresses in To / Cc or in text', blank: { in: ['to', 'cc'] }, nested: [
    { key: 'in', type: 'strings', inline: true, suggest: ['to', 'cc', 'subject', 'body'], help: 'to / cc; or text fields with “found in text”' },
    { key: 'from_text', type: 'bool', label: 'found in text' },
    { key: 'output', type: 'select', options: ['address', 'mailbox', 'name'], default: 'address', help: 'mailbox: dots, +tags and alias domains ignored' },
    { key: 'as', label: 'gives', type: 'select', options: [['list', 'the list'], ['bool', 'yes / no'], ['count', 'how many'], ['first', 'the first']], default: 'list' },
    { key: 'unique', type: 'bool', label: 'unique and sorted' },
    { key: 'pattern', type: 'regex', label: 'address pattern (empty: the one in Settings)' },
    { key: 'where', type: 'object', label: 'only addresses where', fields: [
      { key: 'address_is', type: 'text' }, { key: 'mailbox_in', type: 'strings', help: 'placeholders allowed' },
      { key: 'mailbox_not_in', type: 'strings' }, { key: 'domain_in', type: 'yaml', label: 'domain_in — a list of domains, or {list: …, where: {…}}' },
      { key: 'has_plus_tag', type: 'bool' }, { key: 'not_the_sender', type: 'bool' }, { key: 'name_present', type: 'bool' },
      { key: 'name_without', type: 'text', raw: true }] }] },
  language: { label: 'language — the language of a field', blank: 'body', main: { type: 'text', label: 'field', suggest: refs }, siblings: [
    { key: 'min_chars', type: 'number' }, { key: 'max_chars', type: 'number' }] },
  script: { label: 'script — the writing system of a field', blank: 'subject', main: { type: 'text', label: 'field', suggest: refs } },
  country: { label: 'country — from a domain ending or phone numbers', blank: { domain: 'sender.domain' }, nested: [
    { key: 'domain', type: 'text', suggest: refs, help: 'fill this, or “phones”' }, { key: 'vanity', type: 'strings', inline: true, help: 'endings that say nothing (io, ai, …)' },
    { key: 'aliases', type: 'map', help: 'ending → country code' }, { key: 'phones', type: 'text', suggest: refs }, { key: 'max_chars', type: 'number' }] },
  list: { label: 'list — a value is on a list', blank: { value: 'sender.address', on: '' }, nested: [
    { key: 'value', type: 'text', suggest: refs }, { key: 'on', label: 'on the list(s)', type: 'stringsOrOne', inline: true, suggest: listNames },
    { key: 'where', type: 'map', help: 'records only: field → value(s)' },
    { key: 'returns', label: 'gives', type: 'select', options: [['bool', 'yes / no'], ['record', 'the matching record']], default: 'bool' }] },
  lookup: { label: 'lookup — count the results of a Gmail search (cached)', blank: { search: 'from:{sender_key}', max: 5 }, nested: [
    { key: 'search', type: 'text', mono: true, wide: true, help: '{fact} is filled in' }, { key: 'max', type: 'number' },
    { key: 'exclude_self', type: 'bool', label: 'do not count this email' },
    { key: 'cache', type: 'array', add: 'cache case', help: 'first match wins; no match: not cached', blank: { ttl: '1d' }, fields: [
      { key: 'min', type: 'number', label: 'when the count is at least (empty: any)' }, { key: 'ttl', type: 'text', label: 'keep for (30d, 12h, forever)' }] }] },
  derived: { label: 'derived — the first case whose condition holds', blank: [{ value: true }], main: { type: 'array', label: 'cases', add: 'case', blank: { value: true }, fields: [
    { key: 'when', type: 'cond', label: 'when (no conditions: otherwise)' }, { key: 'value', type: 'smart', keep: true }] } },
  first_available: { label: 'first available — the first of these that has a value', blank: [''], main: { type: 'detectors', label: 'in this order' } },
  any: { label: 'any — yes if any of these is', blank: [''], main: { type: 'detectors', label: 'any of' } },
  all: { label: 'all — yes if all of these are', blank: [''], main: { type: 'detectors', label: 'all of' } },
  extract: { label: 'extract — candidate strings collected and cleaned', blank: { sources: [{ field: 'sender.name' }] }, nested: [
    { key: 'sources', type: 'array', add: 'source', blank: { field: '' }, fields: [
      { key: 'field', type: 'text', suggest: refs, help: 'a field as it is, or a pattern below' }, { key: 'unless_contains', type: 'text', raw: true },
      { key: 'pattern', type: 'regex', label: 'pattern (captures)' }, { key: 'in', type: 'text', suggest: refs }, { key: 'flags', type: 'strings', inline: true }] },
    { key: 'split', type: 'regex', label: 'split each on' }, { key: 'split_flags', type: 'strings', inline: true },
    { key: 'clean', type: 'array', add: 'cleaning step', blank: { replace: '', with: '' }, fields: [
      { key: 'replace', type: 'regex' }, { key: 'with', type: 'text', raw: true, keep: true }, { key: 'strip', type: 'text', raw: true, label: 'or: strip these characters' }] },
    { key: 'keep', type: 'object', label: 'keep only', fields: [{ key: 'min_chars', type: 'number' }, { key: 'max_chars', type: 'number' }, { key: 'matching', type: 'regex' }] },
    { key: 'append', type: 'strings', inline: true, suggest: refs }, { key: 'limit', type: 'number' }] },
  field: { label: 'field — the value of a field or another flag', blank: 'subject', main: { type: 'text', label: 'field or flag', suggest: refs } },
  value: { label: 'value — a fixed value', blank: '', main: { type: 'smart', label: 'value', keep: true } },
};

function detectorForm(det, ctx) {
  const box = h('div', {});
  const draw = () => {
    const type = Object.keys(DETECTOR_TYPES).find(t => t in det) || '';
    const spec = DETECTOR_TYPES[type];
    const inner = { ...ctx, redraw: draw };
    box.replaceChildren(h('label', { class: 'f wide' }, h('span', {}, 'detector'),
      h('select', { onchange: e => {
        for (const k of Object.keys(det)) if (k !== 'help') delete det[k];
        det[e.target.value] = JSON.parse(JSON.stringify(DETECTOR_TYPES[e.target.value].blank));
        if (ctx.onchange) ctx.onchange(); draw();
      } }, type ? null : h('option', { value: '' }, 'choose…'), Object.entries(DETECTOR_TYPES).map(([t, s]) => h('option', { value: t, selected: t === type }, s.label)))));
    if (!spec) return;
    if (spec.main) box.append(fieldFor({ ...spec.main, key: type, keep: true }, det, inner));
    if (spec.siblings) box.append(formFor(spec.siblings, det, { ...inner, ignore: [type, 'help'] }));
    if (spec.nested) { if (!isObj(det[type])) det[type] = {}; box.append(formFor(spec.nested, det[type], inner)); }
  };
  draw();
  return box;
}

// ---------------------------------------------------------------- the forms of each area

const factForm = (holder, key, ctx) => { const d = holder[key]; return h('div', { class: 'form' }, fieldFor({ key: 'help', type: 'prose', rows: 2, label: 'what it means' }, d, ctx), detectorForm(d, ctx)); };

function questionForm(holder, key, ctx) {
  const q = holder[key];
  const box = h('div', {});
  const draw = () => {
    const c = { ...ctx, redraw: draw };
    const gated = isObj(q.ask);
    const w = gated ? (q.ask.when = q.ask.when || {}) : null;
    const source = q.options_from === 'countries' ? 'countries' : isObj(q.options_from) ? 'fact' : isObj(q.options) && 'use' in q.options ? 'set' : 'typed';
    const answerOptions = () => { const a = ctx.cat.facts.find(f => f.name === (w || {}).answer); return a && a.values ? a.values : []; };
    box.replaceChildren(h('div', { class: 'form' },
      fieldFor({ key: 'type', type: 'select', options: [['choice', 'choice — one of several options'], ['yes_no', 'yes / no']], redraw: true }, q, c),
      h('label', { class: 'f' }, h('span', {}, 'asked'), h('select', { onchange: e => { q.ask = e.target.value === 'first' ? 'first' : { when: { answer: '', options: [], sum_at_least: 0.5 } }; draw(); } },
        h('option', { value: 'first', selected: !gated }, 'in the first request, for every email'), h('option', { value: 'when', selected: gated }, 'only when an earlier answer makes it relevant'))),
      gated ? h('fieldset', { class: 'f wide' }, h('legend', {}, 'asked when'), h('div', { class: 'form' },
        fieldFor({ key: 'answer', label: 'the answer to', type: 'select', options: () => ctx.cat.facts.filter(f => f.source === 'jev' && f.cost === 'jev').map(f => f.name), optional: 'choose…', redraw: true }, w, c),
        fieldFor({ key: 'options', label: 'gives these options', type: 'strings', inline: true, suggest: answerOptions }, w, c),
        h('label', { class: 'f' }, h('span', {}, 'a combined probability'), h('div', { class: 'row' },
          h('select', { onchange: e => { const v = w.sum_at_least ?? w.sum_below ?? 0.5; delete w.sum_at_least; delete w.sum_below; w[e.target.value] = v; } },
            h('option', { value: 'sum_at_least', selected: 'sum_at_least' in w }, 'of at least'), h('option', { value: 'sum_below', selected: 'sum_below' in w }, 'below')),
          h('input', { type: 'number', step: '0.05', min: 0, max: 1, style: 'width:90px', value: w.sum_at_least ?? w.sum_below ?? 0.5, onchange: e => { w['sum_below' in w ? 'sum_below' : 'sum_at_least'] = Number(e.target.value); } }))))) : null,
      fieldFor({ key: 'question', type: 'prose', rows: 5, label: 'the question, as the classifier reads it' }, q, c),
      fieldFor({ key: 'context', type: 'map', label: 'context — values given to the classifier with the question', help: 'a value is text or a {{placeholder}}', add: 'value' }, q, c),
      q.type === 'yes_no' ? [
        fieldFor({ key: 'yes', type: 'prose', rows: 3, label: 'yes means' }, q, c), fieldFor({ key: 'no', type: 'prose', rows: 3, label: 'no means' }, q, c),
        fieldFor({ key: 'yes_at', type: 'number', label: 'counts as yes from p(yes) =', help: 'empty: the general threshold' }, q, c)] : [
        h('label', { class: 'f wide' }, h('span', {}, 'options'), h('select', { onchange: e => {
          delete q.options_from; const v = e.target.value;
          if (v === 'typed') q.options = isObj(q.options) && !('use' in q.options) ? q.options : {};
          if (v === 'set') q.options = { use: Object.keys(ctx.optionSets)[0] || '' };
          if (v === 'countries') { delete q.options; q.options_from = 'countries'; }
          if (v === 'fact') { delete q.options; q.options_from = { fact: '', id: 'item_{i}', text: '“{value}”' }; }
          draw();
        } }, [['typed', 'written here'], ['set', 'a shared option set'], ['countries', 'every country (code → name)'], ['fact', 'one option per item of a fact']].map(([v, t]) => h('option', { value: v, selected: v === source }, t)))),
        source === 'typed' ? fieldFor({ key: 'options', type: 'map', long: true, label: 'option → what it means', add: 'option', keep: true }, q, c) : null,
        source === 'set' ? [fieldFor({ key: 'use', label: 'option set', type: 'select', options: () => Object.keys(ctx.optionSets) }, q.options, c),
          fieldFor({ key: 'override', type: 'map', long: true, label: 'options worded differently for this question', add: 'override' }, q.options, c)] : null,
        source === 'fact' ? h('fieldset', { class: 'f wide' }, h('legend', {}, 'one option per item'), formFor([
          { key: 'fact', type: 'text', suggest: refs }, { key: 'id', type: 'text', help: '{i} is the item’s number' }, { key: 'text', type: 'text', raw: true, help: '{value} is the item' }], q.options_from, c)) : null,
        fieldFor({ key: 'extra_options', type: 'map', long: true, label: source === 'typed' ? 'further options' : 'options added after the generated ones', add: 'option' }, q, c),
        source === 'fact' ? fieldFor({ key: 'answer_as', type: 'select', label: 'the answer is reported as', options: [['value', 'the item behind the option']], optional: 'the option’s id' }, q, c) : null]));
  };
  draw();
  return box;
}

const optionSetForm = (holder, key, ctx) => fieldFor({ key, type: 'map', long: true, label: 'option → what it means', add: 'option', keep: true }, holder, ctx);

function jevInputForm(holder, key, ctx) {
  const input = holder[key];
  input.fields = input.fields || {};
  const rows = h('div', { class: 'rows' });
  const draw = () => rows.replaceChildren(...Object.keys(input.fields).map(k => {
    const spec = () => isObj(input.fields[k]) ? input.fields[k] : (input.fields[k] = { field: input.fields[k] });
    const tidy = () => { const s = input.fields[k]; if (isObj(s) && Object.keys(s).length === 1 && 'field' in s) input.fields[k] = s.field; };
    const cur = isObj(input.fields[k]) ? input.fields[k] : { field: input.fields[k] };
    return h('div', { class: 'arrayrow' }, h('div', { class: 'form grow' },
      h('label', { class: 'f' }, h('span', {}, 'shown to the classifier as'), h('input', { value: k, onchange: e => { const nk = e.target.value.trim(); if (!nk || nk === k) return; input.fields = Object.fromEntries(Object.entries(input.fields).map(([a, b]) => [a === k ? nk : a, b])); draw(); } })),
      h('label', { class: 'f' }, h('span', {}, 'field or flag'), h('input', { value: cur.field ?? '', onchange: e => { spec().field = e.target.value.trim(); tidy(); } })),
      h('label', { class: 'f' }, h('span', {}, 'at most (characters)'), h('input', { type: 'number', value: cur.max_chars ?? '', onchange: e => { if (e.target.value === '') delete spec().max_chars; else spec().max_chars = Number(e.target.value); tidy(); } })),
      h('label', { class: 'f' }, h('span', {}, 'if empty, use'), h('input', { value: cur.fallback ?? '', placeholder: 'another field', onchange: e => { if (e.target.value.trim()) spec().fallback = e.target.value.trim(); else delete spec().fallback; tidy(); } })),
      h('label', { class: 'f check' }, h('input', { type: 'checkbox', checked: 'blank' in cur, onchange: e => { if (e.target.checked) spec().blank = null; else delete spec().blank; tidy(); } }), h('span', {}, 'send “nothing” (null) when empty'))),
      h('div', { class: 'updown' }, h('button', { class: 'danger', onclick: () => { delete input.fields[k]; draw(); } }, '✕')));
  }), h('button', { class: 'small', onclick: () => { input.fields['new_field'] = 'subject'; draw(); } }, '+ field'));
  draw();
  return h('div', { class: 'form' }, fieldFor({ key: 'wrap', type: 'text', label: 'wrapped in', help: 'the name the classifier sees for the whole email' }, input, ctx),
    h('fieldset', { class: 'f wide' }, h('legend', {}, 'fields'), rows));
}

const OP_KINDS = [['trash', 'move to Trash'], ['untrash', 'take out of Trash'], ['mark_read', 'mark as read'], ['star', 'star'], ['add_labels', 'add labels'], ['remove_labels', 'remove labels']];
function opsField(label, key, obj, ctx) {
  const list = Array.isArray(obj[key]) ? obj[key] : [];
  const rows = h('div', { class: 'rows' });
  const names = ['$labels', 'INBOX', 'SPAM', 'TRASH', 'UNREAD', 'STARRED', 'user_labels', ...ctx.cat.labels];
  const commit = () => put(obj, key, list);
  const draw = () => rows.replaceChildren(...list.map((op, i) => {
    const kind = typeof op === 'string' ? op : Object.keys(op)[0];
    const id = 'ops-' + Math.random().toString(36).slice(2);
    return h('div', { class: 'maprow' },
      h('select', { onchange: e => { const k = e.target.value; list[i] = k.endsWith('_labels') ? { [k]: isObj(op) ? Object.values(op)[0] : [] } : k; commit(); draw(); } }, OP_KINDS.map(([v, t]) => h('option', { value: v, selected: v === kind }, t))),
      isObj(op) ? [h('input', { class: 'grow', list: id, placeholder: 'labels, comma-separated ($labels: the labels the rules gave)', value: (op[kind] || []).join(', '), onchange: e => { op[kind] = e.target.value.split(',').map(s => s.trim()).filter(Boolean); commit(); } }), h('datalist', { id }, names.map(n => h('option', { value: n })))] : h('span', { class: 'grow' }),
      h('button', { class: 'small', disabled: i === 0, onclick: () => { list.splice(i - 1, 0, list.splice(i, 1)[0]); commit(); draw(); } }, '▲'),
      h('button', { class: 'small danger', onclick: () => { list.splice(i, 1); commit(); draw(); } }, '✕'));
  }), h('button', { class: 'small', onclick: () => { list.push({ add_labels: [] }); commit(); draw(); } }, '+ operation'));
  draw();
  return h('fieldset', { class: 'f wide' }, h('legend', {}, label), rows);
}
const outcomeForm = (holder, key, ctx) => { const o = holder[key]; return h('div', { class: 'form' },
  fieldFor({ key: 'help', type: 'text', wide: true, label: 'what it means' }, o, ctx),
  fieldFor({ key: 'log_as', type: 'text', label: 'word in the log', help: 'empty: the outcome’s name' }, o, ctx),
  opsField('what it does in Gmail, in order', 'do', o, ctx), opsField('undo (from the log)', 'undo', o, ctx),
  formFor([], o, { ...ctx, ignore: ['help', 'log_as', 'do', 'undo'] })); };

function jobForm(holder, key, ctx) {
  const j = holder[key];
  const box = h('div', {});
  const draw = () => {
    const c = { ...ctx, redraw: draw };
    const kind = j.sync_list !== undefined ? 'sync' : j.outcome !== undefined ? 'search' : 'rules';
    box.replaceChildren(h('div', { class: 'form' },
      fieldFor({ key: 'help', type: 'text', wide: true, label: 'what it does' }, j, c),
      h('label', { class: 'f' }, h('span', {}, 'kind'), h('select', { onchange: e => {
        const v = e.target.value; delete j.rules; delete j.sync_list; delete j.outcome;
        if (v === 'rules') j.rules = true; if (v === 'sync') { j.sync_list = ''; delete j.search; } if (v === 'search') { j.outcome = Object.keys(ctx.cat.outcomes)[0]; j.search = j.search || ''; }
        draw();
      } }, [['rules', 'apply the rules to messages'], ['search', 'perform an outcome on what a search finds'], ['sync', 'fill a list from its Gmail label']].map(([v, t]) => h('option', { value: v, selected: v === kind }, t)))),
      fieldFor({ key: 'enabled', type: 'bool', default: true, label: 'enabled' }, j, c),
      kind !== 'sync' ? fieldFor({ key: 'search', type: 'text', mono: true, wide: true, label: 'Gmail search', help: kind === 'rules' ? 'empty: the messages its trigger hands it. {name}: a parameter' : '{name}: a parameter of the run' }, j, c) : null,
      kind === 'search' ? fieldFor({ key: 'outcome', type: 'select', options: () => Object.keys(ctx.cat.outcomes) }, j, c) : null,
      kind === 'sync' ? fieldFor({ key: 'sync_list', label: 'list', type: 'select', options: listNames, optional: 'choose…' }, j, c) : null,
      kind === 'rules' ? [fieldFor({ key: 'only_if_in', type: 'text', label: 'only if the message is still in', suggest: () => ['INBOX', ...ctx.cat.labels] }, j, c),
        fieldFor({ key: 'ids_from', type: 'select', label: 'messages from', options: [['failed', 'the log: attempts that all failed']], optional: 'its trigger or search' }, j, c),
        fieldFor({ key: 'never_move_to_inbox', type: 'bool', label: 'never move mail into the inbox' }, j, c)] : null,
      fieldFor({ key: 'manual', type: 'strings', inline: true, label: 'parameters when run by hand' }, j, c),
      kind !== 'rules' ? fieldFor({ key: 'log', type: 'object', lazy: true, label: 'in the log', fields: [{ key: 'decision', type: 'text' }, { key: 'rule', type: 'text', help: '{name}: a parameter' }] }, j, c) : null,
      formFor([], j, { ...c, ignore: ['help', 'rules', 'sync_list', 'outcome', 'enabled', 'search', 'only_if_in', 'ids_from', 'never_move_to_inbox', 'manual', 'log'] })));
  };
  draw();
  return box;
}

const triggersForm = (holder, key, ctx) => fieldFor({ key, type: 'array', label: 'triggers', add: 'trigger', blank: { on: 'new_mail', label: 'INBOX', run: '' }, keep: true, fields: item => [
  { key: 'on', type: 'select', redraw: true, options: [['new_mail', 'new mail arrives in a label'], ['label_added', 'a label is put on a message'], ['start', 'the daemon starts'], ['schedule', 'once a day'], ['interval', 'every so many minutes']] },
  ...(item.on === 'new_mail' || item.on === 'label_added' ? [{ key: 'label', type: 'text', suggest: () => ['INBOX', ...ctx.cat.labels] }] : []),
  ...(item.on === 'schedule' ? [{ key: 'hour', type: 'number', label: 'during the hour (0–23)' }] : []),
  ...(item.on === 'interval' ? [{ key: 'minutes', type: 'number', label: 'every … minutes (at the next wake-up)' }] : []),
  { key: 'run', label: 'runs the job', type: 'select', options: () => ctx.jobs, optional: 'choose…' }] }, holder, ctx);

const listOptionsFields = ctx => [
  { key: 'match', type: 'text', label: 'records are matched on the field', help: 'records lists only' },
  { key: 'source', type: 'object', lazy: true, label: 'filled from', fields: [{ key: 'gmail_label', type: 'text', label: 'the senders of the mail in the Gmail label', suggest: () => ctx.cat.labels }] },
  { key: 'normalise', type: 'object', lazy: true, label: 'kept tidy', fields: [
    { key: 'collapse_to_domain_at', type: 'number', label: 'this many addresses of one domain become the domain' },
    { key: 'never_collapse', type: 'object', label: 'never collapsed', fields: [{ key: 'lists', type: 'strings', inline: true, suggest: listNames, label: 'domains on the lists' }, { key: 'addresses', type: 'strings', label: 'the domains of these addresses', help: 'placeholders allowed' }] }] },
  { key: 'on_new_entry', type: 'select', label: 'for each new entry, run the job', options: () => ctx.jobs, optional: 'nothing' }];

const PROFILE_LABELS = { reads: 'languages you read', communicates: 'languages you communicate in', suspects: 'suspect languages', lived_in: 'countries you have lived in', plausible_business: 'countries you plausibly do business with', suspect: 'suspect countries', first_name: 'first name', last_names: 'last names', middle_names: 'middle names', emails: 'email addresses', usernames: 'usernames', postal_address: 'postal address' };
function inferredForm(labels) {
  return (holder, key, ctx) => {
    if (isObj(holder[key])) return formFor(inferFields(holder[key], labels), holder[key], ctx);
    return h('div', { class: 'form' }, fieldFor({ ...inferFields({ [key]: holder[key] }, labels)[0], wide: true }, holder, ctx));
  };
}

// ---------------------------------------------------------------- one screen for the entries of a file

// The entries of a mapping in a configuration file: a list on the left, the selected entry as a form (or as
// YAML) on the right. `single`: the value at `path` is itself the one entry.
// In-page dialogs: the browser's own prompt() and confirm() are blocked in embedded panes.
function ask(message, withInput) {
  return new Promise(resolve => {
    const input = withInput ? h('input', { style: 'width:100%' }) : null;
    const done = v => { d.close(); d.remove(); resolve(v); };
    const d = h('dialog', { class: 'ask' }, h('p', { style: 'margin-top:0' }, message), input,
      h('div', { class: 'row', style: 'justify-content:flex-end;gap:8px;margin-top:14px' },
        h('button', { onclick: () => done(withInput ? null : false) }, 'Cancel'),
        h('button', { class: 'primary', onclick: () => done(withInput ? input.value : true) }, 'OK')));
    d.addEventListener('cancel', e => { e.preventDefault(); done(withInput ? null : false); });
    if (input) input.addEventListener('keydown', e => { if (e.key === 'Enter') done(input.value); });
    document.body.append(d); d.showModal(); if (input) input.focus();
  });
}
const slug = v => (v || '').trim().toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '');

async function entryView(o) {
  const el = main();
  const [cat, cfg, jev, jobs] = await Promise.all([catalogue(), api('GET', `api/config/${o.file}`), api('GET', 'api/config/jev'), api('GET', 'api/config/jobs')]);
  const raw = (await api('POST', 'api/yaml/load', { text: cfg.text })).data || {};   // as written, with {{placeholders}}
  const parentPath = o.single ? o.path.slice(0, -1) : o.path;
  const holder = parentPath.reduce((d, k) => (d[k] = d[k] ?? {}), raw);
  const names = o.single ? [o.path[o.path.length - 1]] : Object.keys(holder);
  const key = 'sel:' + o.file + o.path.join('.');
  let sel = o.single ? names[0] : sessionStorage.getItem(key);
  let adding = false;
  if (!o.single && sel && sel.startsWith('+')) { sel = sel.slice(1); adding = !(sel in holder); if (adding) holder[sel] = JSON.parse(JSON.stringify(o.blank ?? {})); }
  if (!names.includes(sel) && !adding) sel = names[0];
  const again = () => entryView(o);
  const ctx = { cat, optionSets: jev.data.option_sets || {}, jobs: Object.keys(jobs.data.jobs || {}), yamlFields: [] };
  let tab = sessionStorage.getItem('tab:' + o.file) || 'form';
  const body = h('div', {});
  const ed = yamlEditor({ rows: 18 });
  const dump = async () => (await api('POST', 'api/yaml/dump', { data: holder[sel] })).text;
  const flush = async () => { for (const f of ctx.yamlFields) await f(); };
  const snippet = async () => { if (tab === 'yaml') return ed.ensureValid(); await flush(); return dump(); };
  const candidate = async () => (await api('POST', `api/node/${o.file}`, { path: [...parentPath, sel], snippet: await snippet() })).text;
  const drawBody = async () => {
    ctx.yamlFields = [];
    ctx.redraw = drawBody;
    if (tab === 'yaml') { ed.value = await dump(); body.replaceChildren(ed); } else body.replaceChildren(o.form(holder, sel, ctx));
  };
  const switchTab = wrap(async to => {
    if (to === tab) return;
    if (tab === 'yaml') holder[sel] = (await api('POST', 'api/yaml/load', { text: await ed.ensureValid() })).data; else await flush();
    tab = to; sessionStorage.setItem('tab:' + o.file, tab); tabs.querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.t === tab)); await drawBody();
  });
  const tabs = h('div', { class: 'tabs' }, [['form', 'Form'], ['yaml', 'YAML']].map(([t, text]) => h('button', { 'data-t': t, class: t === tab ? 'on' : '', onclick: () => switchTab(t) }, text)));
  const side = o.single ? null : h('div', { class: 'side' }, [...names, ...(adding ? [sel] : [])].map(n => h('a', { class: n === sel ? 'on' : '', onclick: () => { sessionStorage.setItem(key, n); again(); } }, n,
    o.columns ? h('span', { class: 'muted', style: 'font-size:12px' }, (o.columns(n, holder[n]) || '')) : null)),
    h('a', { onclick: async () => { const n = slug(await ask('Name (lower-case, no spaces):', true)); if (n) { sessionStorage.setItem(key, '+' + n); again(); } } }, '+ New'));
  const detail = h('div', {});
  const toolBox = h('div', {});
  el.replaceChildren(...[h('h1', {}, o.title), h('p', { class: 'lead' }, o.lead),
    o.sections ? h('div', { class: 'tabs' }, o.sections.map(s => h('button', { class: s.on ? 'on' : '', onclick: s.go }, s.label))) : null,
    side ? h('div', { class: 'split' }, side, detail) : detail,
    h('h2', {}, 'The file'), fileEditor(o.file, cfg.text, again)].filter(Boolean));
  if (sel === undefined) return;
  detail.append(h('div', { class: 'panel' },
    h('div', { class: 'row', style: 'margin-bottom:8px' }, h('h2', { style: 'margin:0', class: 'grow' }, o.single ? (o.entryTitle || sel) : sel, adding ? h('span', { class: 'muted' }, ' (new)') : ''),
      o.single || adding ? null : h('button', { class: 'danger', onclick: wrap(async () => { if (!(await ask(`Delete “${sel}”?`))) return; const t = await api('POST', `api/node/${o.file}`, { path: [...parentPath, sel], snippet: null }); sessionStorage.removeItem(key); reviewAndSave(o.file, t.text, again); }) }, 'Delete'),
      h('button', { class: 'primary', onclick: wrap(async () => { const text = await candidate(); sessionStorage.setItem(key, sel); reviewAndSave(o.file, text, again); }) }, 'Check and save…')),
    o.hint ? h('p', { class: 'muted', style: 'margin-top:0' }, o.hint(sel, holder[sel]) || '') : null,
    tabs, body), toolBox);
  await drawBody();
  if (o.tools) o.tools(toolBox, { sel, candidate, data: holder[sel] });
}

function emailPicker(onpick) {
  const results = h('div', {});
  const search = wrap(async q => {
    const rows = await api('GET', 'api/testset?limit=40&q=' + encodeURIComponent(q));
    results.replaceChildren(h('div', { class: 'tablewrap', style: 'max-height:30vh;margin-top:8px' }, h('table', {}, h('tbody', {}, rows.map(r => h('tr', { class: 'click', onclick: () => onpick(r) },
      h('td', { class: 'clip', style: 'width:32%' }, r.sender), h('td', { class: 'clip', style: 'width:50%' }, r.subject), h('td', {}, h('span', { class: 'muted' }, r.source))))))));
  });
  search('');
  return h('div', {}, h('input', { style: 'width:100%', placeholder: 'Pick a test email: search sender or subject', oninput: e => { clearTimeout(emailPicker.t); emailPicker.t = setTimeout(() => search(e.target.value), 250); } }), results);
}

const viewFacts = () => entryView({
  file: 'facts', path: [], title: 'Flags', form: factForm, blank: { help: '', pattern: '', in: 'subject' },
  lead: 'Every flag the rules can test: an observation about an email. Each is a detector with parameters; regular expressions are typed as they are. {{…}} takes a value from the profile, $name is another flag’s value.',
  columns: (n, d) => isObj(d) ? Object.keys(DETECTOR_TYPES).find(k => k in d) : '',
  tools: (box, ctx) => {
    const out = h('div', {});
    box.append(h('div', { class: 'panel' }, h('h3', { style: 'margin-top:0' }, 'Try it'),
      h('p', { class: 'muted' }, 'The value of this flag for a test email, as it is in the form above (saved or not).'),
      emailPicker(wrap(async r => {
        const res = await api('POST', 'api/try', { id: r.id, name: 'facts', text: await ctx.candidate() });
        out.replaceChildren(h('div', { class: 'banner ok' }, h('b', {}, ctx.sel), ' = ', h('span', { class: 'mono' }, show(res.facts[ctx.sel]))),
          h('div', { class: 'muted' }, `${res.sender} — ${res.subject} → `, badge(res.outcome), ' ', res.rule));
      })), out));
  },
});

function jevSections(on) {
  const go = id => () => { sessionStorage.setItem('jev-section', id); viewJev(); };
  return [['questions', 'Questions'], ['sets', 'Shared option sets'], ['input', 'What the classifier is shown']].map(([id, label]) => ({ label, on: id === on, go: go(id) }));
}
function viewJev() {
  const section = sessionStorage.getItem('jev-section') || 'questions';
  const preview = which => (box, ctx) => {
    const out = h('div', {});
    box.append(h('div', { class: 'panel' }, h('h3', { style: 'margin-top:0' }, 'As sent to the classifier'),
      h('p', { class: 'muted' }, 'For a test email, with the form above as it is now (saved or not).'),
      emailPicker(wrap(async r => {
        const res = await api('POST', 'api/jev/preview', { id: r.id, name: 'jev', text: await ctx.candidate() });
        const parts = [];
        if (which === 'question') parts.push(h('h3', {}, 'The question'), h('pre', {}, JSON.stringify(res.questions[ctx.sel], null, 1)));
        parts.push(h('h3', {}, 'The email as the classifier sees it'), h('pre', {}, JSON.stringify(res.input, null, 1)));
        out.replaceChildren(...parts);
      })), out));
  };
  if (section === 'sets') return entryView({ file: 'jev', path: ['option_sets'], title: 'Classifier', form: optionSetForm, blank: {}, sections: jevSections('sets'),
    lead: 'Option sets shared by several questions (a question can word single options differently).' });
  if (section === 'input') return entryView({ file: 'jev', path: ['input'], single: true, entryTitle: 'What the classifier is shown', title: 'Classifier', form: jevInputForm, sections: jevSections('input'), tools: preview('input'),
    lead: 'The parts of an email the classifier receives with every question. Changing this changes every answer: the check offers to re-ask the test set.' });
  return entryView({ file: 'jev', path: ['questions'], title: 'Classifier', form: questionForm, sections: jevSections('questions'), tools: preview('question'),
    blank: { type: 'choice', ask: 'first', question: '', options: { none: 'None of these.' } },
    lead: 'The questions the classifier (Jev) is asked, and when. A reworded question changes its answers for every email: the check offers to re-ask it on the test set before you save.',
    columns: (n, d) => isObj(d) ? (isObj(d.ask) ? 'gated' : d.type === 'yes_no' ? 'yes / no' : '') : '',
    hint: (n, d) => isObj(d) && isObj(d.ask) ? 'Asked in the second request, and only when a rule reads its answer.' : 'Asked in the first request, when a rule reads any first-request answer.' });
}

const viewOutcomes = () => entryView({
  file: 'outcomes', path: [], title: 'Outcomes', form: outcomeForm, blank: { help: '', do: [] },
  lead: 'What each result of a rule does in Gmail. Label changes of one outcome go to Gmail as a single call; labels that do not exist are created.',
});

function viewJobs() {
  const section = sessionStorage.getItem('jobs-section') || 'jobs';
  const go = id => () => { sessionStorage.setItem('jobs-section', id); viewJobs(); };
  const sections = [['jobs', 'Actions'], ['triggers', 'Triggers']].map(([id, label]) => ({ label, on: id === section, go: go(id) }));
  if (section === 'triggers') return entryView({ file: 'jobs', path: ['triggers'], single: true, entryTitle: 'Triggers', title: 'Actions', form: triggersForm, sections,
    lead: 'When actions run: on new mail in a label, when a label is put on a message, when the daemon starts, once a day, or every so many minutes.' });
  return entryView({
    file: 'jobs', path: ['jobs'], title: 'Actions', form: jobForm, sections, blank: { help: '', rules: true },
    lead: 'Everything besides deciding about one email: list syncs, retrospective moves, purges, clean-ups.',
    columns: (n, d) => isObj(d) ? (d.enabled === false ? 'off' : d.manual ? 'manual' : '') : '',
    tools: (box, ctx) => {
      const d = ctx.data || {};
      if (d.rules && d.manual) { cleanupPanel(box); return; }
      if (!(d.sync_list || (d.search && d.outcome))) { box.append(h('div', { class: 'panel muted' }, 'This job applies the rules to each email its trigger hands it.')); return; }
      const params = [...(d.search || '').matchAll(/\{(\w+)\}/g)].map(m => m[1]);
      const inputs = params.map(p => h('input', { placeholder: p, 'data-p': p }));
      const out = h('pre', { hidden: true });
      const run = dry => wrap(async () => {
        const ps = Object.fromEntries(inputs.map(i => [i.dataset.p, i.value.trim()]));
        if (!dry && !(await ask(`Run “${ctx.sel}” for real?`))) return;
        const r = await api('POST', `api/jobs/${ctx.sel}/run`, { params: ps, dry_run: dry });
        out.hidden = false; out.textContent = (dry ? 'Preview (nothing changed):\n' : 'Done:\n') + JSON.stringify(r.result, null, 1);
      });
      box.append(h('div', { class: 'panel' }, h('h3', { style: 'margin-top:0' }, 'Run the saved job'), h('div', { class: 'row' }, inputs,
        h('button', { onclick: run(true) }, 'Preview'), h('button', { class: 'danger', onclick: run(false) }, 'Run now')), out));
    },
  });
}

// Clean-up of a label: preview in the background, review every removal, then apply.
async function cleanupPanel(box) {
  const cat = await catalogue();
  const label = h('select', {}, h('option', { value: '' }, 'choose a label…'), cat.labels.map(l => h('option', { value: l, selected: l === sessionStorage.getItem('cleanup-label') }, l)));
  const limit = h('input', { type: 'number', min: 1, placeholder: 'all', style: 'width:90px' });
  const fresh = h('input', { type: 'checkbox' });
  const out = h('div', {});
  let timer = null;
  const senderOf = s => (s.match(/<([^>]+)>/) || [0, s])[1].toLowerCase();
  const show = st => {
    clearTimeout(timer);
    const L = label.value, parts = [];
    if (st.running) parts.push(h('div', { class: 'banner warn' }, `Previewing: ${st.classified} of ${st.total ?? '…'} emails classified. Nothing in Gmail changes. You can leave this page.`));
    if (st.error) parts.push(h('div', { class: 'banner bad' }, h('b', {}, 'The preview stopped'), ' — ' + st.error, h('div', { class: 'fix' }, 'What was classified is kept. Press Preview to continue where it stopped.')));
    if (st.failed && !st.running) parts.push(h('div', { class: 'banner bad' }, `${st.failed} emails could not be classified and are left untouched: ${st.failure}`));
    if (st.applied) parts.push(h('p', { class: 'muted' }, `${st.applied} emails of this label were already applied earlier.`));
    if (st.outcomes.length) parts.push(h('h3', {}, 'What would happen'), h('div', { class: 'tablewrap' }, h('table', {}, h('tbody', {},
      st.outcomes.map(([k, n]) => h('tr', {}, h('td', { style: 'width:70px;text-align:right' }, n), h('td', {}, k)))))));
    if (st.removals.length) {
      const groups = {};
      for (const r of st.removals) (groups[r.rule + ' · ' + senderOf(r.sender)] ??= []).push(r);
      const spare = ids => wrap(async () => show(await api('POST', `api/cleanup/${encodeURIComponent(L)}/spare`, { ids })));
      parts.push(h('h3', {}, `${st.removals.length} emails would be removed — check them`),
        h('p', { class: 'muted', style: 'margin-top:0' }, 'Grouped by rule and sender. “Keep” takes an email out of the removal: it stays where it is.'),
        Object.entries(groups).sort((a, b) => b[1].length - a[1].length).map(([k, rows]) => h('details', { class: 'panel', style: 'padding:8px 12px;margin-bottom:6px' },
          h('summary', {}, h('b', {}, rows.length), ' · ' + k + ' — ', h('span', { class: 'muted' }, rows[0].subject.slice(0, 70)), ' ',
            st.running ? null : h('button', { class: 'small', onclick: e => { e.preventDefault(); spare(rows.map(r => r.id))(); } }, 'Keep all')),
          h('table', {}, h('tbody', {}, rows.map(r => h('tr', {}, h('td', { class: 'clip', style: 'width:30%' }, r.sender), h('td', { class: 'clip' }, r.subject),
            h('td', { style: 'width:70px' }, st.running ? null : h('button', { class: 'small', onclick: spare([r.id]) }, 'Keep')))))))));
    }
    if (!st.running && st.outcomes.length) {
      const relabel = h('input', { type: 'checkbox', checked: true });
      parts.push(h('div', { class: 'row', style: 'margin-top:12px' }, h('label', {}, relabel, ` take “${L}” off the mail the rules no longer give it to`),
        h('button', { class: 'danger', onclick: wrap(async () => {
          if (!(await ask(`Apply this to the “${L}” label now? ${st.removals.length} emails go to Trash (restorable for 30 days); the rest is moved and relabelled.`))) return;
          const r = await api('POST', `api/cleanup/${encodeURIComponent(L)}/apply`, { relabel: relabel.checked });
          show(r); out.prepend(h('div', { class: 'banner ok' }, 'Done: ' + Object.entries(r.summary).map(([k, n]) => `${n} × ${k}`).join(' · ')));
        }) }, 'Apply…')));
    }
    if (!parts.length) parts.push(h('p', { class: 'muted' }, 'No preview for this label yet.'));
    out.replaceChildren(...parts.flat());
    if (st.running) timer = setTimeout(refresh, 3000);
  };
  const refresh = wrap(async () => { if (label.value && out.isConnected) show(await api('GET', `api/cleanup/${encodeURIComponent(label.value)}`)); });
  label.onchange = () => { sessionStorage.setItem('cleanup-label', label.value); out.replaceChildren(); refresh(); };
  box.append(h('div', { class: 'panel' }, h('h3', { style: 'margin-top:0' }, 'Clean up a label'),
    h('p', { class: 'muted', style: 'margin-top:0' }, 'Runs today’s rules over the mail already in a label. First a preview, which changes nothing; you check what would be removed, then apply.'),
    h('div', { class: 'row' }, label, h('label', {}, 'newest ', limit, ' emails'), h('label', {}, fresh, ' start over (forget the earlier preview)'),
      h('button', { class: 'primary', onclick: wrap(async () => {
        if (!label.value) return;
        await api('POST', `api/cleanup/${encodeURIComponent(label.value)}/start`, { limit: limit.value ? +limit.value : null, fresh: fresh.checked });
        fresh.checked = false; refresh();
      }) }, 'Preview')), out));
  refresh();
}

const viewProfile = () => entryView({ file: 'profile', path: [], title: 'Profile', form: inferredForm(PROFILE_LABELS), blank: '',
  lead: 'Who you are. Questions, flags and rules take these values through {{placeholders}}, so a change here updates everything that depends on it.' });
const viewSettings = () => entryView({ file: 'settings', path: [], title: 'Settings', form: inferredForm({}), blank: '',
  lead: 'Switches and limits of the engine. No behaviour is decided here.' });

// ---------------------------------------------------------------- Health

async function viewHealth() {
  const el = main();
  el.replaceChildren(h('h1', {}, 'Health'), h('p', { class: 'lead' }, 'Is everything running, connected, and consistent?'));
  const [hl, cr] = await Promise.all([api('GET', 'api/health'), api('GET', 'api/credentials')]);
  const line = (ok, title, text, extra) => h('div', { class: 'panel row' }, h('span', { class: 'dot ' + (ok === null ? 'warn' : ok ? 'ok' : 'bad') }), h('b', { style: 'width:150px' }, title), h('span', { class: 'grow' }, text), extra);
  el.append(
    ...hl.daemons.map(dm => { const age = dm.seconds_since_heartbeat; return line(age === null ? null : dm.running, hl.daemons.length > 1 ? `Daemon, ${dm.mailbox}` : 'Daemon',
      age === null ? 'no heartbeat recorded yet (it has not run here)'
        : dm.running ? `running — last sign of life ${Math.round(age / 60)} min ago` : `no sign of life for ${Math.round(age / 60)} min`); }),
    line(true, 'Last email', hl.last_email ? `${fmtTime(hl.last_email.ts)} · ${hl.last_email.sender} · ${hl.last_email.action}` : 'none yet'),
    line(hl.errors_last_day === 0, 'Errors, last day', String(hl.errors_last_day)),
    line(hl.config_problems.length === 0, 'Configuration', hl.config_problems.length ? hl.config_problems.join(' · ') : 'consistent'),
    line(hl.test_emails > 0, 'Test set', `${hl.test_emails} emails`),
    line(cr.gmail.connected, 'Gmail', cr.gmail.connected ? `connected as ${cr.gmail.detail}` : `not connected — ${cr.gmail.detail}`,
      cr.gmail.fixed_token ? h('span', { class: 'muted' }, 'sign-in set in the options') : h('button', { onclick: () => connectGmail() }, cr.gmail.connected ? 'Reconnect' : 'Connect Gmail')),
    line(cr.outlook.connected ? true : cr.outlook.signed_in ? false : null, 'Outlook',
      cr.outlook.connected ? `connected as ${cr.outlook.detail}` : cr.outlook.signed_in ? `not connected — ${cr.outlook.detail}`
        : cr.outlook.client_id_present ? 'not connected — optional: a second mailbox, sorted by the same rules'
        : 'optional: a second mailbox. To add one, set your Microsoft client ID in the options first.',
      cr.outlook.client_id_present ? h('button', { onclick: wrap(connectOutlook) }, cr.outlook.connected ? 'Reconnect' : 'Connect Outlook') : null),
    line(cr.typesafe.key_present, 'Classifier (TypeSafe Jev)', cr.typesafe.key_present ? 'API key present' : 'TYPESAFE_API_KEY is not set'));
  if (!cr.gmail.client_secrets_present) el.append(h('div', { class: 'banner warn' }, `“Connect Gmail” needs your Google OAuth client: its client id and secret in the options, or the client file at ${cr.gmail.client_secrets_file}.`));
  el.append(h('h2', {}, 'Recent changes'), h('div', { class: 'tablewrap' }, h('table', {}, h('tbody', {},
    hl.events.length ? hl.events.map(e => h('tr', {}, h('td', { style: 'white-space:nowrap;width:1%' }, fmtTime(e.ts)), h('td', { style: 'width:1%' }, badge(e.kind)), h('td', {}, e.detail)))
      : h('tr', {}, h('td', { class: 'muted' }, 'nothing yet'))))));
}

// Gmail sign-in in two steps, for a server that has no browser of its own.
function connectGmail() {
  const input = h('input', { style: 'width:100%', placeholder: 'http://localhost:8765/?state=…&code=…' });
  const note = h('div', {});
  const done = () => { d.close(); d.remove(); };
  const d = h('dialog', { class: 'ask', style: 'max-width:620px' },
    h('h3', { style: 'margin-top:0' }, 'Connect Gmail'),
    h('p', {}, h('b', {}, '1. '), 'Sign in with Google in a new tab and approve the access. If Google says it has not verified the app, choose Advanced and continue.'),
    h('button', { class: 'primary', onclick: wrap(async () => { const r = await api('POST', 'api/oauth/link'); window.open(r.url, '_blank', 'noopener'); }) }, 'Open Google sign-in'),
    h('p', {}, h('b', {}, '2. '), 'After you approve, your browser lands on a page that does not load (an address starting with http://localhost:8765). That is expected. Copy that whole address from the address bar and paste it here.'),
    input, note,
    h('div', { class: 'row', style: 'justify-content:flex-end;gap:8px;margin-top:14px' }, h('button', { onclick: done }, 'Cancel'),
      h('button', { class: 'primary', onclick: async () => {
        try { const r = await api('POST', 'api/oauth/finish', { url: input.value }); if (r.gmail.connected) { done(); toast('Gmail is connected as ' + r.gmail.detail); route(); } else note.replaceChildren(h('div', { class: 'banner bad' }, 'Stored, but Gmail still refuses: ' + r.gmail.detail)); }
        catch (e) { note.replaceChildren(h('div', { class: 'banner bad', style: 'margin-top:8px' }, e.message || String(e))); }
      } }, 'Connect')));
  d.addEventListener('cancel', e => { e.preventDefault(); done(); });
  document.body.append(d); d.showModal();
}

// Outlook sign-in: a short code typed on Microsoft's page, on any device. This page asks until it is approved.
async function connectOutlook() {
  const r = await api('POST', 'api/outlook/link');
  let open = true;
  const note = h('div', { class: 'muted', style: 'margin-top:10px' }, 'Waiting for you to enter the code…');
  const done = () => { open = false; d.close(); d.remove(); };
  const d = h('dialog', { class: 'ask', style: 'max-width:620px' },
    h('h3', { style: 'margin-top:0' }, 'Connect Outlook'),
    h('p', {}, h('b', {}, '1. '), 'Open Microsoft’s sign-in page (on this or any other device):'),
    h('a', { class: 'btn primary', href: r.url, target: '_blank', rel: 'noopener' }, 'Open Microsoft sign-in'),
    h('p', {}, h('b', {}, '2. '), 'Enter this code there, sign in to the mailbox, and approve the access:'),
    h('pre', { style: 'font-size:22px;letter-spacing:3px;text-align:center' }, r.code), note,
    h('div', { class: 'row', style: 'justify-content:flex-end;gap:8px;margin-top:14px' }, h('button', { onclick: done }, 'Cancel')));
  d.addEventListener('cancel', e => { e.preventDefault(); done(); });
  document.body.append(d); d.showModal();
  const until = Date.now() + r.expires_in * 1000;
  while (open && Date.now() < until) {
    await new Promise(ok => setTimeout(ok, r.interval * 1000));
    if (!open) return;
    try {
      const s = await api('POST', 'api/outlook/finish');
      if (s.pending) continue;
      done(); toast(s.outlook.connected ? 'Outlook is connected as ' + s.outlook.detail : 'Stored, but Outlook still refuses: ' + s.outlook.detail); route();
      return;
    } catch (e) { note.replaceChildren(h('div', { class: 'banner bad' }, e.message || String(e))); return; }
  }
  if (open) note.replaceChildren(h('div', { class: 'banner bad' }, 'The code has expired. Close this and try again.'));
}

// ---------------------------------------------------------------- Backup

async function viewBackup() {
  const el = main();
  const cr = await api('GET', 'api/credentials');
  el.replaceChildren(h('h1', {}, 'Backup'), h('p', { class: 'lead' }, 'Take this installation with you, or bring one in. A bundle is one zip file; it never contains a mailbox sign-in.'));
  el.append(h('h2', {}, 'Export'), h('div', { class: 'panel' },
    h('p', { class: 'muted', style: 'margin-top:0' }, 'The configuration is everything mailman needs to sort mail: profile, lists, flags, questions, rules. The full bundle adds the log (history and undo) and the test set (stored emails that “Check and save” runs on); it contains personal mail.'),
    h('div', { class: 'row' }, h('a', { class: 'btn primary', href: 'api/export', download: '' }, 'Download the configuration'),
      h('a', { class: 'btn', href: 'api/export?everything=true', download: '' }, 'Download everything'))));
  const file = h('input', { type: 'file', accept: '.zip' });
  el.append(h('h2', {}, 'Import'), h('div', { class: 'panel' },
    h('p', { class: 'muted', style: 'margin-top:0' }, 'Replaces the configuration here with the bundle’s, and the log and test set too if the bundle has them. The mailbox sign-ins are kept. The app restarts.'),
    cr.can_import ? h('div', { class: 'row' }, file, h('button', { class: 'danger', onclick: wrap(async () => {
      if (!file.files[0]) return;
      if (!(await ask('Replace the configuration of this installation with the bundle?'))) return;
        const f = file.files[0], size = 192 * 1024, parts = Math.max(1, Math.ceil(f.size / size));
        for (let i = 0; i < parts; i++) {   // small pieces as text: the same kind of request as everything else here
          toast(`Uploading… ${Math.round(100 * i / parts)}%`, 60000);
          let bytes;
          try { bytes = new Uint8Array(await f.slice(i * size, (i + 1) * size).arrayBuffer()); }
          catch (e) { throw new Error('Your browser could not read the file (' + (e.message || e) + '). Choose it again, from a folder the browser may read, e.g. Downloads.'); }
          let text = ''; for (let k = 0; k < bytes.length; k += 0x8000) text += String.fromCharCode.apply(null, bytes.subarray(k, k + 0x8000));
          try { await api('POST', 'api/import', { part: i, last: i === parts - 1, data: btoa(text) }); }
          catch (e) { throw new Error(`The upload stopped at piece ${i + 1} of ${parts}: ` + (e.message || e)); }
        }
        toast('Imported. The app is restarting…', 15000);
        for (let i = 0; i < 40; i++) { await new Promise(r => setTimeout(r, 3000)); try { if ((await fetch('api/health')).ok && i > 1) break; } catch (e) { /* still restarting */ } }
        location.reload();
    }) }, 'Import…')) : h('p', {}, 'Importing works in the installed app. Here, unzip the bundle over the config and data folders yourself.')));
}

async function strip() {
  try {
    const hl = await api('GET', 'api/health');
    document.getElementById('strip').replaceChildren(
      h('div', {}, h('span', { class: 'dot ' + (hl.daemon_seconds_since_heartbeat === null ? 'warn' : hl.daemon_running ? 'ok' : 'bad') }), 'daemon'),
      h('div', {}, h('span', { class: 'dot ' + (hl.config_problems.length ? 'bad' : 'ok') }), 'configuration'),
      h('div', {}, h('span', { class: 'dot ' + (hl.errors_last_day ? 'warn' : 'ok') }), `${hl.errors_last_day} errors today`),
      h('div', {}, h('span', { class: 'dot ' + (hl.waiting ? 'bad' : 'ok') }), `${hl.waiting} waiting`));
    const mine = document.body.dataset.version, stale = hl.version && mine && hl.version !== mine;
    document.getElementById('alerts').replaceChildren(
      ...(stale ? [h('div', { class: 'banner warn' }, h('b', {}, `Mailman was updated to ${hl.version}`), ` — this page is still version ${mine}. `,
        h('button', { class: 'small', onclick: () => location.reload() }, 'Reload'))] : []),
      ...(hl.alerts || []).map(a => h('div', { class: 'banner bad' },
        h('b', {}, a.title), a.detail ? h('span', {}, ' — ' + a.detail) : null, h('div', { class: 'fix' }, a.fix))));
  } catch (e) {
    document.getElementById('alerts').replaceChildren(h('div', { class: 'banner bad' }, h('b', {}, 'The interface cannot reach its own server'), ' — ' + (e.message || e)));
  }
}

// ---------------------------------------------------------------- Test set

const testState = { q: '', source: '', status: '', offset: 0 };
const TEST_PAGE = 100;

async function viewTestset() {
  const el = main();
  const cat = await catalogue();
  el.replaceChildren(h('h1', {}, 'Test set'),
    h('p', { class: 'lead' }, 'The emails every change to the configuration is checked against: a cached sample, and the emails you marked with the outcome you expect. Click one to read it, change what you expect of it, or take it out.'));
  const summary = h('div', { class: 'row', style: 'margin-bottom:10px' });
  const table = h('div', { class: 'tablewrap' });
  const count = h('span', { class: 'muted' });
  const source = h('select', { onchange: e => { testState.source = e.target.value; testState.offset = 0; load(); } });
  const load = wrap(async () => {
    const r = await api('GET', 'api/testset/results?' + new URLSearchParams({ ...testState, limit: TEST_PAGE }));
    const c = r.counts;
    summary.replaceChildren(
      h('span', { class: 'badge ' + (c.broken ? 'error' : 'keep') }, c.broken ? `${c.broken} not as expected` : 'all expectations met'),
      h('span', { class: 'muted' }, `${c.ok} as expected · ${c.unmarked} with no expectation`));
    source.replaceChildren(h('option', { value: '' }, 'any source'), r.sources.map(x => h('option', { value: x, selected: x === testState.source }, x)));
    count.textContent = `${r.total.toLocaleString()} emails` + (r.total > TEST_PAGE ? ` · showing ${testState.offset + 1}–${testState.offset + r.rows.length}` : '');
    table.replaceChildren(h('table', {},
      h('thead', {}, h('tr', {}, ['From', 'Subject', 'Source', 'You expect', 'Gets now'].map(t => h('th', {}, t)))),
      h('tbody', {}, r.rows.length ? r.rows.map(x => h('tr', { class: 'click', onclick: () => caseDetail(x.id, load) },
        h('td', { class: 'clip', style: 'width:22%' }, x.sender), h('td', { class: 'clip', style: 'width:34%' }, x.subject),
        h('td', {}, h('span', { class: 'muted' }, x.source)),
        h('td', {}, x.expected ? [badge(x.expected.decision), ' ', (x.expected.labels || []).map(l => [badge(l, 'label'), ' '])] : h('span', { class: 'muted' }, '—')),
        h('td', {}, resultCell(x), x.status === 'broken' ? badge('differs', 'error') : null)))
        : h('tr', {}, h('td', { colspan: 5, class: 'muted' }, 'No emails match.')))));
  });
  const input = h('input', { class: 'grow', placeholder: 'Search sender or subject', value: testState.q });
  input.oninput = () => { testState.q = input.value; testState.offset = 0; clearTimeout(viewTestset.t); viewTestset.t = setTimeout(load, 250); };
  el.append(summary, h('div', { class: 'row', style: 'margin-bottom:10px' }, input, source,
    h('select', { onchange: e => { testState.status = e.target.value; testState.offset = 0; load(); } },
      [['', 'any status'], ['broken', 'not as expected'], ['ok', 'as expected'], ['unmarked', 'no expectation']].map(([v, t]) => h('option', { value: v, selected: v === testState.status }, t))),
    h('button', { onclick: () => { testState.offset = Math.max(0, testState.offset - TEST_PAGE); load(); } }, '‹'),
    h('button', { onclick: () => { testState.offset += TEST_PAGE; load(); } }, '›'), count), table);
  load();
}

async function caseDetail(id, after) {
  const [d, cat] = await Promise.all([api('GET', `api/testset/${encodeURIComponent(id)}`), catalogue()]);
  const outcome = h('select', {}, h('option', { value: '' }, '— nothing expected —'),
    Object.keys(cat.outcomes).map(o => h('option', { value: o, selected: d.expected && d.expected.decision === o }, o)));
  const labels = h('input', { class: 'grow', placeholder: 'expected labels, comma-separated (optional)', value: ((d.expected || {}).labels || []).join(', ') });
  const note = h('input', { class: 'grow', placeholder: 'why (optional)', value: d.note || '' });
  const answersText = JSON.stringify(d.answers, null, 1);
  const answers = h('textarea', { rows: 10, spellcheck: 'false' }, answersText);
  const body = h('div', {});
  body.append(h('h2', {}, d.headers.subject || '(no subject)'),
    h('dl', { class: 'kv' }, Object.entries(d.headers).filter(([k]) => k !== 'subject').map(([k, v]) => [h('dt', {}, k), h('dd', {}, v)]),
      h('dt', {}, 'Source'), h('dd', {}, d.source),
      h('dt', {}, 'Gets now'), h('dd', {}, badge(d.outcome), ' ', d.labels.map(l => [badge(l, 'label'), ' ']), h('span', { class: 'muted' }, ' ' + (d.rule || 'no rule')))),
    h('h3', {}, 'The email as the rules read it'), h('pre', { style: 'max-height:260px;overflow:auto;white-space:pre-wrap' }, d.body || d.snippet || '(empty)'),
    h('h3', {}, 'What you expect'),
    h('p', { class: 'muted', style: 'margin-top:0' }, 'Every later change to the configuration is checked against this. ' +
      (d.marked ? '' : 'Changing it adds this email to the marked ones.')),
    h('div', { class: 'row' }, outcome, labels), h('div', { class: 'row', style: 'margin-top:8px' }, note),
    h('h3', {}, "The classifier's stored answers"),
    h('p', { class: 'muted', style: 'margin-top:0' }, 'What the classifier said about this email, as JSON. Edit them to try a different answer; the rules read these instead of asking again.'),
    answers);
  const save = wrap(async () => {
    let parsed = null;
    if (answers.value !== answersText) {
      try { parsed = JSON.parse(answers.value); } catch (e) { throw new Error('The classifier answers are not valid JSON: ' + e.message); }
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('The classifier answers must be a JSON object.');
    }
    await api('PUT', `api/testset/${encodeURIComponent(id)}`, {
      decision: outcome.value || null, labels: labels.value.split(',').map(s => s.trim()).filter(Boolean), note: note.value, answers: parsed });
    toast('saved'); closeModal(); after();
  });
  const remove = wrap(async () => {
    if (!(await ask(`Take “${d.headers.subject || id}” out of the test set?`))) return;
    await api('DELETE', `api/testset/${encodeURIComponent(id)}`);
    toast('removed from the test set'); closeModal(); after();
  });
  body.append(h('div', { class: 'foot' },
    d.marked ? h('button', { class: 'danger', onclick: remove }, d.source === 'marked' ? 'Remove from the test set' : 'Remove') : null,
    h('button', { onclick: closeModal }, 'Close'), h('button', { class: 'primary', onclick: save }, 'Save')));
  modal(body, true);
}

// ---------------------------------------------------------------- routing

const ROUTES = { log: viewLog, rules: viewRules, lists: viewLists, facts: viewFacts, jev: viewJev, outcomes: viewOutcomes,
  testset: viewTestset, actions: viewJobs, profile: viewProfile, settings: viewSettings, health: viewHealth, backup: viewBackup };

const ROUTE_ALIASES = { jobs: 'actions' };   // the Actions page was Automations at #/jobs: old bookmarks still work

function route() {
  let name = (location.hash.replace(/^#\//, '') || 'log').split('/')[0];
  if (ROUTE_ALIASES[name]) { name = ROUTE_ALIASES[name]; history.replaceState(null, '', '#/' + name); }
  document.querySelectorAll('#nav a').forEach(a => a.classList.toggle('active', a.getAttribute('href') === '#/' + name));
  closeModal();
  main().replaceChildren(h('p', { class: 'muted' }, 'Loading…'));
  wrap(ROUTES[name] || viewLog)();
}
window.addEventListener('hashchange', route);
route(); strip(); setInterval(strip, 60000);
