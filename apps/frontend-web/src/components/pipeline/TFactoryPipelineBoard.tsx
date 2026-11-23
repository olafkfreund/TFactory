/**
 * TFactoryPipelineBoard — the TFactory Tests/Tasks view as an animated pipeline
 * of rings (#267). Unifies the old Tasks board + the read-only Tests list into a
 * single Factory-brand board: a prominent bordered rail of four big stage rings
 * — Plan → Generate → Execute → Report (TFactory's five agents fold into four:
 * Evaluator + Triager = Report) — above four columns of spec cards. The active
 * ring glows + animates; a package-box flies between rings on a stage change.
 * Each card carries a phase strip + a robot 👍 / 👎 + red ✗ verdict.
 *
 * Data: the TFactory workspace specs (TFactoryTaskSummary[]) — the same source
 * as the Tests list. Brand assets (icons.tsx, pipeline.css) are shared with the
 * other factories per #267.
 */
import { AnimatePresence, motion } from 'motion/react';
import {
  useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState,
  type CSSProperties,
} from 'react';

import { dismissRun, rerunTask, type TFactoryTaskSelection, type TFactoryTaskSummary } from '../../lib/tfactory-api';
import { useTranslation } from 'react-i18next';
import { formatRelativeTime } from '../../lib/utils';
import { statusColor } from '../tfactory/TFactoryTaskList';
import {
  CrossIcon, FlaskIcon, PackageIcon, PlanDocIcon,
  RobotHeadIcon, RobotThumbsDownIcon, RobotThumbsUpIcon, SignalIcon, TerminalIcon,
} from './icons';
import './pipeline.css';

type Stage = 'plan' | 'generate' | 'execute' | 'report';

const ORDER: Stage[] = ['plan', 'generate', 'execute', 'report'];

// TFactory stage → the shared brand colour var (plan/code/review/done) so the
// board reuses pipeline.css's four jewel tones without new CSS.
const COLOR: Record<Stage, string> = {
  plan: 'plan', generate: 'code', execute: 'review', report: 'done',
};

// Per-phase chip colour (phaseStrip key → brand colour var) so each phase icon
// shows its OWN stage hue, not the card's done-green (#267 polish).
const CHIP_COLOR: Record<string, string> = {
  plan: 'plan', gen: 'code', exec: 'review', report: 'done',
};

const STAGES: { key: Stage; label: string; sub: string; Icon: typeof RobotHeadIcon }[] = [
  { key: 'plan', label: 'Plan', sub: 'Planner', Icon: PlanDocIcon },
  { key: 'generate', label: 'Generate', sub: 'Gen-Functional', Icon: RobotHeadIcon },
  { key: 'execute', label: 'Execute', sub: 'Executor', Icon: TerminalIcon },
  { key: 'report', label: 'Report', sub: 'Evaluator · Triager', Icon: FlaskIcon },
];

const STAGE_ICON: Record<Stage, typeof RobotHeadIcon> = {
  plan: PlanDocIcon, generate: RobotHeadIcon, execute: TerminalIcon, report: FlaskIcon,
};

const _norm = (s: string | null): string => (s || '').toLowerCase();

/** Terminal "done" verdict — a clean triage outcome. */
function isDone(t: TFactoryTaskSummary): boolean {
  return ['triaged', 'triaged_empty', 'generated_empty', 'evaluated'].includes(_norm(t.status));
}

/** Failure outcome — surfaced as 👎 + red ✗. */
function isFailed(t: TFactoryTaskSummary): boolean {
  const s = _norm(t.status);
  return s.endsWith('_failed') || ['failed', 'stuck', 'stalled', 'replan_needed'].includes(s);
}

/** Which ring a spec sits in, from status (preferred) then phase. */
function stageOf(t: TFactoryTaskSummary): Stage {
  const s = _norm(t.status);
  if (['triaged', 'triaged_empty', 'evaluated', 'evaluating', 'triaging'].includes(s)) return 'report';
  if (s === 'executing') return 'execute';
  if (['generating', 'generated', 'generated_empty', 'replan_needed'].includes(s)) return 'generate';
  if (['pending', 'planning', 'idle', 'created'].includes(s)) return 'plan';
  // phase fallback
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

type PState = 'done' | 'active' | 'failed' | 'pending';

function phaseStrip(t: TFactoryTaskSummary): { key: string; label: string; Icon: typeof RobotHeadIcon; state: PState }[] {
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
    { key: 'plan', label: 'Plan', Icon: PlanDocIcon, state: stateFor('plan') },
    { key: 'gen', label: 'Generate', Icon: RobotHeadIcon, state: stateFor('generate') },
    { key: 'exec', label: 'Execute', Icon: TerminalIcon, state: stateFor('execute') },
    { key: 'report', label: 'Report', Icon: FlaskIcon, state: stateFor('report') },
  ];
}

