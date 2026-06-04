/**
 * TFactory Playwright auth setup template — multi-step / SSO login (#107).
 *
 * Rendered by ``agents.evidence.layout.render_auth_setup_steps`` when a target's
 * ``.tfactory.yml`` ref-auth block declares an ordered ``steps`` list (for SSO /
 * IdP-redirect / multi-step logins). It runs the declared steps ONCE and saves
 * the authenticated session to ``@@STORAGE_STATE_PATH@@``; tests that declare
 * ``dependencies: ["setup"]`` + ``storageState`` then reuse that session.
 *
 * Credentials never appear in this file: ``fill_username`` / ``fill_secret``
 * steps read the username / secret from the injected env vars at run time, which
 * the Executor populates only on egress lanes.
 *
 * Substitution variables (replaced at render time) — the LOGIN_STEPS marker
 * (the ordered login actions) and the STORAGE_STATE_PATH marker (where to write
 * the saved session).
 */

import { test as setup, expect } from "@playwright/test";

const STORAGE_STATE = "@@STORAGE_STATE_PATH@@";

setup("authenticate", async ({ page }) => {
@@LOGIN_STEPS@@

  await page.context().storageState({ path: STORAGE_STATE });
  expect(STORAGE_STATE.length).toBeGreaterThan(0);
});
