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
  evidenceArtifactUrl,
  getTaskDetail,
  getTriageReportMarkdown,
  getVerdicts,
  type EvidenceUrls,
  type TFactoryTaskDetail as TaskDetail,
  type TFactoryVerdict,
  type TFactoryVerdictsDocument,
} from '../../lib/tfactory-api';
import { LaneStatusGrid } from './LaneStatusGrid';
import { TFactoryLogViewer } from './TFactoryLogViewer';

type Tab = 'status' | 'lanes' | 'verdicts' | 'report' | 'logs' | 'evidence';

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

// ── Evidence panel components ───────────────────────────────────────

interface EvidenceTestRowProps {
  specId: string;
  testId: string;
  urls: EvidenceUrls;
}

function EvidenceTestRow({ specId, testId, urls }: EvidenceTestRowProps) {
  const screenshots: string[] = Array.isArray(urls.screenshots)
    ? urls.screenshots
    : typeof urls.screenshots === 'string'
      ? [urls.screenshots]
      : [];
  const videoUrl = typeof urls.video === 'string' ? urls.video : null;
  const traceUrl = typeof urls.trace === 'string' ? urls.trace : null;
  const networkUrl = typeof urls.network === 'string' ? urls.network : null;

  // Build full artifact URL from the relative portal path
  const buildUrl = (artifactPath: string) => {
    // If the path is already a full portal URL use it directly,
    // otherwise construct from specId + testId
    if (artifactPath.startsWith('/api/')) return artifactPath;
    return evidenceArtifactUrl(specId, testId, artifactPath);
  };

  return (
    <div
      data-testid={`evidence-row-${testId}`}
      className="rounded border border-gray-200 p-3 space-y-2"
    >
      <h4 className="font-mono text-xs font-semibold text-gray-700">{testId}</h4>

      {screenshots.length > 0 && (
        <div data-testid="evidence-screenshots" className="flex flex-wrap gap-2">
          {screenshots.map((url) => {
            const full = buildUrl(url);
            const name = url.split('/').pop() ?? 'screenshot';
            return (
              <a key={full} href={full} target="_blank" rel="noopener noreferrer">
                <img
                  src={full}
                  alt={`Screenshot ${name}`}
                  title={name}
                  className="h-24 w-auto rounded border border-gray-300 object-cover"
                  data-testid={`evidence-screenshot-img-${name}`}
                />
              </a>
            );
          })}
        </div>
      )}

      {videoUrl && (
        <div data-testid="evidence-video">
          {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
          <video
            src={buildUrl(videoUrl)}
            controls
            className="max-h-48 w-full rounded"
            data-testid="evidence-video-player"
          />
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {traceUrl && (
          <a
            href={buildUrl(traceUrl)}
            download
            className="rounded bg-gray-100 px-2 py-1 text-xs text-gray-700 hover:bg-gray-200"
            data-testid="evidence-trace-download"
          >
            Download trace.zip
          </a>
        )}
        {networkUrl && (
          <a
            href={buildUrl(networkUrl)}
            download
            className="rounded bg-gray-100 px-2 py-1 text-xs text-gray-700 hover:bg-gray-200"
            data-testid="evidence-har-download"
          >
            Download network.har
          </a>
        )}
      </div>
    </div>
  );
}

interface EvidenceTabProps {
  specId: string;
  /** Map of test_id → evidence_urls (from verdicts or catalog). */
  evidenceByTest: Record<string, EvidenceUrls>;
}

function EvidenceTab({ specId, evidenceByTest }: EvidenceTabProps) {
  const entries = Object.entries(evidenceByTest);
  if (entries.length === 0) {
    return (
      <p
        data-testid="evidence-empty"
        className="p-4 text-sm text-gray-500"
      >
        No evidence captured yet — evidence is collected after tests run.
      </p>
    );
  }
  return (
    <div data-testid="evidence-panel" className="space-y-3 p-3">
      {entries.map(([testId, urls]) => (
        <EvidenceTestRow
          key={testId}
          specId={specId}
          testId={testId}
          urls={urls}
        />
      ))}
    </div>
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

  // Evidence state — populated lazily when the Evidence tab is selected.
  // The evidence_urls map is built from verdicts.json if already loaded,
  // otherwise we show an empty panel until verdicts are fetched.
  const [evidenceByTest, setEvidenceByTest] = useState<Record<string, EvidenceUrls>>({});

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

  // Helper: extract evidence_urls from a verdicts document
  const _extractEvidenceFromVerdicts = useCallback(
    (doc: TFactoryVerdictsDocument) => {
      const byTest: Record<string, EvidenceUrls> = {};
      for (const v of doc.verdicts) {
        if (v.evidence_urls && Object.keys(v.evidence_urls).length > 0) {
          byTest[v.test_id] = v.evidence_urls;
        }
      }
      setEvidenceByTest(byTest);
    },
    [],
  );

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
          _extractEvidenceFromVerdicts(d);
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
    // Evidence tab: if verdicts already loaded, extract evidence URLs from them
    if (tab === 'evidence' && verdicts && Object.keys(evidenceByTest).length === 0) {
      _extractEvidenceFromVerdicts(verdicts);
    }
  }, [specId, fetchFn, verdictsRequested, reportRequested, verdicts, evidenceByTest, _extractEvidenceFromVerdicts]);

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
  // Evidence tab is enabled when the verdicts artefact exists (evidence is
  // stored alongside verdicts and populated from evidence_urls in each verdict)
  const evidenceAvailable = verdictsAvailable;

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
        <TabButton
          tab="evidence" active={activeTab} onClick={onSelectTab}
          label="Evidence" disabled={!evidenceAvailable}
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
        {activeTab === 'evidence' && (
          <EvidenceTab specId={specId} evidenceByTest={evidenceByTest} />
        )}
      </div>
    </div>
  );
}
