/**
 * Visual-baseline viewer + accept flow (#109).
 *
 * Surfaces the stored visual-regression baselines for a target (the backend
 * `agents/evidence/visual_baseline` store, exposed by the #160 portal API) so
 * an operator can:
 *   - see the current baseline images for a target, and
 *   - promote a freshly-captured screenshot to the baseline (accept/update).
 *
 * Self-contained + prop-driven so it unit-tests with an injected `fetchFn`.
 * Theme colour comes from the Gruvbox tokens, matching the rest of the portal.
 */

import { useCallback, useState } from 'react';
import { Loader2, ImageOff } from 'lucide-react';

import {
  TFactoryApiError,
  acceptVisualBaseline,
  listVisualBaselines,
  visualBaselineImageUrl,
  type VisualBaselineEntry,
} from '../../lib/tfactory-api';

/** A captured screenshot the operator can promote to a baseline. */
export interface CapturedScreenshot {
  name: string;
  /** Workspace-relative path the accept endpoint takes as `source`. */
  path: string;
}

interface Props {
  specId: string;
  /** Pre-select a target (else the operator types one). */
  initialTarget?: string;
  /** Captures offered in the "set as baseline" control. */
  captures?: CapturedScreenshot[];
  /** Injected for tests. */
  fetchFn?: typeof fetch;
}

export function VisualBaselines({ specId, initialTarget = '', captures = [], fetchFn }: Props) {
  const [target, setTarget] = useState(initialTarget);
  const [loaded, setLoaded] = useState<string | null>(null);
  const [baselines, setBaselines] = useState<VisualBaselineEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (t: string) => {
      const name = t.trim();
      if (!name) return;
      setLoading(true);
      setError(null);
      try {
        const doc = await listVisualBaselines(specId, name, { fetchFn });
        setBaselines(doc.baselines);
        setLoaded(name);
      } catch (e) {
        setError(e instanceof TFactoryApiError ? e.message : 'Failed to load baselines');
        setBaselines([]);
        setLoaded(name);
      } finally {
        setLoading(false);
      }
    },
    [specId, fetchFn],
  );

  return (
    <div data-testid="visual-baselines" className="rounded-lg border border-border bg-card p-3 space-y-3">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-medium text-foreground">Visual baselines</h4>
        {loaded && (
          <span data-testid="vb-count" className="text-xs text-muted-foreground">
            {baselines.length} for <code>{loaded}</code>
          </span>
        )}
      </div>

      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void load(target);
        }}
      >
        <input
          data-testid="vb-target-input"
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          placeholder="target name (e.g. storefront)"
          className="flex-1 rounded-md border border-border bg-background px-2 py-1 text-sm text-foreground"
        />
        <button
          type="submit"
          data-testid="vb-load"
          disabled={loading || !target.trim()}
          className="rounded-md border border-border bg-background px-3 py-1 text-sm text-foreground disabled:opacity-50"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Load'}
        </button>
      </form>

      {error && (
        <p data-testid="vb-error" className="text-sm text-destructive">{error}</p>
      )}

      {loaded && !loading && !error && baselines.length === 0 && (
        <p data-testid="vb-empty" className="flex items-center gap-2 text-sm text-muted-foreground">
          <ImageOff className="h-4 w-4" /> No baselines stored for <code>{loaded}</code> yet.
        </p>
      )}

      {baselines.length > 0 && (
        <div data-testid="vb-grid" className="flex flex-wrap gap-3">
          {baselines.map((b) => (
            <figure key={b.snapshot} data-testid={`vb-item-${b.snapshot}`} className="space-y-1">
              <img
                src={visualBaselineImageUrl(specId, loaded ?? target, b.snapshot)}
                alt={`baseline ${b.snapshot}`}
                data-testid={`vb-img-${b.snapshot}`}
                className="h-32 w-auto rounded border border-border bg-background object-contain"
              />
              <figcaption className="text-xs text-muted-foreground">
                {b.snapshot} · {(b.sizeBytes / 1024).toFixed(1)} KB
              </figcaption>
            </figure>
          ))}
        </div>
      )}

      {loaded && !error && (
        <AcceptControl
          specId={specId}
          target={loaded}
          captures={captures}
          fetchFn={fetchFn}
          onAccepted={() => void load(loaded)}
        />
      )}
    </div>
  );
}

function AcceptControl({
  specId,
  target,
  captures,
  fetchFn,
  onAccepted,
}: {
  specId: string;
  target: string;
  captures: CapturedScreenshot[];
  fetchFn?: typeof fetch;
  onAccepted: () => void;
}) {
  const [source, setSource] = useState(captures[0]?.path ?? '');
  const [snapshot, setSnapshot] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const accept = async () => {
    const snap = snapshot.trim();
    if (!source || !snap) return;
    setBusy(true);
    setMsg(null);
    try {
      await acceptVisualBaseline(specId, target, snap, source, { fetchFn });
      setMsg(`Promoted to baseline ${snap}`);
      onAccepted();
    } catch (e) {
      setMsg(e instanceof TFactoryApiError ? e.message : 'Accept failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div data-testid="vb-accept" className="space-y-2 border-t border-border pt-3">
      <p className="text-xs text-muted-foreground">Set a captured screenshot as the baseline:</p>
      <div className="flex flex-wrap items-center gap-2">
        {captures.length > 0 ? (
          <select
            data-testid="vb-source"
            value={source}
            onChange={(e) => setSource(e.target.value)}
            className="rounded-md border border-border bg-background px-2 py-1 text-sm text-foreground"
          >
            {captures.map((c) => (
              <option key={c.path} value={c.path}>{c.name}</option>
            ))}
          </select>
        ) : (
          <input
            data-testid="vb-source"
            value={source}
            onChange={(e) => setSource(e.target.value)}
            placeholder="captured screenshot path (workspace-relative)"
            className="rounded-md border border-border bg-background px-2 py-1 text-sm text-foreground"
          />
        )}
        <input
          data-testid="vb-snapshot"
          value={snapshot}
          onChange={(e) => setSnapshot(e.target.value)}
          placeholder="baseline name (e.g. homepage.png)"
          className="rounded-md border border-border bg-background px-2 py-1 text-sm text-foreground"
        />
        <button
          type="button"
          data-testid="vb-accept-btn"
          onClick={() => void accept()}
          disabled={busy || !source || !snapshot.trim()}
          className="rounded-md border border-border bg-background px-3 py-1 text-sm text-foreground disabled:opacity-50"
        >
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Set as baseline'}
        </button>
      </div>
      {msg && <p data-testid="vb-accept-msg" className="text-xs text-muted-foreground">{msg}</p>}
    </div>
  );
}
