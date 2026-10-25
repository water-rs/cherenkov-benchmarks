import {test, expect} from 'bun:test';
import {commitPoints, metrics, validHistory, change} from '../docs/history.mjs';

const row = (revision, value, time, valid = true) => ({kind: 'run', metadata: {
  valid, recorded_at: time, source: {revision}}, statistics: {timing_ms: {frame: {p50: value}}}});

test('commit history aggregates independent runs, not pooled frames or rounded values', () => {
  const rows = [row('base', 3, '2026-10-24T10:00:00-04:00'),
    row('head', 2, '2026-10-24T11:00:00-04:00'),
    row('base', 1, '2026-10-24T12:00:00-04:00'),
    row('head', 4, '2026-10-24T13:00:00-04:00')];
  const points = commitPoints(rows, metrics[0]);
  expect(points.map(p => p.revision)).toEqual(['base', 'head']);
  expect(points[0]).toMatchObject({value: 2, low: 1, high: 3, count: 2});
  expect(points[1].value).toBe(3);
  expect(change(3, 2)).toBe(50);
});

test('missing measurements stay missing and failed runs are excluded', () => {
  const points = commitPoints(validHistory([
    row('base', null, '2026-10-24T10:00:00-04:00'),
    row('head', 0, '2026-10-24T11:00:00-04:00'),
    row('failed', 100, '2026-10-24T12:00:00-04:00', false),
  ]), metrics[0]);
  expect(points).toHaveLength(2);
  expect(points[0]).toMatchObject({value: null, low: null, high: null, count: 0});
  expect(points[1].value).toBe(0);
  expect(change(0, null)).toBeNull();
  expect(change(1, 0)).toBeNull();
});

test('GPU rail energy and process-attributed energy remain different metrics', () => {
  const r = {statistics: {energy: {rails: {
    gpu: {subsystem: 'GPU', joules_per_frame: .002},
    cpu: {subsystem: 'CPU', joules_per_frame: .005},
    process: {joules_per_frame: .001},
  }}}};
  expect(metrics.find(m => m.id === 'gpu-energy').value(r)).toBe(2);
  expect(metrics.find(m => m.id === 'process-energy').value(r)).toBe(1);
  expect(metrics.find(m => m.id === 'gpu-energy').value({statistics: {}})).toBeNull();
});
