export const finite = value => typeof value === 'number' && Number.isFinite(value);
export function median(values) {
  const sorted = values.filter(finite).sort((a, b) => a - b);
  if (!sorted.length) return null;
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}
const mib = value => finite(value) ? value / 1048576 : null;
const mj = value => finite(value) ? value * 1000 : null;
export const metrics = [
  ...['frame', 'gpu', 'cpu'].flatMap(part => ['p50', 'p99'].map(q => ({
    id: `${part}-${q}`, label: `${part === 'frame' ? 'Frame' : part.toUpperCase()} ${q}`, unit: 'ms',
    value: row => row.statistics?.timing_ms?.[part]?.[q],
  }))),
  {id: 'engine-gpu', label: 'Engine GPU memory', unit: 'MiB', value: r => mib(r.statistics?.memory?.steady?.engine?.measured?.gpu_bytes)},
  {id: 'engine-cpu', label: 'Engine CPU memory', unit: 'MiB', value: r => mib(r.statistics?.memory?.steady?.engine?.measured?.cpu_bytes)},
  {id: 'process', label: 'Process memory', unit: 'MiB', value: r => {
    const p = r.statistics?.memory?.steady?.process;
    return mib(p?.phys_footprint_bytes?.measured ?? p?.pss_bytes?.measured);
  }},
  {id: 'gpu-energy', label: 'GPU rail energy', unit: 'mJ/frame', value: r => {
    const rails = Object.values(r.statistics?.energy?.rails ?? {}).filter(x => x.subsystem === 'GPU');
    return rails.length && rails.every(x => finite(x.joules_per_frame))
      ? mj(rails.reduce((sum, x) => sum + x.joules_per_frame, 0)) : null;
  }},
  {id: 'process-energy', label: 'Process energy', unit: 'mJ/frame', value: r => mj(r.statistics?.energy?.rails?.process?.joules_per_frame)},
  {id: 'cycles', label: 'Shader active cycles p50', unit: 'cycles', value: r => r.statistics?.gpu_counters?.shader_core_active_cycles?.p50},
];

export function cherenkovHistory(rows) {
  return rows.filter(r => r.kind === 'run' && r.metadata.valid && r.statistics
    && (r.engine === 'cherenkov' || r.engine === 'cherenkov-cpu'));
}

export function commitPoints(rows, metric) {
  const groups = new Map();
  for (const row of rows) {
    const revision = row.metadata.source.revision;
    if (!groups.has(revision)) groups.set(revision, []);
    groups.get(revision).push(row);
  }
  return [...groups].map(([revision, runs]) => {
    const values = runs.map(metric.value).filter(finite);
    return {revision, runs, count: values.length, total: runs.length,
      first: runs.map(r => r.metadata.recorded_at).sort((a, b) => Date.parse(a) - Date.parse(b))[0],
      value: median(values), low: values.length ? Math.min(...values) : null,
      high: values.length ? Math.max(...values) : null};
  }).sort((a, b) => Date.parse(a.first) - Date.parse(b.first) || a.revision.localeCompare(b.revision));
}

export function change(value, baseline) {
  return finite(value) && finite(baseline) && baseline !== 0 ? (value / baseline - 1) * 100 : null;
}
