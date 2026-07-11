/**
 * @vitest-environment jsdom
 */
/**
 * OnboardingWizard integration tests (#652)
 *
 * Exercises the current provider-choice step machine:
 *   welcome -> provider-choice -> claude | openai_compat | skip paths.
 * The react-i18next mock returns raw keys, so assertions use translation
 * keys and data-testids rather than English copy.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { OnboardingWizard } from './OnboardingWizard';

// Mock react-i18next: t returns the key so tests assert on stable keys.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'en' }
  }),
  Trans: ({ children }: { children: React.ReactNode }) => children
}));

// Mock the settings store
const mockUpdateSettings = vi.fn();

vi.mock('../../stores/settings-store', () => ({
  useSettingsStore: vi.fn((selector) => {
    const state = {
      settings: { onboardingCompleted: false },
      isLoading: false,
      updateSettings: mockUpdateSettings
    };
    if (!selector) return state;
    return selector(state);
  })
}));

// Mock window.API (the wizard and its steps talk to the backend through it)
const mockSaveSettings = vi.fn();
const mockCheckClaudeCredentialsExist = vi.fn();
const mockImportClaudeCredentials = vi.fn();
const mockCheckClaudeCodeVersion = vi.fn();

Object.defineProperty(window, 'API', {
  value: {
    saveSettings: mockSaveSettings,
    checkClaudeCredentialsExist: mockCheckClaudeCredentialsExist,
    importClaudeCredentials: mockImportClaudeCredentials,
    checkClaudeCodeVersion: mockCheckClaudeCodeVersion
  },
  writable: true
});

// Helpers to drive the wizard
function goToProviderChoice() {
  fireEvent.click(screen.getByText('welcome.getStarted'));
}

describe('OnboardingWizard', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn()
  };

  beforeEach(() => {
    vi.clearAllMocks();
    mockSaveSettings.mockResolvedValue({ success: true });
    mockCheckClaudeCredentialsExist.mockResolvedValue({
      success: true,
      data: { exists: true }
    });
    mockImportClaudeCredentials.mockResolvedValue({ success: true });
    mockCheckClaudeCodeVersion.mockResolvedValue({
      success: true,
      data: { installed: true, isOutdated: false, version: '2.0.0' }
    });
  });

  describe('Welcome Step', () => {
    it('shows the welcome step when opened', () => {
      render(<OnboardingWizard {...defaultProps} />);

      expect(screen.getByText('welcome.title')).toBeInTheDocument();
      expect(screen.getByText('welcome.getStarted')).toBeInTheDocument();
      expect(screen.getByText('welcome.skip')).toBeInTheDocument();
    });

    it('does not render when closed', () => {
      render(<OnboardingWizard {...defaultProps} open={false} />);

      expect(screen.queryByText('welcome.title')).not.toBeInTheDocument();
    });

    it('does not show the progress indicator on the welcome step', () => {
      render(<OnboardingWizard {...defaultProps} />);

      expect(screen.queryByText('steps.welcome')).not.toBeInTheDocument();
      expect(screen.queryByText('steps.providerChoice')).not.toBeInTheDocument();
    });

    it('skip on welcome completes onboarding and closes the wizard', async () => {
      const onOpenChange = vi.fn();
      render(<OnboardingWizard {...defaultProps} onOpenChange={onOpenChange} />);

      fireEvent.click(screen.getByText('welcome.skip'));

      await waitFor(() => {
        expect(mockSaveSettings).toHaveBeenCalledWith({ onboardingCompleted: true });
      });
      expect(mockUpdateSettings).toHaveBeenCalledWith({ onboardingCompleted: true });
      expect(onOpenChange).toHaveBeenCalledWith(false);
    });
  });

  describe('Provider Choice Step', () => {
    it('navigates welcome -> provider-choice and shows all three provider cards', async () => {
      render(<OnboardingWizard {...defaultProps} />);

      goToProviderChoice();

      await waitFor(() => {
        expect(screen.getByText('providerChoice.title')).toBeInTheDocument();
      });
      expect(screen.getByTestId('provider-choice-claude')).toBeInTheDocument();
      expect(screen.getByTestId('provider-choice-openai-compat')).toBeInTheDocument();
      expect(screen.getByTestId('provider-choice-skip')).toBeInTheDocument();
    });

    it('shows the progress indicator on the provider-choice step', async () => {
      render(<OnboardingWizard {...defaultProps} />);

      goToProviderChoice();

      await waitFor(() => {
        expect(screen.getByText('steps.providerChoice')).toBeInTheDocument();
      });
      expect(screen.getByText('steps.welcome')).toBeInTheDocument();
    });

    it('back returns to the welcome step', async () => {
      render(<OnboardingWizard {...defaultProps} />);

      goToProviderChoice();
      await waitFor(() => {
        expect(screen.getByText('providerChoice.title')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText('common:back'));

      await waitFor(() => {
        expect(screen.getByText('welcome.getStarted')).toBeInTheDocument();
      });
      expect(screen.queryByText('providerChoice.title')).not.toBeInTheDocument();
    });
  });

  describe('Claude Path', () => {
    it('choosing Claude goes to import-credentials when credentials exist', async () => {
      render(<OnboardingWizard {...defaultProps} />);

      goToProviderChoice();
      await waitFor(() => {
        expect(screen.getByTestId('provider-choice-claude')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByTestId('provider-choice-claude'));

      await waitFor(() => {
        expect(screen.getByText('importCredentials.title')).toBeInTheDocument();
      });
      expect(mockCheckClaudeCredentialsExist).toHaveBeenCalled();
      // Claude path extends the progress steps
      expect(screen.getByText('steps.importCredentials')).toBeInTheDocument();
      expect(screen.getByText('steps.claudeCode')).toBeInTheDocument();
    });

    it('auto-advances to the claude-code step when no credentials exist', async () => {
      mockCheckClaudeCredentialsExist.mockResolvedValue({
        success: true,
        data: { exists: false }
      });
      render(<OnboardingWizard {...defaultProps} />);

      goToProviderChoice();
      await waitFor(() => {
        expect(screen.getByTestId('provider-choice-claude')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByTestId('provider-choice-claude'));

      // ImportCredentialsStep finds nothing and calls onNext -> claude-code step
      await waitFor(() => {
        expect(mockCheckClaudeCodeVersion).toHaveBeenCalled();
      });
      expect(screen.queryByText('importCredentials.title')).not.toBeInTheDocument();
    });

    it('importing credentials jumps straight to completion', async () => {
      render(<OnboardingWizard {...defaultProps} />);

      goToProviderChoice();
      await waitFor(() => {
        expect(screen.getByTestId('provider-choice-claude')).toBeInTheDocument();
      });
      fireEvent.click(screen.getByTestId('provider-choice-claude'));
      await waitFor(() => {
        expect(screen.getByText('importCredentials.importButton')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText('importCredentials.importButton'));

      await waitFor(() => {
        expect(mockImportClaudeCredentials).toHaveBeenCalled();
      });
      // Import success auto-advances to completion after a 1.5s delay
      await waitFor(
        () => {
          expect(screen.getByText('completion.title')).toBeInTheDocument();
        },
        { timeout: 3000 }
      );
    });
  });

  describe('OpenAI Compatible Path', () => {
    it('choosing OpenAI Compatible goes to the setup step', async () => {
      render(<OnboardingWizard {...defaultProps} />);

      goToProviderChoice();
      await waitFor(() => {
        expect(screen.getByTestId('provider-choice-openai-compat')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByTestId('provider-choice-openai-compat'));

      await waitFor(() => {
        expect(screen.getByText('openaiCompatSetup.title')).toBeInTheDocument();
      });
      // OpenAI path: welcome, provider-choice, setup, done
      expect(screen.getByText('steps.openaiCompatSetup')).toBeInTheDocument();
      expect(screen.queryByText('steps.claudeCode')).not.toBeInTheDocument();
    });

    it('skipping the setup step reaches completion', async () => {
      render(<OnboardingWizard {...defaultProps} />);

      goToProviderChoice();
      await waitFor(() => {
        expect(screen.getByTestId('provider-choice-openai-compat')).toBeInTheDocument();
      });
      fireEvent.click(screen.getByTestId('provider-choice-openai-compat'));
      await waitFor(() => {
        expect(screen.getByText('openaiCompatSetup.skip')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText('openaiCompatSetup.skip'));

      await waitFor(() => {
        expect(screen.getByText('completion.title')).toBeInTheDocument();
      });
    });
  });

  describe('Skip Path and Completion', () => {
    it('choosing Skip goes directly to completion', async () => {
      render(<OnboardingWizard {...defaultProps} />);

      goToProviderChoice();
      await waitFor(() => {
        expect(screen.getByTestId('provider-choice-skip')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByTestId('provider-choice-skip'));

      await waitFor(() => {
        expect(screen.getByText('completion.title')).toBeInTheDocument();
      });
    });

    it('finish on completion saves onboardingCompleted and closes the wizard', async () => {
      const onOpenChange = vi.fn();
      render(<OnboardingWizard {...defaultProps} onOpenChange={onOpenChange} />);

      goToProviderChoice();
      await waitFor(() => {
        expect(screen.getByTestId('provider-choice-skip')).toBeInTheDocument();
      });
      fireEvent.click(screen.getByTestId('provider-choice-skip'));
      await waitFor(() => {
        expect(screen.getByText('completion.finish')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText('completion.finish'));

      await waitFor(() => {
        expect(mockSaveSettings).toHaveBeenCalledWith({ onboardingCompleted: true });
      });
      expect(mockUpdateSettings).toHaveBeenCalledWith({ onboardingCompleted: true });
      expect(onOpenChange).toHaveBeenCalledWith(false);
    });
  });
});
