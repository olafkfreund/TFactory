import '@testing-library/jest-dom/vitest';

// jsdom has no ResizeObserver; components (e.g. TFactoryPipelineBoard) mount
// one to re-measure on resize. A no-op stub keeps them renderable under test.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver;
