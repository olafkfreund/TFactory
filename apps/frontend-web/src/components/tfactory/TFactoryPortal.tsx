/**
 * TFactory portal shell — Task 10 (#11) commit 4.
 *
 * Top-level component that ties the task list to the per-task detail.
 * State-driven (no router) for the MVP — the inherited React app's
 * router stays untouched. A future commit can hoist this into
 * react-router-dom routes once the existing portal's wiring is
 * decoupled.
 *
 * Renders <TFactoryTaskList> by default; on row click, switches to
 * <TFactoryTaskDetail>. A back button returns to the list.
 */

import { useState } from 'react';
import { ChevronLeft } from 'lucide-react';

import { TFactoryPipelineView } from '../pipeline/TFactoryPipelineView';
import { TFactoryTaskDetail } from './TFactoryTaskDetail';

interface Props {
  /** Test seam threaded into both children. */
  fetchFn?: typeof fetch;
  /** Test seam for the log viewer's WebSocket factory. */
  wsFactory?: (url: string) => WebSocket;
}

export function TFactoryPortal({ fetchFn, wsFactory }: Props) {
  const [selectedSpecId, setSelectedSpecId] = useState<string | null>(null);

  if (selectedSpecId === null) {
    return (
      <div data-testid="tfactory-portal" data-view="list" className="h-full">
        <TFactoryPipelineView
          onSelectTask={(specId) => setSelectedSpecId(specId)}
          fetchFn={fetchFn}
        />
      </div>
    );
  }

  return (
    <div data-testid="tfactory-portal" data-view="detail" className="flex flex-col gap-3 h-full overflow-auto p-4">
      <button
        type="button"
        data-testid="portal-back"
        onClick={() => setSelectedSpecId(null)}
        className="self-start inline-flex items-center gap-1 text-sm text-primary hover:text-primary"
      >
        <ChevronLeft className="h-4 w-4" aria-hidden />
        Back to tasks
      </button>
      <TFactoryTaskDetail
        specId={selectedSpecId}
        fetchFn={fetchFn}
        wsFactory={wsFactory}
      />
    </div>
  );
}
