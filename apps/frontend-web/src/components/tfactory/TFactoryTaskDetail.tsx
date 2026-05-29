/**
 * Per-task detail view — Task 10 (#11) commit 3.
 *
 * Tab-bar over the four artefact surfaces the operator cares about:
 *   - Status     (status.json fields + phase + counts)
 *   - Lanes      (the LaneStatusGrid, lit by status_json.status)
 *   - Verdicts   (per-test verdict rows from verdicts.json)
 *   - Report     (the rendered triage_report.md)
 *
 * Tabs whose artefact is absent are disabled — the operator can see
 * what's available without clicking blindly.
 *
 * Tests inject ``fetchFn`` to stub the API client; the component
 * lazy-loads verdicts + report only when their tabs are opened.
 */

import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, Loader2 } from 'lucide-react';

import {
  TFactoryApiError,
  getTaskDetail,
  getTriageReportMarkdown,
  getVerdicts,
  type TFactoryTaskDetail as TaskDetail,
  type TFactoryVerdict,
  type TFactoryVerdictsDocument,
} from '../../lib/tfactory-api';
import { LaneStatusGrid } from './LaneStatusGrid';
import { TFactoryLogViewer } from './TFactoryLogViewer';

type Tab = 'status' | 'lanes' | 'verdicts' | 'report' | 'logs';

interface Props {
  specId: string;
  /** Test seam threaded through to all API client calls. */
  fetchFn?: typeof fetch;
  /** Test seam threaded through to the log viewer's WebSocket factory. */
  wsFactory?: (url: string) => WebSocket;
}

// ── Tab button ──────────────────────────────────────────────────────

interface TabButtonProps {
  tab: Tab;
  active: Tab;
  disabled?: boolean;
  onClick: (tab: Tab) => void;
  label: string;
}

function TabButton(props: TabButtonProps) {
  const { tab, active, disabled, onClick, label } = props;
  const isActive = active === tab;
  return (
    <button
      type="button"
      role="tab"
      aria-selected={isActive}
      aria-controls={`tab-panel-${tab}`}
      disabled={disabled}
      onClick={() => onClick(tab)}
      data-testid={`tab-${tab}`}
      data-active={isActive ? 'true' : 'false'}
      className={[
        'px-3 py-2 text-sm font-medium border-b-2 transition-colors',
        isActive
          ? 'border-blue-500 text-blue-700'
          : 'border-transparent text-gray-500 hover:text-gray-700',
        disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer',
      ].join(' ')}
    >
      {label}
    </button>
  );
}

// ── Verdict rows table ──────────────────────────────────────────────

function VerdictBadge({ verdict }: { verdict: string }) {
  const cls =
    verdict === 'accept' ? 'bg-green-100 text-green-800'
      : verdict === 'reject' ? 'bg-red-100 text-red-800'
        : 'bg-yellow-100 text-yellow-800';
  return (
    <span
      data-testid={`verdict-badge-${verdict}`}
      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}
    >
      {verdict}
    </span>
  );
}

