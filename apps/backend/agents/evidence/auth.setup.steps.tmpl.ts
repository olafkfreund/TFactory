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
import crypto from "node:crypto";

const STORAGE_STATE = "@@STORAGE_STATE_PATH@@";

// RFC-6238 TOTP (SHA-1, 30s, 6 digits) for Class B MFA. The base32 seed arrives
// as an env var (from the encrypted vault); the code is generated HERE, at the
// moment of fill, so it never expires in flight. This is the same computation an
// authenticator app does — generation, not a bypass.
function __tfTotp(
  secret: string,
  opts: { digits?: number; alg?: string; period?: number } = {},
): string {
  const digits = opts.digits ?? 6;
  const alg = (opts.alg ?? "sha1").toLowerCase();
  const period = opts.period ?? 30;
  const base32 = (secret || "").replace(/=+$/g, "").replace(/\s/g, "").toUpperCase();
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const ch of base32) {
    const v = alphabet.indexOf(ch);
    if (v < 0) continue;
    bits += v.toString(2).padStart(5, "0");
  }
  const bytes: number[] = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(parseInt(bits.slice(i, i + 8), 2));
  const key = Buffer.from(bytes);
  const counter = Math.floor(Date.now() / 1000 / period);
  const buf = Buffer.alloc(8);
  buf.writeBigInt64BE(BigInt(counter));
  const hmac = crypto.createHmac(alg, key).update(buf).digest();
  const offset = hmac[hmac.length - 1] & 0xf;
  const bin =
    ((hmac[offset] & 0x7f) << 24) |
    ((hmac[offset + 1] & 0xff) << 16) |
    ((hmac[offset + 2] & 0xff) << 8) |
    (hmac[offset + 3] & 0xff);
  const code = bin % 10 ** digits;
  return code.toString().padStart(digits, "0");
}

setup("authenticate", async ({ page }) => {
@@LOGIN_STEPS@@

  await page.context().storageState({ path: STORAGE_STATE });
  expect(STORAGE_STATE.length).toBeGreaterThan(0);
});
