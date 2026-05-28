/**
 * Lane status grid — Task 10 (#11) commit 3; reskinned in Task 0 (#16) commit 3.
 *
 * Presentational grid showing the FIVE v0.2 lanes:
 *
 *   - unit         (Phase 1 — lit at v0.2 MVP; pytest/Jest for the changed feature)
 *   - browser      (Phase 2 — Playwright via the live app; runs against AppRuntime)
 *   - api          (Phase 3 — HTTP/contract checks against the running service)
 *   - integration  (Phase 4 — service-to-service via testcontainers)
 *   - mutation     (Phase 5 — assertion-strength mutation testing)
 *
 * v0.1 used `functional/sast/dast/fuzz/mutation`. The v0.2 spine swap is the
 * modality-based decomposition from `docs/plans/2026-05-28-enterprise-test-frameworks-design.md`
 * (Decision 2). At commit 3 only the UNIT lane is "lit" — it shows the
 * current task's status. Browser/API/Integration/Mutation render as
 * placeholders until Task 15 (#31) fully reskins them.
 */

import { CheckCircle2, Circle, Clock, AlertCircle, Lock } from 'lucide-react';

import { statusColor } from './TFactoryTaskList';

// ── Lane definitions ──────────────────────────────────────────────────

export interface LaneDef {
  id: 'unit' | 'browser' | 'api' | 'integration' | 'mutation';
  label: string;
  phase: number;
  description: string;
}

export const LANES: readonly LaneDef[] = [
  {
    id: 'unit', label: 'Unit', phase: 1,
    description: 'pytest / Jest unit tests for the changed feature.',
  },
  {
    id: 'browser', label: 'Browser', phase: 2,
    description: 'Playwright UI tests run against the live app via AppRuntime.',
  },
  {
    id: 'api', label: 'API', phase: 3,
    description: 'HTTP / contract checks against the running service surface.',
  },
  {
    id: 'integration', label: 'Integration', phase: 4,
    description: 'Service-to-service integration via testcontainers.',
  },
  {
    id: 'mutation', label: 'Mutation', phase: 5,
    description: 'Whole-suite mutation testing for assertion strength.',
  },
] as const;

// ── Card states ──────────────────────────────────────────────────────

export type LaneCardState =
  | 'idle'         // pending / no work yet
  | 'in_flight'    // running
  | 'success'      // green (evaluated / triaged)
  | 'warning'      // yellow (empty bucket, replan, etc.)
  | 'failure'      // red (any *_failed)
  | 'placeholder'; // future phase

/**
 * Derive the LaneCardState for the unit lane from the task's
 * backend status string.
 *
 * Mirrors TFactoryTaskList's statusColor bucket mapping so the grid
 * + the task list stay visually consistent.
 */
export function unitLaneState(status: string | null): LaneCardState {
  const color = statusColor(status);
  if (color === 'green') return 'success';
  if (color === 'red') return 'failure';
  if (color === 'yellow') return 'warning';
  if (color === 'blue') return 'in_flight';
  return 'idle';
}

// ── Card visuals ─────────────────────────────────────────────────────

const STATE_VISUALS: Record<
  LaneCardState,
  { borderClass: string; bgClass: string; iconColorClass: string; label: string }
> = {
  idle: {
    borderClass: 'border-gray-200', bgClass: 'bg-white',
    iconColorClass: 'text-gray-400', label: 'Idle',
  },
  in_flight: {
    borderClass: 'border-blue-300', bgClass: 'bg-blue-50',
    iconColorClass: 'text-blue-500', label: 'In flight',
  },
  success: {
    borderClass: 'border-green-300', bgClass: 'bg-green-50',
    iconColorClass: 'text-green-600', label: 'Complete',
  },
  warning: {
    borderClass: 'border-yellow-300', bgClass: 'bg-yellow-50',
    iconColorClass: 'text-yellow-600', label: 'Needs review',
  },
  failure: {
    borderClass: 'border-red-300', bgClass: 'bg-red-50',
    iconColorClass: 'text-red-600', label: 'Failed',
  },
  placeholder: {
    borderClass: 'border-dashed border-gray-200', bgClass: 'bg-gray-50',
    iconColorClass: 'text-gray-300', label: 'Coming soon',
  },
};

function StateIcon({ state }: { state: LaneCardState }) {
  const cls = STATE_VISUALS[state].iconColorClass;
  switch (state) {
    case 'success':
      return <CheckCircle2 className={`h-5 w-5 ${cls}`} aria-hidden />;
    case 'failure':
      return <AlertCircle className={`h-5 w-5 ${cls}`} aria-hidden />;
    case 'in_flight':
      return <Clock className={`h-5 w-5 animate-pulse ${cls}`} aria-hidden />;
    case 'warning':
      return <AlertCircle className={`h-5 w-5 ${cls}`} aria-hidden />;
    case 'placeholder':
      return <Lock className={`h-5 w-5 ${cls}`} aria-hidden />;
    case 'idle':
    default:
      return <Circle className={`h-5 w-5 ${cls}`} aria-hidden />;
  }
}

interface LaneCardProps {
  lane: LaneDef;
  state: LaneCardState;
  /** Status string ("triaged", "evaluating", ...) for the unit lane. */
  detail?: string;
}

function LaneCard({ lane, state, detail }: LaneCardProps) {
  const visuals = STATE_VISUALS[state];
  const isPlaceholder = state === 'placeholder';
  return (
    <div
      data-testid={`lane-card-${lane.id}`}
      data-lane-state={state}
      className={`flex flex-col gap-1 rounded-lg border p-3 ${visuals.borderClass} ${visuals.bgClass}`}
    >
      <div className="flex items-center gap-2">
        <StateIcon state={state} />
        <span className="font-semibold">{lane.label}</span>
        <span className="ml-auto text-xs text-gray-500">
          Phase&nbsp;{lane.phase}
        </span>
      </div>
      <p className="text-xs text-gray-500">{lane.description}</p>
      {isPlaceholder ? (
        <p
          data-testid={`lane-${lane.id}-placeholder`}
          className="mt-1 text-xs italic text-gray-400"
        >
          Coming in Phase {lane.phase}
        </p>
      ) : (
        <p
          data-testid={`lane-${lane.id}-detail`}
          className="mt-1 text-xs font-medium uppercase tracking-wide"
        >
          {detail ?? visuals.label}
        </p>
      )}
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────────

interface Props {
  /**
   * The current task's backend status string. Drives the unit
   * lane's state. ``null`` → idle.
   */
  unitStatus?: string | null;
}

export function LaneStatusGrid({ unitStatus = null }: Props) {
  const unitState = unitLaneState(unitStatus);
  return (
    <div
      data-testid="lane-status-grid"
      className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5"
    >
      {LANES.map((lane) => {
        const state: LaneCardState = lane.id === 'unit'
          ? unitState
          : 'placeholder';
        const detail = lane.id === 'unit'
          ? (unitStatus ?? 'idle')
          : undefined;
        return (
          <LaneCard
            key={lane.id}
            lane={lane}
            state={state}
            detail={detail}
          />
        );
      })}
    </div>
  );
}
