// Headless check that a document is actually an interactive page, not a printout.
//
// Parallel authors cannot share one browser pane, so verification runs here
// instead: it loads the page, fails on console errors or broken images, asserts
// the interactive widgets exist and respond, and checks both themes and a
// mobile width for horizontal overflow.
//
//   node tools/verify_page.js <url> [--expect wipe,stepper,chart,probe,table]
//
// Exit code 0 = pass, 1 = fail.

const pc = require('puppeteer-core');
const fs = require('fs');

const CHROME = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  (process.env.LOCALAPPDATA || '') + '/Google/Chrome/Application/chrome.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
].find(p => { try { return fs.existsSync(p); } catch (e) { return false; } });

const args = process.argv.slice(2);
const url = args[0];
const expect = (args.includes('--expect') ? args[args.indexOf('--expect') + 1] : '').split(',').filter(Boolean);
if (!url) { console.error('usage: verify_page.js <url> [--expect a,b]'); process.exit(2); }

const fail = [];
const note = m => console.log('  ' + m);

(async () => {
  const browser = await pc.launch({ executablePath: CHROME, headless: 'new',
                                    args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const page = await browser.newPage();
  const errors = [];
  // the browser requests /favicon.ico on its own; a 404 there is not a page defect
  const IGNORE = /favicon\.ico/i;
  // a console "Failed to load resource" carries no URL in its text -- the URL is
  // in location(); real 4xx are caught by the response listener below, which has it
  page.on('console', m => {
    if (m.type() !== 'error') return;
    const t = m.text(), u = (m.location() && m.location().url) || '';
    if (IGNORE.test(u) || IGNORE.test(t)) return;
    if (/Failed to load resource/i.test(t)) return;
    errors.push(t);
  });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('response', r => { if (r.status() >= 400 && !IGNORE.test(r.url())) errors.push(`HTTP ${r.status()} ${r.url()}`); });
  await page.setViewport({ width: 1280, height: 900 });
  await page.goto(url, { waitUntil: 'networkidle0', timeout: 120000 });
  await new Promise(r => setTimeout(r, 700));

  const info = await page.evaluate(() => {
    const imgs = [...document.images];
    return {
      title: document.title,
      images: imgs.length,
      broken: imgs.filter(i => !i.complete || i.naturalWidth === 0).map(i => i.getAttribute('src')),
      widgets: {
        wipe: document.querySelectorAll('.wipe').length,
        stepper: document.querySelectorAll('.dot').length,
        chart: document.querySelectorAll('.chart svg *').length,
        probe: document.querySelectorAll('.rbox').length,
        table: document.querySelectorAll('tbody tr').length,
        filters: document.querySelectorAll('.filters .btn, [data-f]').length,
        tabs: document.querySelectorAll('.tab').length,
        details: document.querySelectorAll('details').length,
        iframe: document.querySelectorAll('iframe').length,
      },
      printLeftovers: {
        atPage: /@page/.test(document.documentElement.innerHTML),
        ptFonts: (document.documentElement.innerHTML.match(/font-size:\s*[\d.]+pt/g) || []).length,
        pageBreak: (document.documentElement.innerHTML.match(/break-(before|after|inside)/g) || []).length,
      },
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    };
  });

  note(`title       ${info.title}`);
  note(`images      ${info.images} (broken ${info.broken.length})`);
  note(`widgets     ` + JSON.stringify(info.widgets));
  note(`print leftovers ` + JSON.stringify(info.printLeftovers));
  note(`overflow@1280  ${info.overflow}px`);

  if (info.broken.length) fail.push('broken images: ' + info.broken.join(', '));
  if (info.overflow > 2) fail.push(`horizontal overflow ${info.overflow}px at 1280`);
  if (info.printLeftovers.atPage) fail.push('@page rule still present (print layout)');
  if (info.printLeftovers.ptFonts > 0) fail.push(`${info.printLeftovers.ptFonts} pt font sizes (print layout)`);

  for (const w of expect) {
    if (!info.widgets[w]) fail.push(`expected widget missing or empty: ${w}`);
  }

  // exercise what exists
  const acted = await page.evaluate(async () => {
    const out = {};
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const dots = document.querySelectorAll('.dot');
    if (dots.length > 1) {
      const before = document.querySelector('.step-img img')?.getAttribute('src');
      dots[Math.min(2, dots.length - 1)].click(); await sleep(150);
      out.stepperChanged = document.querySelector('.step-img img')?.getAttribute('src') !== before;
    }
    const bar = document.querySelector('.wipe .bar');
    if (bar) {
      const b0 = bar.style.left;
      dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight' })); await sleep(60);
      out.wipeKeyboard = bar.style.left !== b0;
    }
    const lg = document.querySelector('.lg');
    if (lg) { lg.click(); await sleep(60); out.legendToggles = lg.classList.contains('off'); }
    const chip = document.querySelector('.rchip');
    if (chip) {
      chip.dispatchEvent(new MouseEvent('mouseover', { bubbles: true })); await sleep(60);
      out.probeReadout = (document.querySelector('.readout')?.textContent || '').trim().length > 10;
    }
    const f = document.querySelectorAll('[data-f]');
    if (f.length > 1) {
      const n0 = document.querySelectorAll('tbody tr').length;
      f[1].click(); await sleep(80);
      out.filterChanges = document.querySelectorAll('tbody tr').length !== n0;
    }
    const tab = document.querySelectorAll('.tab');
    if (tab.length > 1) { tab[1].click(); await sleep(80); out.tabSwitches = tab[1].classList.contains('on'); }
    return out;
  });
  note(`interactions   ` + JSON.stringify(acted));
  for (const [k, v] of Object.entries(acted)) if (v === false) fail.push(`interaction dead: ${k}`);

  // mobile width
  await page.setViewport({ width: 390, height: 840 });
  await new Promise(r => setTimeout(r, 400));
  const mob = await page.evaluate(() =>
    document.documentElement.scrollWidth - document.documentElement.clientWidth);
  note(`overflow@390   ${mob}px`);
  if (mob > 2) fail.push(`horizontal overflow ${mob}px at 390 (mobile)`);

  // dark theme
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
  await new Promise(r => setTimeout(r, 200));
  const dark = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  note(`dark bg        ${dark}`);

  if (errors.length) fail.push(`${errors.length} console errors: ` + errors.slice(0, 3).join(' | '));

  await browser.close();
  if (fail.length) { console.log('\nFAIL'); fail.forEach(f => console.log('  x ' + f)); process.exit(1); }
  console.log('\nPASS');
})().catch(e => { console.error('verify crashed:', e.message); process.exit(1); });