interface Flight { id: string; to: number; x0: number; x1: number; y: number; }

interface Props {
  tasks: TFactoryTaskSummary[];
  onSelectTask: (selection: TFactoryTaskSelection) => void;
  fetchFn?: typeof fetch;
}

export function TFactoryPipelineBoard({ tasks, onSelectTask, fetchFn }: Props) {
  const { t } = useTranslation('tasks');
  const [showFailed, setShowFailed] = useState(false);
  const [showDismissed, setShowDismissed] = useState(false);
  const [overrides, setOverrides] = useState<Record<string, { patch: Partial<TFactoryTaskSummary>; updatedAt: string }>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [plannerReason, setPlannerReason] = useState<string | null>(null);
  const boardRef = useRef<HTMLDivElement>(null);
  const ringRefs = useRef<(HTMLDivElement | null)[]>([]);
  const [centers, setCenters] = useState<{ x: number; y: number }[]>([]);
  const [flights, setFlights] = useState<Flight[]>([]);
  const prevStage = useRef<Map<string, Stage>>(new Map());

  useEffect(() => {
    setOverrides((current) => {
      const next = { ...current };
      for (const task of tasks) {
        const key = `${task.project_id}:${task.spec_id}`;
        const override = next[key];
        if (override && (task.updated_at !== override.updatedAt || !isFailed(task) || (override.patch.dismissed === true && task.dismissed === true))) delete next[key];
      }
      return Object.keys(next).length === Object.keys(current).length ? current : next;
    });
  }, [tasks]);

  const visible = useMemo(() => tasks.map((task) => ({
    ...task,
    ...(isFailed(task) && overrides[`${task.project_id}:${task.spec_id}`]?.updatedAt === task.updated_at
      ? overrides[`${task.project_id}:${task.spec_id}`].patch : undefined),
  })).filter((task) => showDismissed || !task.dismissed), [tasks, overrides, showDismissed]);
  const failed = visible.filter(isFailed);

  const actOn = async (task: TFactoryTaskSummary, action: 'rerun' | 'dismiss') => {
    const key = `${task.project_id}:${task.spec_id}`;
    setBusy(key);
    setActionError(null);
    setPlannerReason(null);
    try {
      if (action === 'rerun') {
        const result = await rerunTask(task.spec_id, { projectId: task.project_id, fetchFn });
        setOverrides((current) => ({ ...current, [key]: { patch: { status: 'pending', phase: null, dismissed: false }, updatedAt: task.updated_at } }));
        if (!result.planner_scheduled) {
          const reasons: Record<string, string> = {
            checkout_failed: 'pipeline.checkoutFailed',
            scheduler_failed: 'pipeline.schedulerFailed',
            auto_plan_disabled: 'pipeline.autoPlanDisabled',
          };
          setPlannerReason(t(reasons[result.planner_reason ?? ''] ?? 'pipeline.plannerNotScheduled'));
        }
      } else {
        await dismissRun(task.spec_id, { projectId: task.project_id, fetchFn });
        setOverrides((current) => ({ ...current, [key]: { patch: { dismissed: true }, updatedAt: task.updated_at } }));
      }
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    } finally { setBusy(null); }
  };

  const byStage = useMemo(() => {
    const g: Record<Stage, TFactoryTaskSummary[]> = { plan: [], generate: [], execute: [], report: [] };
    for (const t of visible) if (!isFailed(t)) g[stageOf(t)].push(t);
    for (const k of ORDER) {
      g[k].sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
    }
    return g;
  }, [visible]);

  const active = useMemo(() => {
    const a: Record<Stage, boolean> = { plan: false, generate: false, execute: false, report: false };
    for (const t of visible) {
      const s = activeStage(t);
      if (s) a[s] = true;
    }
    return a;
  }, [visible]);

  const measure = useCallback(() => {
    const board = boardRef.current;
    if (!board) return;
    const br = board.getBoundingClientRect();
    setCenters(ringRefs.current.map((el) => {
      if (!el) return { x: 0, y: 0 };
      const r = el.getBoundingClientRect();
      return { x: r.left - br.left + r.width / 2, y: r.top - br.top + r.height / 2 };
    }));
  }, []);

  useLayoutEffect(() => { measure(); }, [measure, tasks.length]);
  useEffect(() => {
    const board = boardRef.current;
    if (!board) return;
    const ro = new ResizeObserver(() => measure());
    ro.observe(board);
    window.addEventListener('resize', measure);
    return () => { ro.disconnect(); window.removeEventListener('resize', measure); };
  }, [measure]);

  useEffect(() => {
    if (centers.length < ORDER.length) {
      if (prevStage.current.size === 0) for (const t of tasks) prevStage.current.set(`${t.project_id}:${t.spec_id}`, stageOf(t));
      return;
    }
    const launched: Flight[] = [];
    const seen = new Set<string>();
    for (const t of tasks) {
      const key = `${t.project_id}:${t.spec_id}`;
      seen.add(key);
      const cur = stageOf(t);
      const was = prevStage.current.get(key);
      prevStage.current.set(key, cur);
      if (was && was !== cur) {
        const from = ORDER.indexOf(was), to = ORDER.indexOf(cur);
        launched.push({
          id: `${key}-${was}-${cur}`,
          to, x0: centers[from].x, x1: centers[to].x, y: centers[from].y,
        });
      }
    }
    for (const id of [...prevStage.current.keys()]) if (!seen.has(id)) prevStage.current.delete(id);
    if (launched.length) setFlights((f) => [...f, ...launched]);
  }, [tasks, centers]);

  const removeFlight = useCallback((id: string) => setFlights((f) => f.filter((x) => x.id !== id)), []);

  return (
    <div ref={boardRef} className="pl-board">
      {/* prominent ring rail */}
      <div className="pl-railpanel">
        <div className="pl-rail">
          {STAGES.map((stage, i) => {
            const isActive = active[stage.key];
            const cssVar = { ['--c' as string]: `var(--pl-${COLOR[stage.key]})` } as CSSProperties;
            return (
              <div className="pl-rail-cell" key={stage.key} style={cssVar}>
                <div className={`pl-stage ${isActive ? 'is-active' : byStage[stage.key].length === 0 ? 'is-idle' : ''}`}>
                  <div className="pl-ring" ref={(el) => { ringRefs.current[i] = el; }}>
                    <stage.Icon size={40} />
                    <span className="pl-badge">{byStage[stage.key].length}</span>
                    {stage.key === 'plan' && <span className="pl-mcp" title="Reading the spec (MCP)"><SignalIcon /></span>}
                  </div>
                  <span className="pl-label">{stage.label}</span>
                  <span className="pl-sublabel">{stage.sub}</span>
                </div>
              </div>
            );
          })}
        </div>
        <div className="pl-connectors" aria-hidden>
          {centers.length === ORDER.length && ORDER.slice(0, -1).map((s, i) => {
            const a = centers[i], b = centers[i + 1];
            const flowing = active[ORDER[i]] || active[ORDER[i + 1]];
            return (
              <div key={s} className={`pl-conn ${flowing ? 'is-flowing' : ''}`}
                style={{ left: a.x + 56, top: a.y - 1.5, width: Math.max(b.x - a.x - 112, 0),
                         ['--c' as string]: `var(--pl-${COLOR[s]})` }} />
            );
          })}
        </div>
      </div>

      {/* spec columns */}
      <div className="pl-board-grid">
        {STAGES.map((stage) => {
          const list = byStage[stage.key];
          const cssVar = { ['--c' as string]: `var(--pl-${COLOR[stage.key]})` } as CSSProperties;
          return (
            <div className="pl-col" key={stage.key} style={cssVar}>
              <div className="pl-col-list">
                <AnimatePresence initial={false}>
                  {list.map((task) => {
                    const done = isDone(task);
                    const failed = !done && isFailed(task);
                    const now = activeStage(task);
                    const NowIcon = now ? STAGE_ICON[now] : null;
                    return (
                      <motion.div key={`${task.project_id}:${task.spec_id}`} layout
                        className={`pl-card-wrap ${failed ? 'is-failed' : ''} ${done ? 'is-done' : ''} ${now ? 'is-live' : ''}`}
                        initial={{ opacity: 0, y: 10, scale: 0.98 }}
                        animate={{ opacity: 1, y: 0, scale: 1 }}
                        exit={{ opacity: 0, scale: 0.92 }}
                        transition={{ type: 'spring', stiffness: 380, damping: 30 }}>
                        {done && (
                          <motion.span className="pl-card-done" aria-label="Triaged"
                            initial={{ scale: 0, rotate: -25 }} animate={{ scale: 1, rotate: 0 }}
                            transition={{ type: 'spring', stiffness: 500, damping: 14, delay: 0.1 }}>
                            <RobotThumbsUpIcon size={20} />
                          </motion.span>
                        )}
                        {failed && (
                          <motion.span className="pl-card-fail" aria-label="Failed"
                            initial={{ scale: 0, rotate: 25 }} animate={{ scale: 1, rotate: 0 }}
                            transition={{ type: 'spring', stiffness: 500, damping: 14, delay: 0.1 }}>
                            <RobotThumbsDownIcon size={20} />
                          </motion.span>
                        )}
                        {!done && !failed && NowIcon && (
                          <span className="pl-card-now" title={`Now: ${now}`}><NowIcon size={18} /></span>
                        )}
                        {failed && <span className="pl-card-cross" aria-hidden><CrossIcon size={84} /></span>}

                        <SpecCard task={task} onClick={() => onSelectTask({ projectId: task.project_id, specId: task.spec_id })} />

                        <div className="pl-phase-strip" aria-hidden>
                          {phaseStrip(task).map((p) => (
                            <span
                              key={p.key}
                              className="pl-chip"
                              data-state={p.state}
                              title={`${p.label}: ${p.state}`}
                              style={{ ['--cc' as string]: `var(--pl-${CHIP_COLOR[p.key]})` }}
                            >
                              <span className="pl-chip-ico">
                                <p.Icon size={16} />
                                {p.state === 'failed' && <span className="pl-chip-x"><CrossIcon size={11} /></span>}
                              </span>
                              <span className="pl-chip-lbl">{p.label}</span>
                            </span>
                          ))}
                        </div>
                      </motion.div>
                    );
                  })}
                </AnimatePresence>
                {list.length === 0 && <div className="pl-empty">{emptyText(stage.key)}</div>}
              </div>
            </div>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center gap-4 text-sm">
        <button type="button" aria-expanded={showFailed} onClick={() => setShowFailed((value) => !value)}>
          {t('pipeline.failed', { count: failed.length })}
        </button>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={showDismissed} onChange={(event) => setShowDismissed(event.target.checked)} />
          {t('pipeline.showDismissed')}
        </label>
      </div>
      {actionError && <p role="alert" className="text-sm text-destructive">{actionError}</p>}
      {plannerReason && <p role="status" className="text-sm text-warning">{plannerReason}</p>}
      {showFailed && failed.length > 0 && (
        <div data-testid="failed-runs" className="grid gap-2">
          {failed.map((task) => <div key={`${task.project_id}:${task.spec_id}`} className="rounded border border-destructive/40 p-2">
            <SpecCard task={task} onClick={() => onSelectTask({ projectId: task.project_id, specId: task.spec_id })} />
            <div className="flex gap-2 mt-2">
              <button type="button" disabled={busy !== null} onClick={() => void actOn(task, 'rerun')}>{t('pipeline.rerun')}</button>
              <button type="button" disabled={busy !== null || task.dismissed} onClick={() => void actOn(task, 'dismiss')}>{t('pipeline.dismiss')}</button>
            </div>
          </div>)}
        </div>
      )}

      <div className="pl-flights" aria-hidden>
        <AnimatePresence>
          {flights.map((fl) => (
            <motion.div key={fl.id} className="pl-pkg"
              style={{ top: fl.y, ['--c' as string]: `var(--pl-${COLOR[ORDER[fl.to]]})` }}
              initial={{ left: fl.x0, opacity: 0, scale: 0.5 }}
              animate={{ left: fl.x1, opacity: [0, 1, 1, 0], scale: [0.5, 1.15, 1, 0.8], y: [0, -26, -26, 0] }}
              transition={{ duration: 1.1, ease: 'easeInOut', times: [0, 0.2, 0.8, 1] }}
              onAnimationComplete={() => removeFlight(fl.id)}>
              <PackageIcon size={26} />
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}

/** Compact spec card — the unified replacement for the Tests-list row. */
function SpecCard({ task, onClick }: { task: TFactoryTaskSummary; onClick: () => void }) {
  const color = statusColor(task.status);
  return (
    <button
      type="button"
      className="pl-speccard"
      data-testid={`task-row-${task.spec_id}`}
      data-status-color={color}
      onClick={onClick}
    >
      <span className="pl-speccard-id">{task.spec_id}</span>
      <span className="pl-speccard-proj">{task.project_id}</span>
      <span className="pl-speccard-meta">
        <span className="pl-speccard-status" data-status-color={color}>{task.status ?? '—'}</span>
        {task.phase && <span className="pl-speccard-phase">{task.phase}</span>}
      </span>
      <span className="pl-speccard-time">{formatRelativeTime(task.updated_at)}</span>
    </button>
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
