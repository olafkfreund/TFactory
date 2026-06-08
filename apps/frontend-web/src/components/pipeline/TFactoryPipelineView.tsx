/**
 * TFactoryPipelineView — fetches the TFactory workspace specs and renders them
 * as the animated ring pipeline board (#267). This is the unified "Tasks + Tests"
 * view: same data as the old Tests list, presented as the Factory-brand pipeline.
 * Polls so live status changes (planning → … → triaged) flow between rings.
 */
import { useEffect, useState } from 'react';

import { listTasks, type TFactoryTaskSummary } from '../../lib/tfactory-api';
import { TFactoryPipelineBoard } from './TFactoryPipelineBoard';

interface Props {
  onSelectTask: (specId: string) => void;
  /** Test seam: inject a fake fetch (default: global fetch). */
  fetchFn?: typeof fetch;
  /** Background auto-refresh interval (ms). 0 disables. */
  pollMs?: number;
}

export function TFactoryPipelineView({ onSelectTask, fetchFn, pollMs = 5000 }: Props) {
  const [tasks, setTasks] = useState<TFactoryTaskSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    listTasks({ fetchFn })
      .then((response) => {
        if (cancelled) return;
        setTasks(response.tasks);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      });
    return () => { cancelled = true; };
  }, [fetchFn]);

  useEffect(() => {
    if (!pollMs || pollMs <= 0) return;
    const id = setInterval(() => {
      listTasks({ fetchFn })
        .then((response) => setTasks(response.tasks))
        .catch(() => { /* keep last-good board on a transient poll error */ });
    }, pollMs);
    return () => clearInterval(id);
  }, [fetchFn, pollMs]);

  return (
    <div data-testid="tfactory-pipeline" className="flex flex-col gap-3 h-full">
      <header className="border-b border-border pb-2">
        <h1 className="text-xl font-semibold">TFactory Pipeline</h1>
        <p className="text-xs text-muted-foreground">
          Plan → Generate → Execute → Report. Live view of TFactory workspaces under ~/.tfactory/.
        </p>
      </header>
      {loading && tasks.length === 0 ? (
        <div className="text-sm text-muted-foreground p-4">Loading pipeline…</div>
      ) : error && tasks.length === 0 ? (
        <div className="text-sm text-red-400 p-4" data-testid="tfactory-pipeline-error">
          Failed to load TFactory workspaces: {error}
        </div>
      ) : (
        <TFactoryPipelineBoard tasks={tasks} onSelectTask={onSelectTask} />
      )}
    </div>
  );
}
