/**
 * WebMCP portal tool exposure (#333, epic #332) — EXPERIMENTAL, default-off.
 *
 * Registers TFactory's existing portal actions as WebMCP tools via
 * `navigator.modelContext.registerTool()`, so a browser-side agent (e.g. Claude
 * in Chrome) can drive TFactory from the open portal tab — no `acw_` token /
 * MCP-server setup. The in-browser analog of the remote-MCP control plane (#50)
 * + the WS2 ingest endpoint.
 *
 * Safety / non-breaking guarantees:
 *   - **Feature-detected**: no-op unless `navigator.modelContext` exists
 *     (only Chrome 146+ behind a flag today).
 *   - **Flag-gated**: no-op unless `VITE_WEBMCP_TOOLS === 'true'` (default off).
 *   - Tools wrap the existing typed `tfactory-api` client — no new transport,
 *     no backend change. Read-only tools are annotated `readOnlyHint`.
 *
 * WebMCP is a W3C Community-Group draft (not a formal standard) — see
 * guides/webmcp-testing.md. The `navigator.modelContext` shape is typed locally
 * because it isn't yet in the TS DOM lib.
 */

import {
  dismissRun,
  getTaskDetail,
  getTriageReportJson,
  ingestSpec,
  listTasks,
  mergeAcceptedTests,
  type SpecIngestRequest,
} from './tfactory-api';

// ─── Minimal local typing for the not-yet-in-lib WebMCP API ───────────────

interface ModelContextTool {
  name: string;
  description: string;
  inputSchema?: Record<string, unknown>;
  execute: (input: Record<string, unknown>, client?: unknown) => Promise<unknown>;
  annotations?: { readOnlyHint?: boolean };
}

interface ModelContext {
  registerTool: (tool: ModelContextTool) => void;
}

function getModelContext(): ModelContext | null {
  if (typeof navigator === 'undefined') return null;
  const mc = (navigator as Navigator & { modelContext?: ModelContext }).modelContext;
  return mc && typeof mc.registerTool === 'function' ? mc : null;
}

function flagEnabled(): boolean {
  // Default off. Opt in by building with VITE_WEBMCP_TOOLS=true.
  return (import.meta.env?.VITE_WEBMCP_TOOLS as string | undefined) === 'true';
}

/** True only when both the flag is on and the browser supports WebMCP. */
export function webmcpAvailable(): boolean {
  return flagEnabled() && getModelContext() !== null;
}

// ─── Tool definitions (wrap the existing tfactory-api client) ──────────────

function buildTools(): ModelContextTool[] {
  return [
    {
      name: 'tfactory_ingest_spec',
      description:
        'Create a TFactory test-generation task from a raw acceptance-criteria spec ' +
        '(markdown / Gherkin / EARS). Runs the native pipeline; no AIFactory branch needed.',
      inputSchema: {
        type: 'object',
        properties: {
          project_id: { type: 'string' },
          spec_id: { type: 'string' },
          spec_text: { type: 'string' },
          format: { type: 'string', enum: ['markdown', 'gherkin', 'ears'] },
          target_paths: { type: 'array', items: { type: 'string' } },
        },
        required: ['project_id', 'spec_id', 'spec_text'],
      },
      execute: async (input) => ingestSpec(input as unknown as SpecIngestRequest),
    },
    {
      name: 'tfactory_list_tasks',
      description: 'List TFactory test-generation tasks (newest first).',
      inputSchema: { type: 'object', properties: {} },
      annotations: { readOnlyHint: true },
      execute: async () => listTasks(),
    },
    {
      name: 'tfactory_get_task',
      description: "Get a TFactory task's status + artefact metadata by spec_id.",
      inputSchema: {
        type: 'object',
        properties: { spec_id: { type: 'string' } },
        required: ['spec_id'],
      },
      annotations: { readOnlyHint: true },
      execute: async (input) => getTaskDetail(String(input.spec_id)),
    },
    {
      name: 'tfactory_get_triage_report',
      description: "Get a TFactory task's triage report (verdicts + ranking) by spec_id.",
      inputSchema: {
        type: 'object',
        properties: { spec_id: { type: 'string' } },
        required: ['spec_id'],
      },
      annotations: { readOnlyHint: true },
      execute: async (input) => getTriageReportJson(String(input.spec_id)),
    },
    {
      name: 'tfactory_merge_tests',
      description:
        'Commit a task\'s accepted tests to its branch. Dry-run by default; pass ' +
        'dry_run:false to actually commit (the human review gate).',
      inputSchema: {
        type: 'object',
        properties: {
          spec_id: { type: 'string' },
          dry_run: { type: 'boolean' },
        },
        required: ['spec_id'],
      },
      execute: async (input) =>
        mergeAcceptedTests(String(input.spec_id), {
          dry_run: input.dry_run === undefined ? true : Boolean(input.dry_run),
        }),
    },
    {
      name: 'tfactory_dismiss_run',
      description: 'Mark a TFactory task run dismissed by spec_id.',
      inputSchema: {
        type: 'object',
        properties: { spec_id: { type: 'string' } },
        required: ['spec_id'],
      },
      execute: async (input) => dismissRun(String(input.spec_id)),
    },
  ];
}

/**
 * Register the TFactory portal tools with the browser's WebMCP runtime.
 * No-op (returns 0) unless the flag is on AND the browser supports WebMCP.
 * Returns the number of tools registered.
 */
export function registerWebmcpTools(): number {
  if (!flagEnabled()) return 0;
  const mc = getModelContext();
  if (!mc) return 0;

  const tools = buildTools();
  for (const tool of tools) {
    mc.registerTool(tool);
  }
  return tools.length;
}
