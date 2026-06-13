/**
 * @vitest-environment jsdom
 *
 * Tests for the WebMCP portal tool exposure (#333). Verifies the safety
 * guarantees (no-op when flag-off or API-absent), that all portal tools
 * register when enabled, and that a tool delegates to the tfactory-api client.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from './tfactory-api';
import { registerWebmcpTools, webmcpAvailable } from './webmcp';

vi.mock('./tfactory-api', () => ({
  ingestSpec: vi.fn(),
  listTasks: vi.fn().mockResolvedValue({ tasks: [], count: 0 }),
  getTaskDetail: vi.fn(),
  getTriageReportJson: vi.fn(),
  mergeAcceptedTests: vi.fn(),
  dismissRun: vi.fn(),
}));

type RegisterToolFn = ReturnType<typeof vi.fn>;

function installModelContext(): RegisterToolFn {
  const registerTool = vi.fn();
  (navigator as unknown as { modelContext?: unknown }).modelContext = { registerTool };
  return registerTool;
}

function removeModelContext() {
  delete (navigator as unknown as { modelContext?: unknown }).modelContext;
}

beforeEach(() => {
  vi.clearAllMocks();
  removeModelContext();
});

afterEach(() => {
  vi.unstubAllEnvs();
  removeModelContext();
});

describe('registerWebmcpTools — safety guards', () => {
  it('is a no-op when the flag is off (default), even if the API exists', () => {
    vi.stubEnv('VITE_WEBMCP_TOOLS', '');
    const registerTool = installModelContext();
    expect(registerWebmcpTools()).toBe(0);
    expect(registerTool).not.toHaveBeenCalled();
    expect(webmcpAvailable()).toBe(false);
  });

  it('is a no-op when navigator.modelContext is absent, even with the flag on', () => {
    vi.stubEnv('VITE_WEBMCP_TOOLS', 'true');
    removeModelContext();
    expect(registerWebmcpTools()).toBe(0);
    expect(webmcpAvailable()).toBe(false);
  });
});

describe('registerWebmcpTools — enabled', () => {
  it('registers all portal tools when flag-on + API present', () => {
    vi.stubEnv('VITE_WEBMCP_TOOLS', 'true');
    const registerTool = installModelContext();

    const n = registerWebmcpTools();
    expect(n).toBe(6);
    expect(registerTool).toHaveBeenCalledTimes(6);
    expect(webmcpAvailable()).toBe(true);

    const names = registerTool.mock.calls.map((c) => c[0].name);
    expect(names).toEqual(
      expect.arrayContaining([
        'tfactory_ingest_spec',
        'tfactory_list_tasks',
        'tfactory_get_task',
        'tfactory_get_triage_report',
        'tfactory_merge_tests',
        'tfactory_dismiss_run',
      ]),
    );
    // read-only tools are annotated
    const listTasksTool = registerTool.mock.calls.map((c) => c[0]).find((t) => t.name === 'tfactory_list_tasks');
    expect(listTasksTool.annotations?.readOnlyHint).toBe(true);
  });

  it('a registered tool delegates to the tfactory-api client', async () => {
    vi.stubEnv('VITE_WEBMCP_TOOLS', 'true');
    const registerTool = installModelContext();
    registerWebmcpTools();

    const tools = registerTool.mock.calls.map((c) => c[0]);
    const listTasksTool = tools.find((t) => t.name === 'tfactory_list_tasks');
    const result = await listTasksTool.execute({});
    expect(api.listTasks).toHaveBeenCalledTimes(1);
    expect(result).toEqual({ tasks: [], count: 0 });

    const mergeTool = tools.find((t) => t.name === 'tfactory_merge_tests');
    await mergeTool.execute({ spec_id: 's1' });
    expect(api.mergeAcceptedTests).toHaveBeenCalledWith('s1', { dry_run: true }); // dry-run default
  });
});
