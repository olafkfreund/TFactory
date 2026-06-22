/**
 * Regression surface (RFC-0018 #489) — a project's continuous-regression view.
 *
 * Renders the read-model from `GET /api/projects/{projectId}/regression`
 * (agents.regression.project_regression_summary): the latest verdict, run
 * history, the current regressions/fixes, the flaky/quarantine list, and the
 * coverage trend.
 *
 * Self-contained + prop-driven so it unit-tests with an injected `fetchFn`,
 * matching VisualBaselines and the rest of the portal.
 */

import { useCallback, useEffect, useState } from 'react';
import { Loader2, AlertTriangle, CheckCircle2 } from 'lucide-react';

import {
  TFactoryApiError,
  getRegressionSummary,
  type RegressionSummary,
} from '../../lib/tfactory-api';

interface Props {
  projectId: string;
  /** Injected for tests. */
  fetchFn?: typeof fetch;
}

export function RegressionPanel({ projectId, fetchFn }: Props) {
  const [summary, setSummary] = useState<RegressionSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setSummary(await getRegressionSummary(projectId, { fetchFn }));
    } catch (e) {
      setError(e instanceof TFactoryApiError ? e.message : 'Failed to load regression data');
      setSummary(null);
    } finally {
      setLoading(false);
    }
  }, [projectId, fetchFn]);

  useEffect(() => {
    void load();
  }, [load]);

  const idsOfClass = (cls: string): string[] =>
    summary?.latest_diff
      ? Object.entries(summary.latest_diff.entries)
          .filter(([, c]) => c === cls)
          .map(([tid]) => tid)
          .sort()
      : [];

  return (
    <div
      data-testid="regression-panel"
      className="rounded-lg border border-border bg-card p-3 space-y-3"
    >
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-medium text-foreground">Regression</h4>
        {summary && summary.has_regressions !== null && (
          <span
            data-testid="rp-verdict"
            className={`inline-flex items-center gap-1 text-xs ${
              summary.has_regressions ? 'text-red-400' : 'text-green-400'
            }`}
          >
            {summary.has_regressions ? (
              <>
                <AlertTriangle className="h-3.5 w-3.5" /> regressions detected
              </>
            ) : (
              <>
                <CheckCircle2 className="h-3.5 w-3.5" /> no regressions
              </>
            )}
          </span>
        )}
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> loading…
        </div>
      )}
      {error && <div className="text-xs text-red-400">{error}</div>}

      {summary && !loading && !error && summary.runs.length === 0 && (
        <div data-testid="rp-empty" className="text-xs text-muted-foreground">
          No regression runs yet.
        </div>
      )}

      {summary && !loading && !error && summary.runs.length > 0 && (
        <div className="space-y-3">
          {idsOfClass('regression').length > 0 && (
            <Section title="Regressions" testid="rp-regressions" ids={idsOfClass('regression')} />
          )}
          {idsOfClass('fixed').length > 0 && (
            <Section title="Fixed" testid="rp-fixed" ids={idsOfClass('fixed')} />
          )}
          {summary.quarantined.length > 0 && (
            <div data-testid="rp-quarantined" className="space-y-1">
              <h5 className="text-xs font-medium text-foreground">
                Quarantined ({summary.quarantined.length})
              </h5>
              <ul className="space-y-0.5">
                {summary.quarantined.map((q) => (
                  <li key={q.test_id} className="text-xs text-muted-foreground">
                    <code>{q.test_id}</code> — {q.reason}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div data-testid="rp-history" className="space-y-1">
            <h5 className="text-xs font-medium text-foreground">Run history</h5>
            <ul className="space-y-0.5">
              {summary.runs.map((r) => (
                <li key={r.run_id} className="text-xs text-muted-foreground">
                  <code>{r.run_id}</code> — {r.totals.passed}/{r.totals.total} passed
                  {r.totals.failed > 0 ? `, ${r.totals.failed} failed` : ''}
                  {r.coverage_pct !== null ? `, ${r.coverage_pct.toFixed(1)}% cov` : ''}
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}

function Section({ title, testid, ids }: { title: string; testid: string; ids: string[] }) {
  return (
    <div data-testid={testid} className="space-y-1">
      <h5 className="text-xs font-medium text-foreground">
        {title} ({ids.length})
      </h5>
      <ul className="space-y-0.5">
        {ids.map((tid) => (
          <li key={tid} className="text-xs text-muted-foreground">
            <code>{tid}</code>
          </li>
        ))}
      </ul>
    </div>
  );
}
