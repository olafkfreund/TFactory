/** @vitest-environment jsdom */
import { describe, expect, it, vi } from 'vitest';
import type { Task } from '../shared/types';
import { loadTasks, useTaskStore } from './task-store';

describe('loadTasks', () => {
  it('keeps the latest project tasks when an older request completes last', async () => {
    let resolveOld!: (value: { success: true; data: Task[] }) => void;
    const oldRequest = new Promise<{ success: true; data: Task[] }>((resolve) => { resolveOld = resolve; });
    const newTask = { id: 'new-task' } as Task;
    (window as unknown as { API: unknown }).API = {
      getTasks: vi.fn().mockReturnValueOnce(oldRequest).mockResolvedValueOnce({ success: true, data: [newTask] }),
    };

    const oldLoad = loadTasks('old-project');
    await loadTasks('new-project');
    resolveOld({ success: true, data: [{ id: 'old-task' } as Task] });
    await oldLoad;

    expect(useTaskStore.getState().tasks).toEqual([newTask]);
    expect(useTaskStore.getState().isLoading).toBe(false);
  });
});
