/**
 * RFC-6238 TOTP generation for Class B MFA login steps (#1063).
 *
 * Extracted out of ``auth.setup.steps.tmpl.ts`` (a marker-substitution string
 * template that CodeQL cannot parse and is excluded from JS analysis) so this
 * genuine security-sensitive logic stays inside CodeQL's scanned set. This
 * file has no template placeholders and is plain, valid TypeScript.
 *
 * ``agents.evidence.layout.render_auth_setup_steps`` copies this file
 * alongside the rendered ``auth.setup.ts`` into the generated Playwright test
 * workspace (same directory, so the relative import below resolves wherever
 * Playwright executes the generated file — see ``scaffold_auth_setup``).
 *
 * The base32 seed arrives via an env var (from the encrypted vault); the code
 * is generated HERE, at the moment of fill, so it never expires in flight.
 * This is the same computation an authenticator app does — generation, not a
 * bypass.
 */

import crypto from "node:crypto";

export function __tfTotp(
  secret: string,
  opts: { digits?: number; alg?: string; period?: number; now?: number } = {},
): string {
  const digits = opts.digits ?? 6;
  const alg = (opts.alg ?? "sha1").toLowerCase();
  const period = opts.period ?? 30;
  const now = opts.now ?? Date.now();
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
  const counter = Math.floor(now / 1000 / period);
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
