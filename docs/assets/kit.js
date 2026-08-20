/* Shared interactive kit for the AI-PCG-Scene documents.
 *
 * Vanilla, no dependencies, no build step. Every widget is driven by data the
 * caller passes in, so a page can only show what it actually measured.
 *
 *   <link rel="stylesheet" href="assets/kit.css">
 *   <script src="assets/kit.js"></script>
 *
 * Widgets: Kit.theme, Kit.wipe, Kit.stepper, Kit.lineChart, Kit.probe,
 *          Kit.filterTable, Kit.tabs.
 */
const Kit = (() => {
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => [...(r || document).querySelectorAll(s)];
  const cssv = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const esc = s => String(s).replace(/[&<>"]/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  /* ---------- theme ---------- */
  function theme(btnSel, key = 'asb-theme', onChange) {
    try { const t = localStorage.getItem(key); if (t) document.documentElement.dataset.theme = t; } catch (e) {}
    const b = $(btnSel);
    if (!b) return;
    b.onclick = () => {
      const cur = document.documentElement.dataset.theme
        || (matchMedia('(prefers-color-scheme:dark)').matches ? 'dark' : 'light');
      const nx = cur === 'dark' ? 'light' : 'dark';
      document.documentElement.dataset.theme = nx;
      try { localStorage.setItem(key, nx); } catch (e) {}
      if (onChange) onChange(nx);
    };
  }

  /* ---------- before / after wipe ---------- */
  function wipe(sel, { a, b, labelA = '', labelB = '', start = 50 } = {}) {
    const el = typeof sel === 'string' ? $(sel) : sel;
    if (!el) return null;
    el.classList.add('wipe');
    el.innerHTML =
      `<img class="base" alt="${esc(labelB)}">` +
      `<div class="top"><img class="over" alt="${esc(labelA)}"></div>` +
      (labelA ? `<div class="lbl" style="left:10px">${esc(labelA)}</div>` : '') +
      (labelB ? `<div class="lbl" style="right:10px">${esc(labelB)}</div>` : '') +
      `<div class="bar"></div><div class="knob">⇔</div>`;
    const base = $('.base', el), over = $('.over', el),
          bar = $('.bar', el), knob = $('.knob', el), top = $('.top', el);
    base.src = b; over.src = a;
    let p = start;
    const set = v => {
      p = Math.max(0, Math.min(100, v));
      top.style.width = p + '%';
      bar.style.left = p + '%';
      knob.style.left = p + '%';
      over.style.width = el.clientWidth + 'px';
    };
    const from = e => {
      const r = el.getBoundingClientRect();
      set(((e.clientX ?? (e.touches && e.touches[0].clientX)) - r.left) / r.width * 100);
    };
    let drag = false;
    el.addEventListener('pointerdown', e => { drag = true; el.setPointerCapture(e.pointerId); from(e); });
    el.addEventListener('pointermove', e => { if (drag) from(e); });
    el.addEventListener('pointerup', () => { drag = false; });
    el.addEventListener('pointercancel', () => { drag = false; });
    base.addEventListener('load', () => set(p));
    addEventListener('resize', () => set(p));
    // guard: e.target is window for synthetic events and has no .matches
    addEventListener('keydown', e => {
      const t = e.target;
      if (t && t.matches && t.matches('input,textarea,select')) return;
      if (e.key === 'ArrowLeft') { set(p - 3); e.preventDefault(); }
      if (e.key === 'ArrowRight') { set(p + 3); e.preventDefault(); }
    });
    set(start);
    return { set, get: () => p };
  }

  /* ---------- stepper ---------- */
  function stepper({ items, dots, img, badge, title, body, metrics, prev, next, play,
                     pos, imgBase = '', metricKeys = [], onChange, interval = 1900 }) {
    let cur = 0, timer = null;
    const dotsEl = $(dots), imgEl = $(img);
    dotsEl.innerHTML = items.map((m, i) =>
      `<button class="dot" data-i="${i}" title="${esc(m.title)}">${esc(m.n ?? i + 1)}</button>`).join('');
    function stop() {
      if (timer) { clearInterval(timer); timer = null; }
      const p = $(play); if (p) { p.textContent = '▶ 自动播放'; p.classList.remove('on'); }
    }
    function render(i) {
      cur = (i + items.length) % items.length;
      const m = items[cur], pv = cur > 0 ? items[cur - 1] : null;
      if (imgEl) { imgEl.src = imgBase + m.img; imgEl.alt = m.title; }
      if ($(badge)) $(badge).textContent = `${String(m.n ?? cur + 1).padStart(2, '0')}  ${m.title}`;
      if ($(title)) $(title).textContent = `${m.n ?? cur + 1} · ${m.title}`;
      if ($(body)) $(body).innerHTML = m.html || esc(m.what || '');
      if ($(pos)) $(pos).textContent = `第 ${cur + 1} / ${items.length} 步`;
      if ($(metrics) && metricKeys.length) {
        $(metrics).innerHTML = metricKeys.map(([k, lbl, dg = 3, lowerBetter = false]) => {
          if (m[k] == null) return '';
          let d = '';
          if (pv && pv[k] != null) {
            const df = m[k] - pv[k];
            const better = lowerBetter ? Math.abs(m[k]) < Math.abs(pv[k]) : df > 0;
            if (Math.abs(df) > 1e-9)
              d = `<span class="${better ? 'win' : 'loss'}">${df > 0 ? '+' : ''}${df.toFixed(dg)}</span>`;
          }
          return `<div class="mcell"><div class="k">${esc(lbl)}</div>` +
                 `<div class="v">${typeof m[k] === 'number' ? m[k].toFixed(dg) : esc(m[k])}</div>` +
                 `<div class="d">${d || '&nbsp;'}</div></div>`;
        }).join('');
      }
      $$('.dot', dotsEl).forEach((d, j) => d.classList.toggle('on', j === cur));
      if (onChange) onChange(cur, m);
    }
    dotsEl.onclick = e => { const b = e.target.closest('.dot'); if (b) { stop(); render(+b.dataset.i); } };
    if ($(prev)) $(prev).onclick = () => { stop(); render(cur - 1); };
    if ($(next)) $(next).onclick = () => { stop(); render(cur + 1); };
    if ($(play)) $(play).onclick = () => {
      if (timer) return stop();
      $(play).textContent = '❚❚ 暂停'; $(play).classList.add('on');
      timer = setInterval(() => render(cur + 1), interval);
    };
    render(0);
    return { render, stop, index: () => cur };
  }

  /* ---------- interactive line chart ---------- */
  function lineChart(svgSel, { rows, series, xLabel, lo, hi, onPick, legend }) {
    const svg = $(svgSel);
    const shown = new Set(series.map(s => s.key));
    if (legend && $(legend)) {
      $(legend).innerHTML = series.map(s =>
        `<button class="lg" data-k="${s.key}"><span class="sw" style="background:var(${s.color})"></span>${esc(s.label)}</button>`).join('');
      $(legend).onclick = e => {
        const b = e.target.closest('.lg'); if (!b) return;
        const k = b.dataset.k;
        shown.has(k) ? shown.delete(k) : shown.add(k);
        b.classList.toggle('off', !shown.has(k));
        draw();
      };
    }
    let active = 0;
    const vals = rows.flatMap(r => series.map(s => r[s.key])).filter(v => v != null);
    const LO = lo != null ? lo : Math.min(...vals) * 0.94;
    const HI = hi != null ? hi : Math.max(...vals) * 1.06;
    function draw(cur) {
      if (cur != null) active = cur;
      const W = 900, H = 340, L = 54, R = 128, T = 18, B = 52;
      const x = i => L + (W - L - R) * i / Math.max(1, rows.length - 1);
      const y = v => H - B - (H - T - B) * (v - LO) / (HI - LO);
      let o = '';
      for (let g = 0; g <= 5; g++) {
        const v = LO + (HI - LO) * g / 5, yy = y(v);
        o += `<line x1="${L}" y1="${yy}" x2="${W - R}" y2="${yy}" stroke="${cssv('--rule')}"/>`;
        o += `<text x="${L - 9}" y="${yy + 4}" text-anchor="end" font-size="12" fill="${cssv('--faint')}">${v.toFixed(2)}</text>`;
      }
      rows.forEach((r, i) => {
        o += `<text x="${x(i)}" y="${H - B + 20}" text-anchor="middle" font-size="12" fill="${cssv('--faint')}">${esc(r.tick ?? i + 1)}</text>`;
        if (i === active) o += `<line x1="${x(i)}" y1="${T}" x2="${x(i)}" y2="${H - B}" stroke="${cssv('--acc')}" stroke-width="1.5" stroke-dasharray="4 3"/>`;
      });
      series.forEach(s => {
        if (!shown.has(s.key)) return;
        const c = cssv(s.color);
        const pts = rows.map((r, i) => [x(i), y(r[s.key])]);
        o += `<polyline fill="none" stroke="${c}" stroke-width="2.5" stroke-linejoin="round" points="${pts.map(p => p.join(',')).join(' ')}"/>`;
        pts.forEach((p, i) => { o += `<circle cx="${p[0]}" cy="${p[1]}" r="${i === active ? 5.5 : 3.5}" fill="${c}"/>`; });
        const last = pts[pts.length - 1];
        o += `<text x="${last[0] + 8}" y="${last[1] + 4}" font-size="12.5" font-weight="600" fill="${c}">${esc(s.label)}</text>`;
      });
      if (xLabel) o += `<text x="${L}" y="${H - 6}" font-size="12" fill="${cssv('--faint')}">${esc(xLabel)}</text>`;
      rows.forEach((r, i) => {
        const tip = series.filter(s => r[s.key] != null)
          .map(s => `${s.label} ${(+r[s.key]).toFixed(3)}`).join('  ');
        o += `<rect x="${x(i) - 16}" y="${T}" width="32" height="${H - T - B}" fill="transparent" style="cursor:pointer" data-i="${i}"><title>${esc(r.tick ?? i + 1)}  ${esc(tip)}</title></rect>`;
      });
      svg.innerHTML = o;
      $$('[data-i]', svg).forEach(r => r.onclick = () => { draw(+r.dataset.i); if (onPick) onPick(+r.dataset.i); });
    }
    draw(0);
    return { draw };
  }

  /* ---------- image region probe ---------- */
  function probe(sel, { img, w, h, boxes, chips, readout, render }) {
    const el = typeof sel === 'string' ? $(sel) : sel;
    el.classList.add('probe');
    el.innerHTML = `<img src="${img}" alt="">`;
    el.insertAdjacentHTML('beforeend', boxes.map(b =>
      `<div class="rbox" data-k="${esc(b.key)}" style="left:${b.rect[0] / w * 100}%;top:${b.rect[1] / h * 100}%;` +
      `width:${(b.rect[2] - b.rect[0]) / w * 100}%;height:${(b.rect[3] - b.rect[1]) / h * 100}%"></div>`).join(''));
    if (chips && $(chips)) {
      $(chips).innerHTML = boxes.map(b =>
        `<button class="rchip" data-k="${esc(b.key)}">${b.chip || esc(b.key)}</button>`).join('');
    }
    function show(k) {
      const b = boxes.find(z => z.key === k); if (!b) return;
      $$('.rbox', el).forEach(x => x.classList.toggle('on', x.dataset.k === k));
      if (chips && $(chips)) $$('.rchip', $(chips)).forEach(x => x.classList.toggle('on', x.dataset.k === k));
      if (readout && $(readout)) $(readout).innerHTML = render(b);
    }
    el.addEventListener('mouseover', e => { const b = e.target.closest('.rbox'); if (b) show(b.dataset.k); });
    el.addEventListener('click', e => { const b = e.target.closest('.rbox'); if (b) show(b.dataset.k); });
    if (chips && $(chips)) {
      $(chips).addEventListener('mouseover', e => { const b = e.target.closest('.rchip'); if (b) show(b.dataset.k); });
      $(chips).addEventListener('click', e => { const b = e.target.closest('.rchip'); if (b) show(b.dataset.k); });
    }
    return { show };
  }

  /* ---------- filterable table ---------- */
  function filterTable({ rows, filters, filterEl, bodyEl, columns, onHover, initial = 'all' }) {
    let f = initial;
    if (filterEl && $(filterEl)) {
      $(filterEl).innerHTML = filters.map(x =>
        `<button class="btn${x.key === initial ? ' on' : ''}" data-f="${esc(x.key)}">${esc(x.label)}</button>`).join('');
      $(filterEl).onclick = e => {
        const b = e.target.closest('[data-f]'); if (!b) return;
        f = b.dataset.f;
        $$('.btn', $(filterEl)).forEach(x => x.classList.toggle('on', x === b));
        draw();
      };
    }
    function draw() {
      const list = rows.filter(r => f === 'all' || (filters.find(x => x.key === f).test || (z => z._f === f))(r));
      $(bodyEl).innerHTML = list.map(r =>
        `<tr data-id="${esc(r.id)}">` + columns.map(c => `<td class="${c.cls || ''}">${c.get(r)}</td>`).join('') + '</tr>').join('');
    }
    if (onHover) {
      $(bodyEl).addEventListener('mouseover', e => {
        const tr = e.target.closest('tr'); if (!tr) return;
        $$('tr', $(bodyEl)).forEach(x => x.classList.toggle('on', x === tr));
        onHover(rows.find(r => String(r.id) === tr.dataset.id));
      });
    }
    draw();
    return { draw };
  }

  /* ---------- tabs ---------- */
  function tabs(tabSel, panelSel, onChange) {
    const t = $$(tabSel), p = $$(panelSel);
    function go(i) {
      t.forEach((x, j) => x.classList.toggle('on', j === i));
      p.forEach((x, j) => x.style.display = j === i ? '' : 'none');
      if (onChange) onChange(i);
    }
    t.forEach((x, i) => x.onclick = () => go(i));
    go(0);
    return { go };
  }

  return { $, $$, cssv, esc, theme, wipe, stepper, lineChart, probe, filterTable, tabs };
})();