function VerdictTable({ verdicts }: { verdicts: TFactoryVerdict[] }) {
  if (verdicts.length === 0) {
    return (
      <p className="p-4 text-sm text-gray-500">No verdicts yet.</p>
    );
  }
  return (
    <table
      data-testid="verdict-table"
      className="w-full text-left text-sm"
    >
      <thead className="border-b border-gray-200 text-xs uppercase text-gray-500">
        <tr>
          <th className="px-3 py-2">Test</th>
          <th className="px-3 py-2">Verdict</th>
          <th className="px-3 py-2">Coverage</th>
          <th className="px-3 py-2">Stability</th>
          <th className="px-3 py-2">Mutation</th>
        </tr>
      </thead>
      <tbody>
        {verdicts.map((v) => (
          <tr
            key={v.test_id}
            data-testid={`verdict-row-${v.test_id}`}
            className="border-b border-gray-100"
          >
            <td className="px-3 py-2 font-mono text-xs">{v.test_id}</td>
            <td className="px-3 py-2">
              <VerdictBadge verdict={v.verdict} />
            </td>
            <td className="px-3 py-2 text-xs">
              {v.signals_summary?.coverage_delta_pct ?? '?'}%
            </td>
            <td className="px-3 py-2 text-xs">
              {v.signals_summary?.stability ?? '?'}
            </td>
            <td className="px-3 py-2 text-xs">
              {v.signals_summary?.mutation ?? '?'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ── Main component ───────────────────────────────────────────────────

export function TFactoryTaskDetail({ specId, fetchFn, wsFactory }: Props) {
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(true);
  const [activeTab, setActiveTab] = useState<Tab>('status');

  const [verdicts, setVerdicts] = useState<TFactoryVerdictsDocument | null>(null);
  const [verdictsError, setVerdictsError] = useState<string | null>(null);
  const [loadingVerdicts, setLoadingVerdicts] = useState(false);
  const [verdictsRequested, setVerdictsRequested] = useState(false);

  const [reportMd, setReportMd] = useState<string | null>(null);
  const [reportError, setReportError] = useState<string | null>(null);
  const [loadingReport, setLoadingReport] = useState(false);
  const [reportRequested, setReportRequested] = useState(false);

  // ── Initial fetch: detail ────────────────────────────────────────

  useEffect(() => {
    let cancelled = false;
    setLoadingDetail(true);
    setDetailError(null);
    getTaskDetail(specId, { fetchFn })
      .then((d) => {
        if (cancelled) return;
        setDetail(d);
        setLoadingDetail(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setDetailError(err instanceof Error ? err.message : String(err));
        setLoadingDetail(false);
      });
    return () => { cancelled = true; };
  }, [specId, fetchFn]);

  // ── Lazy fetch: verdicts (when tab opened) ──────────────────────

  const onSelectTab = useCallback((tab: Tab) => {
    setActiveTab(tab);
    if (tab === 'verdicts' && !verdictsRequested) {
      setVerdictsRequested(true);
      setLoadingVerdicts(true);
      setVerdictsError(null);
      getVerdicts(specId, { fetchFn })
        .then((d) => {
          setVerdicts(d);
          setLoadingVerdicts(false);
        })
        .catch((err: unknown) => {
          if (err instanceof TFactoryApiError && err.status === 404) {
            setVerdictsError('No verdicts yet — task hasn\'t reached the Evaluator.');
          } else {
            setVerdictsError(err instanceof Error ? err.message : String(err));
          }
          setLoadingVerdicts(false);
        });
    }
    if (tab === 'report' && !reportRequested) {
      setReportRequested(true);
      setLoadingReport(true);
      setReportError(null);
      getTriageReportMarkdown(specId, { fetchFn })
        .then((md) => {
          setReportMd(md);
          setLoadingReport(false);
        })
        .catch((err: unknown) => {
          if (err instanceof TFactoryApiError && err.status === 404) {
            setReportError('No report yet — task hasn\'t reached the Triager.');
          } else {
            setReportError(err instanceof Error ? err.message : String(err));
          }
          setLoadingReport(false);
        });
    }
  }, [specId, fetchFn, verdictsRequested, reportRequested]);

  // ── Loading / error of the primary detail fetch ─────────────────

  if (loadingDetail) {
    return (
      <div role="status" className="flex items-center gap-2 p-6 text-gray-500">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        <span>Loading task…</span>
      </div>
    );
  }

  if (detailError) {
    return (
      <div role="alert" className="flex items-center gap-2 p-6 text-red-700">
        <AlertTriangle className="h-4 w-4" aria-hidden />
        <span>{detailError}</span>
      </div>
    );
  }

  if (!detail) {
    return (
      <div role="alert" className="p-6 text-red-700">No detail returned.</div>
    );
  }

  // ── Render ───────────────────────────────────────────────────────

  const status = (detail.status_json.status as string | undefined) ?? null;
  const verdictsAvailable = detail.artefacts.verdicts?.exists ?? false;
  const reportAvailable = detail.artefacts.triage_report_md?.exists ?? false;

  // Derive per-lane statuses from status_json.lane_progress (v0.2) or
  // fall back to { unit: status } for v0.1 workspaces that don't have
  // a lane_progress field yet.
  const laneProgress = detail.status_json.lane_progress as
    | Record<string, string | null>
    | undefined;
  const laneStatuses = laneProgress
    ? (laneProgress as Record<string, string | null>)
    : { unit: status };

  return (
    <div data-testid="tfactory-task-detail" className="flex flex-col gap-4">
      <header className="flex items-baseline gap-2 border-b border-gray-200 pb-3">
        <h2 className="text-lg font-semibold">{detail.spec_id}</h2>
        <span className="text-xs text-gray-500">project {detail.project_id}</span>
      </header>

      <div role="tablist" className="flex border-b border-gray-200">
        <TabButton tab="status" active={activeTab} onClick={onSelectTab} label="Status" />
        <TabButton tab="lanes" active={activeTab} onClick={onSelectTab} label="Lanes" />
        <TabButton
          tab="verdicts" active={activeTab} onClick={onSelectTab}
          label="Verdicts" disabled={!verdictsAvailable}
        />
        <TabButton
          tab="report" active={activeTab} onClick={onSelectTab}
          label="Report" disabled={!reportAvailable}
        />
        <TabButton
          tab="logs" active={activeTab} onClick={onSelectTab}
          label="Logs"
        />
      </div>

      <div
        role="tabpanel"
        id={`tab-panel-${activeTab}`}
        data-testid={`panel-${activeTab}`}
      >
        {activeTab === 'status' && (
          <pre className="overflow-auto rounded bg-gray-50 p-3 text-xs">
            {JSON.stringify(detail.status_json, null, 2)}
          </pre>
        )}
        {activeTab === 'lanes' && <LaneStatusGrid laneStatuses={laneStatuses} />}
        {activeTab === 'verdicts' && (
          <>
            {loadingVerdicts && (
              <div role="status" className="p-4 text-sm text-gray-500">
                Loading verdicts…
              </div>
            )}
            {verdictsError && (
              <div role="alert" className="p-4 text-sm text-red-600">
                {verdictsError}
              </div>
            )}
            {verdicts && <VerdictTable verdicts={verdicts.verdicts} />}
          </>
        )}
        {activeTab === 'report' && (
          <>
            {loadingReport && (
              <div role="status" className="p-4 text-sm text-gray-500">
                Loading report…
              </div>
            )}
            {reportError && (
              <div role="alert" className="p-4 text-sm text-red-600">
                {reportError}
              </div>
            )}
            {reportMd && (
              <pre
                data-testid="report-md-content"
                className="overflow-auto whitespace-pre-wrap p-3 text-sm"
              >
                {reportMd}
              </pre>
            )}
          </>
        )}
        {activeTab === 'logs' && (
          <TFactoryLogViewer specId={specId} wsFactory={wsFactory} />
        )}
      </div>
    </div>
  );
}
