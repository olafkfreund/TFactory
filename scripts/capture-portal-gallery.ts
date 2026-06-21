/**
 * TFactory live-portal gallery capture.
 *
 * Drives the LIVE portal (TFACTORY_PORTAL_URL) via Playwright with a real
 * FORM LOGIN (localStorage pre-seed does NOT authenticate this app), then
 * navigates the SPA in-app (clicks, not URL routes) and captures a curated
 * gallery of screenshots into docs/static/img/gallery/.
 *
 * Auth recipe:
 *   - mint a fresh token:
 *       kubectl get secret factory-secrets -n factory \
 *         -o jsonpath='{.data.APP_API_TOKEN}' | base64 -d > ~/.tfactory/.token
 *   - navigate /login, fill #token, click the submit button, wait for SPA.
 *
 * Run:
 *   TFACTORY_PORTAL_URL=https://tfactory.freundcloud.org.uk \
 *   PLAYWRIGHT_CHROMIUM_EXECUTABLE=/etc/profiles/per-user/olafkfreund/bin/google-chrome-stable \
 *   node_modules/.bin/tsx scripts/capture-portal-gallery.ts
 *
 * Tolerant by design: a missing view logs a warning and continues.
 */

import {chromium, type Browser, type Page} from '@playwright/test';
import * as path from 'node:path';
import * as fs from 'node:fs';

const PORTAL_URL = (
  process.env.TFACTORY_PORTAL_URL ?? 'https://tfactory.freundcloud.org.uk'
).replace(/\/$/, '');
const TOKEN_FILE = path.join(
  process.env.HOME ?? '/root',
  '.tfactory',
  '.token'
);
const EXEC = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined;
const OUT_DIR = path.resolve(__dirname, '..', 'docs', 'static', 'img', 'gallery');

function loadToken(): string {
  if (!fs.existsSync(TOKEN_FILE)) {
    throw new Error(`Token not found at ${TOKEN_FILE}.`);
  }
  return fs.readFileSync(TOKEN_FILE, 'utf-8').trim();
}

const captured: string[] = [];

async function shoot(page: Page, name: string): Promise<void> {
  const outPath = path.join(OUT_DIR, name);
  await page.screenshot({path: outPath, fullPage: false});
  captured.push(name);
  console.log(`  captured ${name}`);
}

async function step(name: string, fn: () => Promise<void>): Promise<void> {
  try {
    await fn();
  } catch (e) {
    console.warn(`  WARN ${name}: ${(e as Error).message}`);
  }
}

async function settle(page: Page, ms = 900): Promise<void> {
  try {
    await page.waitForLoadState('networkidle', {timeout: 4000});
  } catch {
    /* tolerate */
  }
  await page.waitForTimeout(ms);
}

async function clickByText(page: Page, re: RegExp): Promise<boolean> {
  const candidates = [
    page.getByRole('button', {name: re}),
    page.getByRole('link', {name: re}),
    page.getByRole('tab', {name: re}),
    page.getByText(re),
  ];
  for (const loc of candidates) {
    const first = loc.first();
    try {
      if (await first.isVisible({timeout: 1200})) {
        await first.click();
        return true;
      }
    } catch {
      /* next */
    }
  }
  return false;
}

async function login(page: Page, token: string): Promise<boolean> {
  // The backend owns /login (returns JSON 404); the SPA serves the login
  // form at the root, so go there and do a real FORM login.
  await page.goto(PORTAL_URL, {waitUntil: 'domcontentloaded'});
  await settle(page, 800);
  const input = page.locator('#token');
  if (!(await input.isVisible({timeout: 5000}).catch(() => false))) {
    // No token field — likely already authenticated.
    console.warn('  WARN login: #token not visible, assuming already authed');
    return true;
  }
  // Capture the front door before we authenticate.
  await shoot(page, '01-login.png');
  await input.fill(token);
  const submitted =
    (await clickByText(page, /^continue$|log\s*in|sign\s*in/i)) ||
    (await (async () => {
      await input.press('Enter');
      return true;
    })());
  // The form validates against /api/health then renders the app in-place
  // (no reload). Wait for the login card to disappear. We must NOT reload
  // afterwards: /api/auth/me returns 401 for API-tokens, so a reload bounces
  // back to login. All subsequent navigation is in-SPA only.
  await settle(page, 2000);
  const tokenGone = !(await page
    .locator('#token')
    .isVisible({timeout: 1500})
    .catch(() => false));
  if (!tokenGone) {
    console.warn('  WARN login: token field still visible after submit');
    return false;
  }
  console.log(`  login OK (submitted=${submitted}), now at ${page.url()}`);
  return true;
}

