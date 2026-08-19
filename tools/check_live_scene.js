// Confirm the embedded three.js scene actually renders from GitHub Pages.
//
// This is the most fragile claim in the whole site: the archived source imports
// three.js from a CDN, and it has to survive Pages' headers, the module graph and
// a WebGL context. Verify it rather than trusting the build log.
//
//   node tools/check_live_scene.js <url>

const pc = require('puppeteer-core');
const fs = require('fs');

const CHROME = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
].find(p => { try { return fs.existsSync(p); } catch (e) { return false; } });

const url = process.argv[2];
if (!url) { console.error('usage: check_live_scene.js <url>'); process.exit(2); }

(async () => {
  const browser = await pc.launch({
    executablePath: CHROME, headless: 'new',
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--enable-unsafe-swiftshader'],
  });
  const page = await browser.newPage();
  const errs = [], cdn = [];
  page.on('pageerror', e => errs.push(String(e.message).slice(0, 120)));
  // the browser fetches /favicon.ico on its own; a 404 there is not a page defect
  page.on('console', m => {
    if (m.type() !== 'error') return;
    const t = String(m.text()), u = (m.location() && m.location().url) || '';
    if (/favicon\.ico/i.test(u) || /favicon\.ico/i.test(t)) return;
    if (/Failed to load resource/i.test(t)) return;
    errs.push(t.slice(0, 120));
  });
  page.on('response', r => {
    if (r.status() >= 400 && !/favicon\.ico/i.test(r.url()))
      errs.push('HTTP ' + r.status() + ' ' + r.url().slice(0, 90));
  });
  page.on('response', r => {
    const u = r.url();
    if (/jsdelivr|unpkg|cdnjs/i.test(u)) cdn.push(r.status() + ' ' + u.split('/').slice(-2).join('/'));
  });

  await page.setViewport({ width: 1280, height: 800 });
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 120000 });
  await new Promise(r => setTimeout(r, 30000));   // let the scene build

  const canvas = await page.evaluate(() => {
    const c = document.querySelector('canvas');
    return c ? (c.width + 'x' + c.height) : 'none';
  });
  // measure brightness from a real screenshot instead of readPixels: a WebGL
  // context with preserveDrawingBuffer=false reads back empty
  const shot = await page.screenshot({ encoding: 'binary' });
  let mean = 0;
  for (let i = 1000; i < shot.length; i += 997) mean += shot[i];
  mean = (mean / Math.floor((shot.length - 1000) / 997)).toFixed(1);

  const stats = await page.evaluate(() => {
    try {
      if (window.__fob && typeof window.__fob.info === 'function') {
        const i = window.__fob.info();
        return Object.keys(i).map(k => k + '=' + i[k]).join(' ');
      }
    } catch (e) { return 'info() threw: ' + e.message; }
    return 'no __fob.info';
  });

  console.log('  CDN 请求      ', cdn.length ? cdn.slice(0, 2).join(' | ') : '(none)');
  console.log('  canvas        ', canvas);
  console.log('  截图字节均值  ', mean, '(≈0 表示全黑)');
  console.log('  场景自报      ', stats);
  console.log('  页面错误      ', errs.length ? errs.slice(0, 2).join(' | ') : '无');
  await browser.close();

  const ok = canvas !== 'none' && Number(mean) > 5 && errs.length === 0;
  console.log(ok ? '\n场景在线上确实渲染出来了' : '\n未通过');
  process.exit(ok ? 0 : 1);
})().catch(e => { console.error('crashed:', e.message); process.exit(1); });
