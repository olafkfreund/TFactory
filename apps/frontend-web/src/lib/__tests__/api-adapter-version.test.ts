/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as apiClient from '../api-client';
import { resetAppVersionCache, webAPI } from '../api-adapter';

describe('webAPI app version from /api/health', () => {
  beforeEach(() => {
    resetAppVersionCache();
    vi.restoreAllMocks();
  });

  it('caches the /api/health version across getAppVersion and getDebugInfo', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({
      success: true,
      data: { status: 'healthy', version: '3.2.1' },
    });

    const [version, debugInfo] = await Promise.all([
      webAPI.getAppVersion(),
      webAPI.getDebugInfo(),
    ]);

    expect(version).toBe('3.2.1');
    expect(debugInfo.systemInfo.appVersion).toBe('3.2.1');
    expect(getSpy).toHaveBeenCalledTimes(1);
    expect(getSpy).toHaveBeenCalledWith('/health');

    const secondVersion = await webAPI.getAppVersion();
    expect(secondVersion).toBe('3.2.1');
    expect(getSpy).toHaveBeenCalledTimes(1);
  });

  it('returns "unknown" on failure or rejection with no unhandled rejection', async () => {
    const getSpy = vi
      .spyOn(apiClient, 'get')
      .mockResolvedValueOnce({ success: false, error: 'HTTP 503' })
      .mockRejectedValueOnce(new Error('network down'));

    await expect(webAPI.getAppVersion()).resolves.toBe('unknown');
    await expect(webAPI.getDebugInfo()).resolves.toMatchObject({
      systemInfo: { appVersion: 'unknown' },
    });
    expect(getSpy).toHaveBeenCalledTimes(2);
  });
});
