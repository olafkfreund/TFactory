/**
 * @vitest-environment jsdom
 *
 * Verifies #326: the GitHub issue → task flow creates a NATIVE TFactory
 * test-generation task via `ingestSpec` (POST /api/specs/ingest), and no longer
 * calls the inherited AIFactory `createTask` (POST /api/projects/{id}/tasks).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as tfactoryApi from '../../lib/tfactory-api';
import { investigateGitHubIssue, useInvestigationStore } from './investigation-store';

vi.mock('../../lib/tfactory-api', () => ({ ingestSpec: vi.fn() }));

describe('investigateGitHubIssue → native TFactory task (#326)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (tfactoryApi.ingestSpec as ReturnType<typeof vi.fn>).mockResolvedValue({
      task_id: 'gh-issue-5-123',
      project_id: 'p1',
      spec_dir: '/x',
      source_format: 'markdown',
      ac_count: 1,
      planner_scheduled: true,
      warnings: [],
    });
    // Stub window.API: a successful investigation + a createTask spy that must NOT fire.
    (window as unknown as { API: unknown }).API = {
      createTask: vi.fn(),
      investigateGitHubIssue: vi.fn().mockResolvedValue({
        success: true,
        data: {
          issue: { title: 'Broken login', body: 'login fails' },
          analysis: {
            summary: 'login bug',
            complexity: 'standard',
            issue_type: 'bug',
            affected_areas: ['src/auth.py'],
            suggestions: ['return 401 on bad creds'],
          },
        },
      }),
    };
  });

  it('calls ingestSpec with a native test-spec payload, not createTask', async () => {
    await investigateGitHubIssue('p1', 5);

    const api = (window as unknown as { API: { createTask: ReturnType<typeof vi.fn> } }).API;
    expect(api.createTask).not.toHaveBeenCalled();

    expect(tfactoryApi.ingestSpec).toHaveBeenCalledTimes(1);
    const arg = (tfactoryApi.ingestSpec as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(arg.project_id).toBe('p1');
    expect(arg.spec_id).toMatch(/^gh-issue-5-/);
    expect(arg.format).toBe('markdown');
    expect(arg.spec_text).toContain('# Broken login');       // title folded in as H1
    expect(arg.spec_text).toContain('GitHub Issue #5');        // description body
    expect(arg.target_paths).toEqual(['src/auth.py']);         // affected_areas → target_paths
  });

  it('surfaces the returned task_id on the investigation result', async () => {
    await investigateGitHubIssue('p1', 5);
    const res = useInvestigationStore.getState().lastInvestigationResult;
    expect(res?.success).toBe(true);
    expect(res?.taskId).toBe('gh-issue-5-123');
  });
});
