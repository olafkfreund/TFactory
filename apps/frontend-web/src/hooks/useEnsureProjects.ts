import { useEffect, useState } from 'react';
import { loadProjects, useProjectStore } from '../stores/project-store';

export function useEnsureProjects(): boolean {
  const haveProjects = useProjectStore((state) => state.projects.length > 0);
  const [ready, setReady] = useState(haveProjects);

  useEffect(() => {
    if (haveProjects) return;
    let cancelled = false;
    void loadProjects().finally(() => { if (!cancelled) setReady(true); });
    return () => { cancelled = true; };
    // Load once for standalone console routes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return ready || haveProjects;
}
