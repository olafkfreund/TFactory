/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '../../shared/i18n';
import { AddProjectModal } from '../AddProjectModal';

vi.mock('../../stores/project-store', () => ({
  addProject: vi.fn(),
  addProjectFromGitUrl: vi.fn(),
}));

describe('AddProjectModal', () => {
  const discoverProjects = vi.fn();

  beforeEach(() => {
    discoverProjects.mockReset();
    discoverProjects.mockResolvedValue({
      success: true,
      data: [
        {
          name: 'discovered-repo',
          path: '/home/user/discovered-repo',
          has_git: true,
          has_package_json: true,
          has_requirements: false,
          has_tfactory: false,
          has_claude_md: true,
        },
      ],
    });
    (window as unknown as { API: Record<string, unknown> }).API = {
      discoverProjects,
    };
  });

  it('defaults to clone mode without scanning on open, scans on Local choice, and resets on reopen', async () => {
    const onOpenChange = vi.fn();
    const { rerender } = render(<AddProjectModal open={true} onOpenChange={onOpenChange} />);

    // Defaults to clone mode; no scan on open
    const gitUrlInput = screen.getByLabelText('Git repository URL');
    expect(gitUrlInput).toBeInTheDocument();
    expect(discoverProjects).not.toHaveBeenCalled();

    fireEvent.change(gitUrlInput, { target: { value: 'https://github.com/acme/demo.git' } });
    expect(gitUrlInput).toHaveValue('https://github.com/acme/demo.git');

    // Choosing Local folder scans once and preserves local mode when folder path changes
    fireEvent.click(screen.getByRole('button', { name: 'Local folder' }));
    await waitFor(() => {
      expect(discoverProjects).toHaveBeenCalledTimes(1);
    });
    expect(await screen.findByText('discovered-repo')).toBeInTheDocument();

    const folderInput = screen.getByLabelText('Projects Folder');
    fireEvent.change(folderInput, { target: { value: '/custom/root' } });
    expect(folderInput).toHaveValue('/custom/root');
    // Editing folder path while open does not reset back to clone mode
    expect(screen.queryByLabelText('Git repository URL')).toBeNull();

    // Closing and reopening resets to clone mode without scanning
    discoverProjects.mockClear();
    rerender(<AddProjectModal open={false} onOpenChange={onOpenChange} />);
    rerender(<AddProjectModal open={true} onOpenChange={onOpenChange} />);

    const reopenedGitUrlInput = screen.getByLabelText('Git repository URL');
    expect(reopenedGitUrlInput).toHaveValue('');
    expect(discoverProjects).not.toHaveBeenCalled();
  });
});
