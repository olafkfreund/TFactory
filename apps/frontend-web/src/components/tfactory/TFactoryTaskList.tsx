/**
 * TFactory Task List — Task 10 (#11) commit 2.
 *
 * Table of TFactory workspaces sorted by ``updated_at`` desc (newest
 * first). Each row is clickable; ``onSelectTask`` fires with the
 * selected ``spec_id`` so the parent can route to a detail page.
 *
 * Loading / empty / error states are all rendered inline — the
 * parent is purely a routing host and doesn't need to handle them.
 *
 * Tests inject ``fetchFn`` so they can mock the API without MSW or
 * fetch-mock.
 */

import { useEffect, useState } from 'react';
import { Loader2, AlertTriangle, Inbox } from 'lucide-react';

import {
  listTasks,
  type TFactoryTaskSummary,
} from '../../lib/tfactory-api';

interface Props {
  /** Called when a row is clicked. */
  onSelectTask?: (specId: string) => void;
  /** Test seam: inject a fake fetch (default: global fetch). */
  fetchFn?: typeof fetch;
}

// ── Status → Tailwind colour ────────────────────────────────────────

/**
 * Map a backend status string to a Tailwind colour palette name.
 * Used to colour the badge in each row.
 *
 * Buckets:
 *   - success: evaluated, triaged
 *   - active:  planning, generating, evaluating, triaging, in-flight
 *   - error:   *_failed, stuck, stalled, replan_needed
 *   - empty:   *_empty
 *   - default: pending / unknown → gray
 */
export function statusColor(status: string | null): string {
  if (!status) return 'gray';
  if (status === 'triaged' || status === 'evaluated') return 'green';
  if (
    status.endsWith('_failed') ||
    status === 'stuck' ||
    status === 'stalled' ||
    status === 'replan_needed'
  ) {
    return 'red';
  }
  if (status.endsWith('_empty')) return 'yellow';
  if (status === 'pending' || status === 'idle') return 'gray';
  // Anything else is in-flight (planning, generating, evaluating, triaging, ...)
  return 'blue';
}

const BADGE_CLASSES: Record<string, string> = {
  green: 'bg-success/15 text-success',
  red: 'bg-destructive/15 text-destructive',
  blue: 'bg-info/15 text-primary',
  yellow: 'bg-warning/15 text-warning',
  gray: 'bg-muted text-foreground',
};

function StatusBadge({ status }: { status: string | null }) {
  const color = statusColor(status);
  const cls = BADGE_CLASSES[color] || BADGE_CLASSES.gray;
  return (
    <span
      data-testid="status-badge"
      data-status-color={color}
      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}
    >
      {status ?? '—'}
    </span>
  );
}

// ── Component states ────────────────────────────────────────────────

function LoadingState() {
  return (
    <div role="status" className="flex items-center gap-2 p-6 text-muted-foreground">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
      <span>Loading tasks…</span>
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div role="alert" className="flex items-center gap-2 p-6 text-destructive">
      <AlertTriangle className="h-4 w-4" aria-hidden />
      <span>{message}</span>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center gap-2 p-12 text-muted-foreground">
      <Inbox className="h-8 w-8" aria-hidden />
      <p>No TFactory tasks yet.</p>
      <p className="text-xs">Workspaces appear here once an AIFactory handover lands.</p>
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────────

export function TFactoryTaskList({ onSelectTask, fetchFn }: Props) {
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
    return () => {
      cancelled = true;
    };
  }, [fetchFn]);

  if (loading) return <LoadingState />;
  if (error) return <ErrorState message={error} />;
  if (tasks.length === 0) return <EmptyState />;

  return (
    <div className="overflow-x-auto">
      <table
        className="w-full text-left text-sm"
        data-testid="tfactory-task-list"
        aria-label="TFactory tasks"
      >
        <thead className="border-b border-border text-xs uppercase tracking-wide text-muted-foreground">
          <tr>
            <th className="px-3 py-2">Spec</th>
            <th className="px-3 py-2">Project</th>
            <th className="px-3 py-2">Status</th>
            <th className="px-3 py-2">Phase</th>
            <th className="px-3 py-2">Updated</th>
          </tr>
        </thead>
        <tbody>
          {tasks.map((task) => (
            <tr
              key={`${task.project_id}::${task.spec_id}`}
              className="cursor-pointer border-b border-border hover:bg-muted"
              onClick={() => onSelectTask?.(task.spec_id)}
              data-testid={`task-row-${task.spec_id}`}
              data-spec-id={task.spec_id}
            >
              <td className="px-3 py-2 font-medium">{task.spec_id}</td>
              <td className="px-3 py-2 text-muted-foreground">{task.project_id}</td>
              <td className="px-3 py-2">
                <StatusBadge status={task.status} />
              </td>
              <td className="px-3 py-2 text-muted-foreground">{task.phase ?? '—'}</td>
              <td className="px-3 py-2 text-muted-foreground">{task.updated_at}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
