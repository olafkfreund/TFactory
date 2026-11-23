/**
 * TFactoryPipelineView — fetches the TFactory workspace specs and renders them
 * as the shared Factory pipeline board. Polls so live status changes
 * (planning → … → triaged) flow between stages.
 */
import { useEffect, useState } from 'react';

import { listTasks, type TFactoryTaskSelection, type TFactoryTaskSummary } from '../../lib/tfactory-api';
import { TFactoryPipelineBoard } from './TFactoryPipelineBoard';

interface Props {
  onSelectTask: (selection: TFactoryTaskSelection) => void;
  projectId?: string;
  /** Test seam: inject a fake fetch (default: global fetch). */
  fetchFn?: typeof fetch;
  /** Background auto-refresh interval (ms). 0 disables. */
  pollMs?: number;
}

export function TFactoryPipelineView({ onSelectTask, projectId, fetchFn, pollMs = 5000 }: Props) {
  const [tasks, setTasks] = useState<TFactoryTaskSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    listTasks({ fetchFn, projectId })
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
  }, [fetchFn, projectId]);

  useEffect(() => {
    if (!pollMs || pollMs <= 0) return;
    let cancelled = false;
    const id = setInterval(() => {
      listTasks({ fetchFn, projectId })
        .then((response) => { if (!cancelled) setTasks(response.tasks); })
        .catch(() => { /* keep last-good board on a transient poll error */ });
    }, pollMs);
    return () => { cancelled = true; clearInterval(id); };
  }, [fetchFn, projectId, pollMs]);

  return (
    <div data-testid="tfactory-pipeline" className="flex flex-col gap-3 h-full">
      <header className="f-page-head">
        <div className="f-page-head__main">
          <h1 className="f-page-head__title">TFactory Pipeline</h1>
          <p className="f-page-head__desc">
            Plan → Generate → Execute → Report. Live view of TFactory workspaces under ~/.tfactory/.
          </p>
        </div>
      </header>
      {loading && tasks.length === 0 ? (
        <div className="f-state f-state--loading" aria-busy="true">
          <div className="f-state__skeleton f-state__skeleton--row" />
        </div>
      ) : error && tasks.length === 0 ? (
        <div className="f-state f-state--error" data-testid="tfactory-pipeline-error">
          <div className="f-state__body">
            <span className="f-state__reason">Failed to load TFactory workspaces: {error}</span>
          </div>
        </div>
      ) : (
        <TFactoryPipelineBoard
          tasks={tasks}
          projectId={projectId}
          onSelectTask={onSelectTask}
          fetchFn={fetchFn}
        />
      )}
    </div>
  );
}
