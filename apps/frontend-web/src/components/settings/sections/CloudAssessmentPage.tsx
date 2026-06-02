/**
 * Cloud Assessment viewer (#133/#140).
 *
 * Renders the latest cloud assessment the TFactory cloud task-write (#138)
 * produced — the verdict, the report (Markdown), and the Mermaid service
 * topology — fetched from GET /api/cloud/assessment.
 */
import { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { Loader2, ShieldAlert, ShieldCheck, ShieldQuestion } from 'lucide-react';
import { get } from '../../../lib/api-client';
import { SettingsSection } from '../SettingsSection';
import { MermaidDiagram } from './MermaidDiagram';

interface Assessment {
  present: boolean;
  json?: {
    provider?: string;
    account?: string;
    verdict?: string;
    failed?: number;
    passed?: number;
    fail_counts?: Record<string, number>;
  };
  reportMarkdown?: string;
  diagramMermaid?: string;
}

function VerdictBadge({ verdict }: { verdict?: string }) {
  const v = (verdict || '').toLowerCase();
  if (v === 'reject') {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-destructive/15 px-3 py-1 text-sm font-semibold text-destructive">
        <ShieldAlert className="h-4 w-4" /> REJECT
      </span>
    );
  }
  if (v === 'flag') {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-warning/15 px-3 py-1 text-sm font-semibold text-warning">
        <ShieldQuestion className="h-4 w-4" /> FLAG
      </span>
    );
  }
  if (v === 'accept') {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-success/15 px-3 py-1 text-sm font-semibold text-success">
        <ShieldCheck className="h-4 w-4" /> ACCEPT
      </span>
    );
  }
  return null;
}

export function CloudAssessmentPage() {
  const [data, setData] = useState<Assessment | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const res = await get<Assessment>('/cloud/assessment');
      if (!cancelled) {
        setData(res.success && res.data ? res.data : { present: false });
        setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <SettingsSection
      title="Cloud Assessment"
      description="Latest cloud misconfiguration assessment — verdict, findings, and service topology."
    >
      {loading ? (
        <div className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading assessment…
        </div>
      ) : !data?.present ? (
        <div className="rounded-lg border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
          No cloud assessment yet. Run a cloud target (AWS/Azure/GCP) to produce a
          report — it lands in <code>findings/cloud_assessment.md</code> and shows here.
        </div>
      ) : (
        <div className="space-y-5">
          <div className="flex flex-wrap items-center gap-3">
            <VerdictBadge verdict={data.json?.verdict} />
            <span className="text-sm text-muted-foreground">
              {data.json?.provider?.toUpperCase()} · account {data.json?.account} ·{' '}
              🔴 {data.json?.failed ?? 0} fail · ✅ {data.json?.passed ?? 0} pass
            </span>
          </div>

          {data.diagramMermaid && (
            <div>
              <h4 className="mb-2 text-sm font-semibold text-foreground">Service topology</h4>
              <MermaidDiagram source={data.diagramMermaid} />
            </div>
          )}

          {data.reportMarkdown && (
            <div className="prose prose-sm dark:prose-invert max-w-none rounded-lg border border-border p-4">
              <ReactMarkdown>{data.reportMarkdown}</ReactMarkdown>
            </div>
          )}
        </div>
      )}
    </SettingsSection>
  );
}
