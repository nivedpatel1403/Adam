import { createContext, useContext } from 'react';
import type { ArchiveItem, Page, Sandbox } from './api';

export interface Workspace {
  sandbox: Sandbox | null; sandboxError: string | null; archive: Page<ArchiveItem> | null;
  archiveError: string | null; refresh: () => void;
}
export const WorkspaceContext = createContext<Workspace | null>(null);
export function useWorkspace() { return useContext(WorkspaceContext)!; }
