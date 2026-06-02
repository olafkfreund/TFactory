/**
 * Cloud Assessment viewer (#133/#140).
 *
 * Renders the latest cloud assessment the TFactory cloud task-write (#138)
 * produced — the verdict, the report (Markdown), and the Mermaid service
 * topology — fetched from GET /api/cloud/assessment.
 */
import { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
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
            <div
              className="overflow-auto rounded-lg border border-border p-4 text-sm
                [&_h1]:mb-2 [&_h1]:mt-1 [&_h1]:text-lg [&_h1]:font-bold [&_h1]:text-foreground
                [&_h2]:mb-2 [&_h2]:mt-4 [&_h2]:text-base [&_h2]:font-semibold [&_h2]:text-foreground
                [&_p]:mb-2 [&_p]:text-muted-foreground
                [&_ul]:mb-2 [&_ul]:list-disc [&_ul]:pl-5 [&_ul]:text-muted-foreground
                [&_blockquote]:mb-2 [&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:text-xs [&_blockquote]:text-muted-foreground
                [&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-xs
                [&_table]:my-3 [&_table]:w-full [&_table]:border-collapse [&_table]:text-xs
                [&_thead]:bg-muted/50
                [&_th]:border [&_th]:border-border [&_th]:px-2 [&_th]:py-1 [&_th]:text-left [&_th]:font-semibold
                [&_td]:border [&_td]:border-border [&_td]:px-2 [&_td]:py-1 [&_td]:align-top"
            >
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.reportMarkdown}</ReactMarkdown>
            </div>
          )}
        </div>
      )}
    </SettingsSection>
  );
}
