/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { DEFAULT_PROJECT_SETTINGS } from '../shared/constants/config';
import { updateProjectSettings, updateProjectSettingsResult, useProjectStore } from './project-store';

describe('updateProjectSettingsResult', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    useProjectStore.getState().setProjects([]);
  });

  it('returns the server detail and makes the boolean wrapper return false', async () => {
    const error = 'Only project admins can update settings';
    vi.stubGlobal('API', {
      updateProjectSettings: vi.fn().mockResolvedValue({ success: false, error })
    });

    expect(await updateProjectSettingsResult('project', {})).toEqual({ ok: false, error });
    expect(await updateProjectSettings('project', {})).toBe(false);
  });

  it('merges saved settings through both entry points', async () => {
    vi.stubGlobal('API', {
      updateProjectSettings: vi.fn().mockResolvedValue({ success: true })
    });
    useProjectStore.getState().setProjects([{
      id: 'project',
      name: 'Project',
      path: '/project',
      autoBuildPath: '/project/.tfactory',
      settings: DEFAULT_PROJECT_SETTINGS,
      createdAt: new Date(0),
      updatedAt: new Date(0)
    }]);

    expect(await updateProjectSettingsResult('project', { mainBranch: 'main' })).toEqual({ ok: true });
    expect(await updateProjectSettings('project', { graphitiMcpEnabled: false })).toBe(true);
    expect(useProjectStore.getState().projects[0].settings).toEqual({
      ...DEFAULT_PROJECT_SETTINGS,
      mainBranch: 'main',
      graphitiMcpEnabled: false
    });
  });

  it('returns the message when the API throws', async () => {
    const error = new Error('Save interrupted');
    vi.stubGlobal('API', {
      updateProjectSettings: vi.fn().mockRejectedValue(error)
    });

    expect(await updateProjectSettingsResult('project', {})).toEqual({ ok: false, error: error.message });
  });
});