async function main(): Promise<void> {
  fs.mkdirSync(OUT_DIR, {recursive: true});
  const token = loadToken();

  const browser: Browser = await chromium.launch({
    headless: true,
    executablePath: EXEC,
  });
  const context = await browser.newContext({
    viewport: {width: 1440, height: 900},
    deviceScaleFactor: 2,
  });
  const page = await context.newPage();

  console.log(`Capturing TFactory gallery from ${PORTAL_URL} -> ${OUT_DIR}`);

  const authed = await login(page, token);
  if (!authed) {
    console.error('Login failed — capturing what we can and exiting.');
    await browser.close();
    return;
  }

  // Helper: return to the home/project list WITHOUT reloading (a reload
  // hits /api/auth/me -> 401 -> bounce to login). Click the brand/home/back.
  const goHome = async (): Promise<void> => {
    await page.keyboard.press('Escape').catch(() => {});
    await page.waitForTimeout(300);
    const ok =
      (await clickByText(page, /^home$/i)) ||
      (await (async () => {
        const brand = page
          .locator('a[href="/"], a[href="#/"], [data-testid="brand"], .brand')
          .first();
        if (await brand.isVisible({timeout: 1000}).catch(() => false)) {
          await brand.click();
          return true;
        }
        return false;
      })()) ||
      (await clickByText(page, /projects|back|all tasks/i));
    if (!ok) {
      // Last resort: browser back.
      await page.goBack().catch(() => {});
    }
    await settle(page, 800);
  };

  // ── Home / project list ──────────────────────────────────────────
  await step('home', async () => {
    await settle(page);
    await shoot(page, '02-home-projects.png');
  });

  // ── Open first project → pipeline board ──────────────────────────
  await step('open-project', async () => {
    const card = page
      .locator(
        '[data-testid^="project-card-"], [data-testid^="task-row-"], a[href*="project"], .project-card'
      )
      .first();
    if (await card.isVisible({timeout: 3000}).catch(() => false)) {
      await card.click();
      await settle(page);
    } else {
      await clickByText(page, /aifactory-demo|demo|project/i);
      await settle(page);
    }
    await shoot(page, '03-pipeline-board.png');
  });

  // ── Lane status grid (if present on the board) ───────────────────
  await step('lanes-overview', async () => {
    const lanes = page.locator('[data-testid="lane-status-grid"]').first();
    if (await lanes.isVisible({timeout: 2000}).catch(() => false)) {
      await lanes.scrollIntoViewIfNeeded();
      await page.waitForTimeout(500);
      await shoot(page, '04-lane-status-grid.png');
    }
  });

  // ── Open a task → cycle the verification tabs ────────────────────
  const tabSpecs: Array<[string, string]> = [
    ['status', '06-task-status.png'],
    ['lanes', '07-task-lanes.png'],
    ['verdicts', '08-task-verdicts.png'],
    ['report', '09-task-report.png'],
    ['acceptance', '10-task-acceptance.png'],
    ['logs', '11-task-logs.png'],
    ['evidence', '12-task-evidence.png'],
  ];

  // Open a task by a name fragment from the board (falls back to first card).
  const openTaskByName = async (re: RegExp): Promise<boolean> => {
    await goHome();
    const named = page.getByText(re).first();
    if (await named.isVisible({timeout: 2500}).catch(() => false)) {
      await named.click();
      await settle(page);
      return true;
    }
    const row = page
      .locator('[data-testid^="task-row-"], [data-testid^="task-card-"], .task-card')
      .first();
    if (await row.isVisible({timeout: 2500}).catch(() => false)) {
      await row.click();
      await settle(page);
      return true;
    }
    return false;
  };

  await step('open-task', async () => {
    // Prefer a completed/triaged task so the verdicts/report/acceptance/
    // evidence tabs are enabled (the auto-opened one may still be planning).
    const opened =
      (await openTaskByName(/bench-go-hello/i)) ||
      (await openTaskByName(/triaged|complete/i)) ||
      (await openTaskByName(/./));
    if (opened) {
      await shoot(page, '05-task-detail.png');
    } else {
      console.warn('  WARN open-task: no task card found');
    }
  });

  for (const [tab, file] of tabSpecs) {
    await step(`tab-${tab}`, async () => {
      const t = page.locator(`[data-testid="tab-${tab}"]`).first();
      if (await t.isVisible({timeout: 1500}).catch(() => false)) {
        const disabled = await t.getAttribute('disabled');
        if (disabled !== null) {
          console.warn(`  WARN tab-${tab}: disabled, skipping`);
          return;
        }
        await t.click();
        await settle(page, 1200);
        await shoot(page, file);
      } else {
        console.warn(`  WARN tab-${tab}: not visible`);
      }
    });
  }

  // Close any open modal before continuing.
  await step('close-modal', async () => {
    await page.keyboard.press('Escape');
    await page.waitForTimeout(400);
  });

  // ── A failed task — capture the FAILED verdict / status ──────────
  await step('failed-task', async () => {
    const opened =
      (await openTaskByName(/verify-smoke|smoke/i)) ||
      (await openTaskByName(/failed/i));
    if (!opened) {
      console.warn('  WARN failed-task: not found');
      return;
    }
    // Capture status (shows failed) — verdicts if available, else status.
    const v = page.locator('[data-testid="tab-verdicts"]').first();
    if (
      (await v.isVisible({timeout: 1500}).catch(() => false)) &&
      (await v.getAttribute('disabled')) === null
    ) {
      await v.click();
      await settle(page, 1000);
    }
    await shoot(page, '13-failed-task.png');
    await page.keyboard.press('Escape');
  });

  // ── Sidebar nav pages (Files, MCP, Test Plans, Visual Reports,
  //    GitHub PRs, Changelog) — only present inside a project ───────
  await step('sidebar-nav', async () => {
    await openTaskByName(/bench-go-hello/i); // ensure inside a project shell
    await page.keyboard.press('Escape');
    await settle(page, 600);
    const navShots: Array<[RegExp, string]> = [
      [/^Files$/i, '18-files.png'],
      [/^MCP$/i, '19-mcp.png'],
      [/^Test Plans$/i, '20-test-plans.png'],
      [/^Visual Reports$/i, '21-visual-reports.png'],
      [/^GitHub PRs$/i, '22-github-prs.png'],
    ];
    for (const [re, file] of navShots) {
      await step(`nav-${file}`, async () => {
        if (await clickByText(page, re)) {
          await settle(page, 1100);
          await shoot(page, file);
        }
      });
    }
  });

  // ── Settings ─────────────────────────────────────────────────────
  await step('settings', async () => {
    await goHome();
    if (await clickByText(page, /settings/i)) {
      await settle(page, 1000);
      await shoot(page, '14-settings.png');
      // A settings sub-tab (providers/credentials) if present.
      if (await clickByText(page, /credential|provider|llm|test cred/i)) {
        await settle(page, 800);
        await shoot(page, '15-settings-credentials.png');
      }
      await page.keyboard.press('Escape');
    }
  });

  // ── Create / new task dialog (the "+ Task" button on the board) ──
  await step('create-task', async () => {
    await goHome();
    if (await clickByText(page, /^\+?\s*task$|new task|create task|ingest/i)) {
      await settle(page, 1000);
      await shoot(page, '16-create-task.png');
      await page.keyboard.press('Escape');
    }
  });

  // ── Insights / chat (if present) ─────────────────────────────────
  await step('insights', async () => {
    if (await clickByText(page, /insights|chat/i)) {
      await settle(page, 1000);
      await shoot(page, '17-insights.png');
    }
  });

  await browser.close();
  console.log(`\nDone. ${captured.length} screenshots:`);
  for (const c of captured) console.log(`  - ${c}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
