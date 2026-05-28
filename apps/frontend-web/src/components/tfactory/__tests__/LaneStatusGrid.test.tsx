/**
 * @vitest-environment jsdom
 *
 * Tests for <LaneStatusGrid> — Task 10 (#11) commit 3.
 *
 * Presentational component; no fetching. Tests assert the visual
 * state mapping + placeholder semantics for the not-yet-implemented
 * lanes.
 */

import { describe, it, expect } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { render, screen } from '@testing-library/react';

import {
  LANES,
  LaneStatusGrid,
  functionalLaneState,
} from '../LaneStatusGrid';

// ── functionalLaneState mapping ───────────────────────────────────────

describe('functionalLaneState', () => {
  it.each([
    ['triaged', 'success'],
    ['evaluated', 'success'],
    ['evaluator_failed', 'failure'],
    ['triager_failed', 'failure'],
    ['stuck', 'failure'],
    ['planning', 'in_flight'],
    ['generating', 'in_flight'],
    ['evaluating', 'in_flight'],
    ['triaging', 'in_flight'],
    ['triaged_empty', 'warning'],
    ['evaluated_empty', 'warning'],
    ['pending', 'idle'],
  ])('maps %s → %s', (status, expected) => {
    expect(functionalLaneState(status)).toBe(expected);
  });

  it('null status → idle', () => {
    expect(functionalLaneState(null)).toBe('idle');
  });
});

// ── LANES table ──────────────────────────────────────────────────────

describe('LANES definition', () => {
  it('declares 5 lanes in roadmap order', () => {
    expect(LANES.map((l) => l.id)).toEqual([
      'functional', 'sast', 'dast', 'fuzz', 'mutation',
    ]);
  });

  it('assigns Phase 1-5 in order', () => {
    expect(LANES.map((l) => l.phase)).toEqual([1, 2, 3, 4, 5]);
  });
});

// ── Grid rendering ────────────────────────────────────────────────────

describe('<LaneStatusGrid>', () => {
  it('renders all five lane cards', () => {
    render(<LaneStatusGrid functionalStatus="triaged" />);
    for (const lane of LANES) {
      expect(screen.getByTestId(`lane-card-${lane.id}`)).toBeInTheDocument();
    }
  });

  it('lights the functional lane with the given status', () => {
    render(<LaneStatusGrid functionalStatus="triaged" />);
    const card = screen.getByTestId('lane-card-functional');
    expect(card).toHaveAttribute('data-lane-state', 'success');
    const detail = screen.getByTestId('lane-functional-detail');
    expect(detail.textContent).toBe('triaged');
  });

  it.each([
    ['evaluating', 'in_flight'],
    ['triager_failed', 'failure'],
    ['evaluated_empty', 'warning'],
    ['pending', 'idle'],
  ])('functional state for %s → %s', (status, expectedState) => {
    render(<LaneStatusGrid functionalStatus={status} />);
    expect(screen.getByTestId('lane-card-functional')).toHaveAttribute(
      'data-lane-state', expectedState,
    );
  });

  it('null functionalStatus → idle state', () => {
    render(<LaneStatusGrid functionalStatus={null} />);
    expect(screen.getByTestId('lane-card-functional')).toHaveAttribute(
      'data-lane-state', 'idle',
    );
  });

  it('renders placeholder text for sast/dast/fuzz/mutation', () => {
    render(<LaneStatusGrid functionalStatus="triaged" />);
    for (const id of ['sast', 'dast', 'fuzz', 'mutation'] as const) {
      const card = screen.getByTestId(`lane-card-${id}`);
      expect(card).toHaveAttribute('data-lane-state', 'placeholder');
      const placeholder = screen.getByTestId(`lane-${id}-placeholder`);
      expect(placeholder.textContent).toMatch(/Coming in Phase \d/);
    }
  });

  it('placeholder text includes the correct phase number', () => {
    render(<LaneStatusGrid functionalStatus="triaged" />);
    expect(
      screen.getByTestId('lane-sast-placeholder').textContent,
    ).toBe('Coming in Phase 2');
    expect(
      screen.getByTestId('lane-dast-placeholder').textContent,
    ).toBe('Coming in Phase 3');
    expect(
      screen.getByTestId('lane-fuzz-placeholder').textContent,
    ).toBe('Coming in Phase 4');
    expect(
      screen.getByTestId('lane-mutation-placeholder').textContent,
    ).toBe('Coming in Phase 5');
  });

  it('default prop (no functionalStatus) → idle state', () => {
    render(<LaneStatusGrid />);
    expect(screen.getByTestId('lane-card-functional')).toHaveAttribute(
      'data-lane-state', 'idle',
    );
  });
});
