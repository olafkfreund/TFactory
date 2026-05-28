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
 *   - error:   *_failed, stuck, replan_needed
 *   - empty:   *_empty
 *   - default: pending / unknown → gray
 */
export function statusColor(status: string | null): string {
  if (!status) return 'gray';
  if (status === 'triaged' || status === 'evaluated') return 'green';
  if (status.endsWith('_failed') || status === 'stuck' || status === 'replan_needed') {
    return 'red';
  }
  if (status.endsWith('_empty')) return 'yellow';
  if (status === 'pending' || status === 'idle') return 'gray';
  // Anything else is in-flight (planning, generating, evaluating, triaging, ...)
  return 'blue';
}

const BADGE_CLASSES: Record<string, string> = {
  green: 'bg-green-100 text-green-800',
  red: 'bg-red-100 text-red-800',
  blue: 'bg-blue-100 text-blue-800',
  yellow: 'bg-yellow-100 text-yellow-800',
  gray: 'bg-gray-100 text-gray-800',
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
    <div role="status" className="flex items-center gap-2 p-6 text-gray-500">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
      <span>Loading tasks…</span>
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div role="alert" className="flex items-center gap-2 p-6 text-red-700">
      <AlertTriangle className="h-4 w-4" aria-hidden />
      <span>{message}</span>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center gap-2 p-12 text-gray-500">
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
        <thead className="border-b border-gray-200 text-xs uppercase tracking-wide text-gray-500">
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
              className="cursor-pointer border-b border-gray-100 hover:bg-gray-50"
              onClick={() => onSelectTask?.(task.spec_id)}
              data-testid={`task-row-${task.spec_id}`}
              data-spec-id={task.spec_id}
            >
              <td className="px-3 py-2 font-medium">{task.spec_id}</td>
              <td className="px-3 py-2 text-gray-600">{task.project_id}</td>
              <td className="px-3 py-2">
                <StatusBadge status={task.status} />
              </td>
              <td className="px-3 py-2 text-gray-500">{task.phase ?? '—'}</td>
              <td className="px-3 py-2 text-gray-500">{task.updated_at}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
