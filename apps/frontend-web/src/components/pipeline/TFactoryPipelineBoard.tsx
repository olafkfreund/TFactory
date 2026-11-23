/**
 * TFactoryPipelineBoard — TFactory specs board rendered with the hub-owned
 * shared factory-ui PipelineStrip, WorkColumn and WorkCard components (#3876).
 *
 * Stages: Plan → Generate → Execute → Report.
 * Done/triaged runs collapse to a single 36px row by default; failed runs
 * surface their failure reason plus Re-run (outline) and Dismiss (ghost).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  dismissRun,
  rerunTask,
  type TFactoryTaskSelection,
  type TFactoryTaskSummary,
} from '../../lib/tfactory-api';
import { formatRelativeTime } from '../../lib/utils';
import { usePipelineStripSlot } from '../../contexts/PipelineStripContext';
import {
  PipelineStrip,
  type PipelineStage,
  type PipelineStripLabels,
  type PipelineStripProps,
  type StageTone,
} from '../factory-ui/PipelineStrip';
import { WorkCard, type WorkCardLabels, type WorkPhase } from '../factory-ui/WorkCard';
import { WorkColumn } from '../factory-ui/WorkColumn';
import { Button } from '../ui/button';

type Stage = 'plan' | 'generate' | 'execute' | 'report';

const ORDER: Stage[] = ['plan', 'generate', 'execute', 'report'];

const STAGES: { key: Stage; label: string; service: string; tone: StageTone }[] = [
  { key: 'plan', label: 'Plan', service: 'Planner', tone: 'plan' },
  { key: 'generate', label: 'Generate', service: 'Gen-Functional', tone: 'code' },
  { key: 'execute', label: 'Execute', service: 'Executor', tone: 'test' },
  { key: 'report', label: 'Report', service: 'Evaluator · Triager', tone: 'success' },
];

const _norm = (s: string | null): string => (s || '').toLowerCase();

/** Terminal "done" verdict — a clean triage outcome. */
export function isDone(t: TFactoryTaskSummary): boolean {
  return ['triaged', 'triaged_empty', 'generated_empty', 'evaluated'].includes(_norm(t.status));
}

/** Failure outcome. */
export function isFailed(t: TFactoryTaskSummary): boolean {
  const s = _norm(t.status);
  return s.endsWith('_failed') || ['failed', 'stuck', 'stalled', 'replan_needed'].includes(s);
}

/** Which stage a spec sits in, from status (preferred) then phase. */
export function stageOf(t: TFactoryTaskSummary): Stage {
  const s = _norm(t.status);
  if (['triaged', 'triaged_empty', 'evaluated', 'evaluating', 'triaging'].includes(s)) return 'report';
  if (s === 'executing') return 'execute';
  if (['generating', 'generated', 'generated_empty', 'replan_needed'].includes(s)) return 'generate';
  if (['pending', 'planning', 'idle', 'created'].includes(s)) return 'plan';
  const p = _norm(t.phase);
  if (p === 'verify_job_reaped') return 'report';
  if (p.startsWith('triager') || p.startsWith('evaluator')) return 'report';
  if (p.startsWith('executor')) return 'execute';
  if (p.startsWith('gen_functional') || p.startsWith('gen-functional')) return 'generate';
  if (p.startsWith('planner')) return 'plan';
  return 'plan';
}

/** The stage actively running RIGHT NOW (null when idle/terminal). */
function activeStage(t: TFactoryTaskSummary): Stage | null {
  const s = _norm(t.status);
  if (s === 'planning') return 'plan';
  if (s === 'generating') return 'generate';
  if (s === 'executing') return 'execute';
  if (s === 'evaluating' || s === 'triaging') return 'report';
  return null;
}

type PState = WorkPhase['state'];

