import {chromium} from '@playwright/test';
import * as fs from 'node:fs';
import * as path from 'node:path';

const TOKEN = fs.readFileSync(process.env.HOME + '/.tfactory/.token', 'utf8').trim();
const CHROME = process.env.CHROME_PATH;
const OUT = process.argv[2]; // target webm path
const FRONT = 'http://localhost:3100';

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
  // Linger on the dashboard.
  await settle(2500);
  // Try to surface TFactory tasks via the nav, best-effort.
  for (const re of [/tfactory/i, /tasks/i, /triage/i]) {
    const link = page.getByRole('link', {name: re}).first();
    if (await link.count().catch(() => 0)) {
      await link.click().catch(() => {});
      await settle(3000);
      break;
    }
  }
  // Gentle scroll to show content.
  for (let i = 0; i < 3; i++) { await page.mouse.wheel(0, 300); await settle(900); }
  await settle(2000);
} catch (e) {
  console.error('portal_rec warning:', e?.message || e);
}

await ctx.close(); // flush video
await browser.close();
const webm = fs.readdirSync(vdir).find((f) => f.endsWith('.webm'));
if (!webm) { console.error('no video produced'); process.exit(1); }
fs.copyFileSync(path.join(vdir, webm), OUT);
console.log('wrote', OUT);
