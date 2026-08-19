// Run with: npm run test:scripts
//
// Covers the step 7 staging list in scripts/bump-version.js (#1045).
//
// updateBackendInit() and updatePackageJson() both skip files that are absent
// and say so. Step 7 then staged all three by name regardless, and git exits
// non-zero on a pathspec that matches nothing -- so the two "skipping" paths
// could not complete a bump, and left the frontend package.json rewritten and
// uncommitted. These tests drive the real script against throwaway
// repositories rather than unit-testing the list, because the failure was in
// the interaction with git and nowhere else.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const SCRIPTS = path.dirname(fileURLToPath(import.meta.url));

function git(cwd, ...args) {
  return execFileSync('git', args, { cwd, encoding: 'utf8' }).trim();
}

/** A minimal repo carrying only the files named, plus a CHANGELOG for 1.2.4. */
function fixture({ rootPackageJson = false, backendInit = false } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'bumpver-'));
  fs.mkdirSync(path.join(dir, 'apps', 'frontend-web'), { recursive: true });
  fs.mkdirSync(path.join(dir, 'scripts'), { recursive: true });
  fs.writeFileSync(
    path.join(dir, 'apps', 'frontend-web', 'package.json'),
    JSON.stringify({ name: 'x', version: '1.2.3' }, null, 2) + '\n',
  );
  for (const f of ['bump-version.js', 'validate-release.js', 'argv-safety.cjs']) {
    const src = path.join(SCRIPTS, f);
    if (fs.existsSync(src)) fs.copyFileSync(src, path.join(dir, 'scripts', f));
  }
  fs.writeFileSync(path.join(dir, 'CHANGELOG.md'), '# Changelog\n\n## [1.2.4] - 2026-08-19\n- x\n');
  if (rootPackageJson) {
    fs.writeFileSync(
      path.join(dir, 'package.json'),
      JSON.stringify({ name: 'root', version: '1.2.3' }, null, 2) + '\n',
    );
  }
  if (backendInit) {
    fs.mkdirSync(path.join(dir, 'apps', 'backend'), { recursive: true });
    fs.writeFileSync(path.join(dir, 'apps', 'backend', '__init__.py'), '__version__ = "1.2.3"\n');
  }
  git(dir, 'init', '-q');
  git(dir, 'config', 'user.email', 'test@example.com');
  git(dir, 'config', 'user.name', 'test');
  git(dir, 'add', '-A');
  git(dir, 'commit', '-qm', 'init');
  return dir;
}

function bump(dir) {
  execFileSync('node', [path.join(dir, 'scripts', 'bump-version.js'), 'patch'], {
    cwd: dir,
    encoding: 'utf8',
    stdio: 'pipe',
  });
}

test('completes when the optional files are absent, and stages only what it wrote', () => {
  const dir = fixture();
  bump(dir);

  // The bug: this threw, because `git add package.json` matched nothing.
  assert.equal(git(dir, 'status', '--porcelain'), '', 'working tree left dirty');
  const staged = git(dir, 'show', '--stat', '--name-only', '--format=', 'HEAD').split('\n').filter(Boolean);
  assert.deepEqual(staged, ['apps/frontend-web/package.json']);
  const pkg = JSON.parse(fs.readFileSync(path.join(dir, 'apps', 'frontend-web', 'package.json'), 'utf8'));
  assert.equal(pkg.version, '1.2.4');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('still stages all three when all three exist', () => {
  const dir = fixture({ rootPackageJson: true, backendInit: true });
  bump(dir);

  assert.equal(git(dir, 'status', '--porcelain'), '', 'working tree left dirty');
  const staged = git(dir, 'show', '--stat', '--name-only', '--format=', 'HEAD').split('\n').filter(Boolean).sort();
  assert.deepEqual(staged, ['apps/backend/__init__.py', 'apps/frontend-web/package.json', 'package.json']);
  assert.match(
    fs.readFileSync(path.join(dir, 'apps', 'backend', '__init__.py'), 'utf8'),
    /__version__ = "1\.2\.4"/,
  );
  fs.rmSync(dir, { recursive: true, force: true });
});
