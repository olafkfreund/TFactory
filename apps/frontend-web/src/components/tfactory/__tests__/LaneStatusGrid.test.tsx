/**
 * @vitest-environment jsdom
 *
 * Tests for <LaneStatusGrid> — Task 10 (#11) commit 3; reskinned in Task 0
 * (#16) commit 3 for the v0.2 lane spine (unit/browser/api/integration/mutation).
 *
 * Presentational component; no fetching. Tests assert the visual
 * state mapping + placeholder semantics for the not-yet-lit lanes.
 */

import { describe, it, expect } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { render, screen } from '@testing-library/react';

import {
  LANES,
  LaneStatusGrid,
  unitLaneState,
} from '../LaneStatusGrid';

// ── unitLaneState mapping ─────────────────────────────────────────────

describe('unitLaneState', () => {
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
    expect(unitLaneState(status)).toBe(expected);
  });

  it('null status → idle', () => {
    expect(unitLaneState(null)).toBe('idle');
  });
});

// ── LANES table ──────────────────────────────────────────────────────

describe('LANES definition', () => {
  it('declares 5 v0.2 lanes in roadmap order', () => {
    expect(LANES.map((l) => l.id)).toEqual([
      'unit', 'browser', 'api', 'integration', 'mutation',
    ]);
  });

  it('assigns Phase 1-5 in order', () => {
    expect(LANES.map((l) => l.phase)).toEqual([1, 2, 3, 4, 5]);
  });

  it('uses the v0.2 modality labels', () => {
    expect(LANES.map((l) => l.label)).toEqual([
      'Unit', 'Browser', 'API', 'Integration', 'Mutation',
    ]);
  });
});

// ── Grid rendering ────────────────────────────────────────────────────

describe('<LaneStatusGrid>', () => {
  it('renders all five lane cards', () => {
    render(<LaneStatusGrid unitStatus="triaged" />);
    for (const lane of LANES) {
      expect(screen.getByTestId(`lane-card-${lane.id}`)).toBeInTheDocument();
    }
  });

  it('lights the unit lane with the given status', () => {
    render(<LaneStatusGrid unitStatus="triaged" />);
    const card = screen.getByTestId('lane-card-unit');
    expect(card).toHaveAttribute('data-lane-state', 'success');
    const detail = screen.getByTestId('lane-unit-detail');
    expect(detail.textContent).toBe('triaged');
  });

  it.each([
    ['evaluating', 'in_flight'],
    ['triager_failed', 'failure'],
    ['evaluated_empty', 'warning'],
    ['pending', 'idle'],
  ])('unit state for %s → %s', (status, expectedState) => {
    render(<LaneStatusGrid unitStatus={status} />);
    expect(screen.getByTestId('lane-card-unit')).toHaveAttribute(
      'data-lane-state', expectedState,
    );
  });

  it('null unitStatus → idle state', () => {
    render(<LaneStatusGrid unitStatus={null} />);
    expect(screen.getByTestId('lane-card-unit')).toHaveAttribute(
      'data-lane-state', 'idle',
    );
  });

  it('renders placeholder text for browser/api/integration/mutation', () => {
    render(<LaneStatusGrid unitStatus="triaged" />);
    for (const id of ['browser', 'api', 'integration', 'mutation'] as const) {
      const card = screen.getByTestId(`lane-card-${id}`);
      expect(card).toHaveAttribute('data-lane-state', 'placeholder');
      const placeholder = screen.getByTestId(`lane-${id}-placeholder`);
      expect(placeholder.textContent).toMatch(/Coming in Phase \d/);
    }
  });

  it('placeholder text includes the correct phase number', () => {
    render(<LaneStatusGrid unitStatus="triaged" />);
    expect(
      screen.getByTestId('lane-browser-placeholder').textContent,
    ).toBe('Coming in Phase 2');
    expect(
      screen.getByTestId('lane-api-placeholder').textContent,
    ).toBe('Coming in Phase 3');
    expect(
      screen.getByTestId('lane-integration-placeholder').textContent,
    ).toBe('Coming in Phase 4');
    expect(
      screen.getByTestId('lane-mutation-placeholder').textContent,
    ).toBe('Coming in Phase 5');
  });

  it('default prop (no unitStatus) → idle state', () => {
    render(<LaneStatusGrid />);
    expect(screen.getByTestId('lane-card-unit')).toHaveAttribute(
      'data-lane-state', 'idle',
    );
  });
});