export function phaseStrip(t: TFactoryTaskSummary): WorkPhase[] {
  const cur = stageOf(t);
  const done = isDone(t);
  const failed = isFailed(t);
  const reachedIdx = ORDER.indexOf(cur);
  const act = activeStage(t);

  const stateFor = (stage: Stage): PState => {
    const idx = ORDER.indexOf(stage);
    if (act === stage) return 'active';
    if (done) return idx <= reachedIdx ? 'done' : 'pending';
    if (failed && stage === cur) return 'failed';
    if (idx < reachedIdx) return 'done';
    if (idx === reachedIdx) return failed ? 'failed' : 'active';
    return 'pending';
  };

  return [
    { key: 'plan', label: 'Plan', state: stateFor('plan') },
    { key: 'gen', label: 'Generate', state: stateFor('generate') },
    { key: 'exec', label: 'Execute', state: stateFor('execute') },
    { key: 'report', label: 'Report', state: stateFor('report') },
  ];
}

interface Props {
  tasks: TFactoryTaskSummary[];
  projectId?: string;
  onSelectTask: (selection: TFactoryTaskSelection) => void;
  fetchFn?: typeof fetch;
}

export function TFactoryPipelineBoard({ tasks, projectId, onSelectTask, fetchFn }: Props) {
  const { t } = useTranslation(['tasks', 'navigation']);
  const stripSlot = usePipelineStripSlot();
  const [selectedStage, setSelectedStage] = useState<Stage | null>(null);
  const [failureOnly, setFailureOnly] = useState(false);
  const [showFailed, setShowFailed] = useState(false);
  const [showDismissed, setShowDismissed] = useState(false);
  const [overrides, setOverrides] = useState<Record<string, { patch: Partial<TFactoryTaskSummary>; updatedAt: string }>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [plannerReason, setPlannerReason] = useState<string | null>(null);
  const [alarm, setAlarm] = useState<{ key: string; event: string | number } | undefined>(undefined);
  const prevFailedRef = useRef<Set<string> | null>(null);

  useEffect(() => {
    setOverrides((current) => {
      const next = { ...current };
      for (const task of tasks) {
        const key = `${task.project_id}:${task.spec_id}`;
        const override = next[key];
        if (
          override &&
          (task.updated_at !== override.updatedAt ||
            !isFailed(task) ||
            (override.patch.dismissed === true && task.dismissed === true))
        ) {
          delete next[key];
        }
      }
      return Object.keys(next).length === Object.keys(current).length ? current : next;
    });
  }, [tasks]);

  const visible = useMemo(
    () =>
      tasks
        .map((task) => ({
          ...task,
          ...(isFailed(task) && overrides[`${task.project_id}:${task.spec_id}`]?.updatedAt === task.updated_at
            ? overrides[`${task.project_id}:${task.spec_id}`].patch
            : undefined),
        }))
        .filter((task) => showDismissed || !task.dismissed),
    [tasks, overrides, showDismissed]
  );

  const failed = useMemo(() => visible.filter(isFailed), [visible]);

  useEffect(() => {
    const nextFailed = new Set<string>();
    let latestAlarm: { key: string; event: string } | undefined;
    for (const task of failed) {
      const key = `${task.project_id}:${task.spec_id}`;
      nextFailed.add(key);
      if (prevFailedRef.current && !prevFailedRef.current.has(key)) {
        latestAlarm = { key: stageOf(task), event: `${key}:${task.updated_at}` };
      }
    }
    prevFailedRef.current = nextFailed;
    if (latestAlarm) setAlarm(latestAlarm);
  }, [failed]);

  const actOn = async (task: TFactoryTaskSummary, action: 'rerun' | 'dismiss') => {
    const key = `${task.project_id}:${task.spec_id}`;
    setBusy(key);
    setActionError(null);
    setPlannerReason(null);
    try {
      if (action === 'rerun') {
        const result = await rerunTask(task.spec_id, { projectId: task.project_id, fetchFn });
        setOverrides((current) => ({
          ...current,
          [key]: { patch: { status: 'pending', phase: null, dismissed: false }, updatedAt: task.updated_at },
        }));
        if (!result.planner_scheduled) {
          const reasons: Record<string, string> = {
            checkout_failed: 'tasks:pipeline.checkoutFailed',
            scheduler_failed: 'tasks:pipeline.schedulerFailed',
            auto_plan_disabled: 'tasks:pipeline.autoPlanDisabled',
          };
          setPlannerReason(t(reasons[result.planner_reason ?? ''] ?? 'tasks:pipeline.plannerNotScheduled'));
        }
      } else {
        await dismissRun(task.spec_id, { projectId: task.project_id, fetchFn });
        setOverrides((current) => ({
          ...current,
          [key]: { patch: { dismissed: true }, updatedAt: task.updated_at },
        }));
      }
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(null);
    }
  };

  const byStage = useMemo(() => {
    const g: Record<Stage, TFactoryTaskSummary[]> = { plan: [], generate: [], execute: [], report: [] };
    for (const task of visible) {
      if (!isFailed(task)) g[stageOf(task)].push(task);
    }
    for (const k of ORDER) {
      g[k].sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
    }
    return g;
  }, [visible]);

  const failedByStage = useMemo(() => {
    const g: Record<Stage, TFactoryTaskSummary[]> = { plan: [], generate: [], execute: [], report: [] };
    for (const task of failed) {
      g[stageOf(task)].push(task);
    }
    for (const k of ORDER) {
      g[k].sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
    }
    return g;
  }, [failed]);

  const stages: PipelineStage[] = useMemo(
    () =>
      STAGES.map((stage) => ({
        key: stage.key,
        label: stage.label,
        service: stage.service,
        count: byStage[stage.key].length,
        failed: failedByStage[stage.key].length,
        tone: stage.tone,
      })),
    [byStage, failedByStage]
  );

  const handleSelectStage = useCallback((key: string | null) => {
    setSelectedStage((key as Stage | null) ?? null);
    setFailureOnly(false);
    if (key === null) setShowFailed(false);
  }, []);

  const handleSelectFailed = useCallback((key: string) => {
    setSelectedStage(key as Stage);
    setFailureOnly(true);
    setShowFailed(true);
  }, []);

  const stripProps: PipelineStripProps = useMemo(
    () => ({
      stages,
      activeStage: selectedStage,
      onSelectStage: handleSelectStage,
      onSelectFailed: handleSelectFailed,
      alarm,
    }),
    [stages, selectedStage, handleSelectStage, handleSelectFailed, alarm]
  );

  useEffect(() => {
    if (!stripSlot) return;
    stripSlot.setStrip(stripProps);
    return () => {
      stripSlot.setStrip(undefined);
    };
  }, [stripSlot, stripProps]);

  const pipelineLabels: PipelineStripLabels = useMemo(
    () => ({
      pipeline: t('navigation:shell.pipeline', 'Pipeline stages'),
      clearFilter: t('navigation:shell.clearFilter', 'Clear filter'),
      failures: (count: number) => t('tasks:pipeline.failed', { count, defaultValue: '{{count}} failed' }),
      filterFailures: (stage) =>
        t('navigation:shell.filterFailures', {
          stage: stage.label,
          count: stage.failed,
          defaultValue: 'Filter {{stage}} failures ({{count}})',
        }),
    }),
    [t]
  );

  const workCardLabels: WorkCardLabels = useMemo(
    () => ({
      expand: (title: string) => `Expand ${title}`,
      collapse: (title: string) => `Collapse ${title}`,
      phaseStates: {
        done: 'done',
        active: 'active',
        failed: 'failed',
        pending: 'pending',
      },
    }),
    []
  );

  const visibleStages = useMemo(
    () => (selectedStage ? STAGES.filter((s) => s.key === selectedStage) : STAGES),
    [selectedStage]
  );

  const visibleFailed = useMemo(
    () => (selectedStage ? failedByStage[selectedStage] : failed),
    [selectedStage, failedByStage, failed]
  );

  return (
    <div className="flex flex-col gap-3">
      {!stripSlot && <PipelineStrip {...stripProps} labels={pipelineLabels} />}

      <div className="f-board">
        {visibleStages.map((stage) => (
          <WorkColumn
            key={stage.key}
            title={stage.label}
            tone={stage.tone}
            projectId={projectId}
            emptyLabel={emptyText(stage.key)}
            showMoreLabel={(count) => t('tasks:pipeline.showMore', { count, defaultValue: `Show ${count} more` })}
            items={(failureOnly ? [] : byStage[stage.key]).map((task) => {
              const done = isDone(task);
              const running = activeStage(task) !== null;
              const tone = done
                ? (['generated_empty', 'triaged_empty'].includes(_norm(task.status)) ? 'warning' : 'success')
                : (running ? 'running' : 'neutral');
              return {
                id: task.spec_id,
                projectId: task.project_id,
                collapsedByDefault: done,
                render: ({ expanded, onToggle }) => (
                  <div data-testid={`task-row-${task.spec_id}`}>
                    <WorkCard
                      labels={workCardLabels}
                      title={task.spec_id}
                      subtitle={task.project_id}
                      status={task.status ?? '—'}
                      tone={tone}
                      age={formatRelativeTime(task.updated_at)}
                      phases={phaseStrip(task)}
                      badges={[task.project_id, ...(task.phase ? [task.phase] : [])]}
                      collapsedByDefault={done}
                      expanded={expanded}
                      onToggle={onToggle}
                      onOpen={() => onSelectTask({ projectId: task.project_id, specId: task.spec_id })}
                    />
                  </div>
                ),
              };
            })}
          />
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-4 text-sm">
        <Button
          type="button"
          size="sm"
          variant="outline"
          aria-expanded={showFailed}
          onClick={() => setShowFailed((value) => !value)}
        >
          {t('tasks:pipeline.failed', { count: visibleFailed.length })}
        </Button>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={showDismissed}
            onChange={(event) => setShowDismissed(event.target.checked)}
          />
          {t('tasks:pipeline.showDismissed')}
        </label>
      </div>
      {actionError && <p role="alert" className="text-sm text-destructive">{actionError}</p>}
      {plannerReason && <p role="status" className="text-sm text-warning">{plannerReason}</p>}
      {showFailed && visibleFailed.length > 0 && (
        <div data-testid="failed-runs" className="grid gap-2">
          {visibleFailed.map((task) => {
            const key = `${task.project_id}:${task.spec_id}`;
            return (
              <div key={key} data-testid={`task-row-${task.spec_id}`}>
                <WorkCard
                  labels={workCardLabels}
                  title={task.spec_id}
                  subtitle={task.project_id}
                  status={task.status ?? '—'}
                  tone="danger"
                  collapsedByDefault={Boolean(task.dismissed)}
                  age={formatRelativeTime(task.updated_at)}
                  reason={task.phase ?? task.status ?? undefined}
                  phases={phaseStrip(task)}
                  badges={[task.project_id, ...(task.phase ? [task.phase] : [])]}
                  onOpen={() => onSelectTask({ projectId: task.project_id, specId: task.spec_id })}
                  actions={[
                    {
                      id: 'rerun',
                      label: t('tasks:pipeline.rerun'),
                      variant: 'outline',
                      disabled: busy !== null,
                      busy: busy === key,
                      onClick: () => { void actOn(task, 'rerun'); },
                    },
                    {
                      id: 'dismiss',
                      label: t('tasks:pipeline.dismiss'),
                      variant: 'ghost',
                      disabled: busy !== null || Boolean(task.dismissed),
                      busy: busy === key,
                      onClick: () => { void actOn(task, 'dismiss'); },
                    },
                  ]}
                />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function emptyText(stage: Stage): string {
  switch (stage) {
    case 'plan': return 'Nothing planned';
    case 'generate': return 'Nothing generating';
    case 'execute': return 'Nothing running';
    case 'report': return 'No reports yet';
    default: return '';
  }
}
