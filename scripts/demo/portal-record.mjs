import {chromium} from '@playwright/test';
import * as fs from 'node:fs';
import * as path from 'node:path';

const TOKEN = fs.readFileSync(process.env.HOME + '/.tfactory/.token', 'utf8').trim();
const CHROME = process.env.CHROME_PATH;
const OUT = process.argv[2]; // target webm path
const FRONT = process.env.FRONT_URL || 'http://localhost:3100';
const SPEC = process.env.SPEC || ''; // when set, drill into this task + walk its tabs

const vdir = fs.mkdtempSync('/tmp/portalrec-');
const browser = await chromium.launch({headless: true, executablePath: CHROME});
const ctx = await browser.newContext({
  viewport: {width: 1366, height: 850},
  recordVideo: {dir: vdir, size: {width: 1366, height: 850}},
});
const page = await ctx.newPage();

// Pre-auth: inject the token so the SPA skips the login wall.
await page.addInitScript((t) => {
  try { localStorage.setItem('tfactory-token', t); } catch {}
}, TOKEN);

async function settle(ms) { await page.waitForTimeout(ms); }

try {
  await page.goto(FRONT + '/', {waitUntil: 'domcontentloaded'});
  await settle(2500);
  // If still on the login screen, fill the token field + login.
  const tokenInput = page.locator('input[placeholder="Enter your token"]');
  if (await tokenInput.count()) {
    await tokenInput.fill(TOKEN);
    await settle(400);
    const loginBtn = page.getByRole('button', {name: /login/i});
    if (await loginBtn.count()) { await loginBtn.click(); }
    await settle(3000);
  }
  await settle(1500);
  // Surface the TFactory Tests/Tasks list.
  for (const re of [/tfactory/i, /tests/i, /tasks/i, /triage/i]) {
    const link = page.getByRole('link', {name: re}).first();
    if (await link.count().catch(() => 0)) {
      await link.click().catch(() => {});
      await settle(2200);
      break;
    }
  }
  if (SPEC) {
    // Drill into THIS scenario's task so the pane shows its own lanes +
    // verdicts — not the generic list (that's what made every demo look alike).
    const row = page.getByText(SPEC, {exact: false}).first();
    if (await row.count().catch(() => 0)) {
      await row.click().catch(() => {});
      await settle(2200);
    }
    // Walk the task's tabs to showcase this scenario's data.
    for (const tab of ['Status', 'Lanes', 'Verdicts', 'Report']) {
      const t = page.getByText(tab, {exact: false}).first();
      if (await t.count().catch(() => 0)) {
        await t.click().catch(() => {});
        await settle(1700);
        if (tab === 'Verdicts') {
          // Scroll through the 5-signal verdict cards, then back to the top.
          for (let i = 0; i < 3; i++) { await page.mouse.wheel(0, 320); await settle(750); }
          await page.mouse.wheel(0, -960); await settle(500);
        }
      }
    }
  } else {
    for (let i = 0; i < 3; i++) { await page.mouse.wheel(0, 300); await settle(900); }
  }
  await settle(1500);
} catch (e) {
  console.error('portal_rec warning:', e?.message || e);
}

await ctx.close(); // flush video
await browser.close();
const webm = fs.readdirSync(vdir).find((f) => f.endsWith('.webm'));
if (!webm) { console.error('no video produced'); process.exit(1); }
fs.copyFileSync(path.join(vdir, webm), OUT);
console.log('wrote', OUT);
