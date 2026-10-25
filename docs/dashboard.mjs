import {metrics, validHistory, commitPoints, change, finite} from './history.mjs';

const $ = id => document.getElementById(id);
const colors = {'cherenkov':'#438b5c','cherenkov-cpu':'#438b5c','skia-metal':'#bb713b','skia-vulkan':'#bb713b','skia-cpu':'#bb713b','vello-classic':'#477bd0','vello-hybrid':'#8362b7','vello-cpu':'#477bd0'};
const number = value => finite(value) ? value.toLocaleString('en-US', {maximumFractionDigits:3, minimumFractionDigits:3}) : '—';
const percent = value => finite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(2)}%` : '—';
const date = value => new Intl.DateTimeFormat('en-US', {dateStyle:'medium',timeStyle:'short',timeZone:'America/New_York'}).format(new Date(value));
const unique = values => [...new Set(values)];
function options(id, entries) {
  const select = $(id), previous = select.value;
  select.replaceChildren(...entries.map(([value, text]) => new Option(text, value)));
  if (entries.some(([value]) => value === previous)) select.value = previous;
  select.disabled = !entries.length;
}
function selected(id) { return $(id).querySelector('[aria-pressed="true"]'); }
function switches(id, entries, onChange) {
  const group = $(id);
  group.replaceChildren(...entries.map(([value, label], index) => {
    const button = document.createElement('button');
    button.type = 'button'; button.textContent = label; button.dataset.value = value;
    button.setAttribute('aria-pressed', String(index === 0));
    button.onclick = () => {
      for (const sibling of group.children) sibling.setAttribute('aria-pressed', String(sibling === button));
      onChange();
    };
    return button;
  }));
}
function cell(row, value, className = '') {
  const element = document.createElement('td');
  if (value instanceof Node) element.append(value); else element.textContent = value;
  element.className = className; row.append(element); return element;
}
function link(text, href) { const a = document.createElement('a'); a.textContent = text; a.href = href; return a; }
function paragraph(parent, text) { const p = document.createElement('p'); p.textContent = text; parent.append(p); }
function commitLink(revision, repository = 'water-rs/waterui') { return link(revision.slice(0,9), `https://github.com/${repository}/commit/${revision}`); }
function chartOptions() {
  return {responsive:true, maintainAspectRatio:false, animation:false,
    plugins:{legend:{display:false}},
    scales:{x:{grid:{color:'#eee'},ticks:{color:'#777'}},y:{grid:{color:'#eee'},ticks:{color:'#777'},title:{display:true,color:'#555'}}}};
}

async function main() {
  const response = await fetch('./data.json');
  if (!response.ok) throw new Error(`Measurements could not be loaded (${response.status}).`);
  const data = await response.json();
  const history = validHistory(data.history);
  const historyCount = `${history.length} history runs · ${unique(history.map(r => r.device.model_identifier)).length} device models`;
  $('archiveCount').textContent = historyCount;
  Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
  Chart.defaults.font.size = 12;
  const charts = {history:[], snapshot:[]};
  function panels(kind, scenes, render) {
    charts[kind].forEach(chart => chart.destroy()); charts[kind] = [];
    const container = $(`${kind}Panels`), group = $(`${kind}Scenes`);
    const previous = new Map([...group.children].map(button => [button.dataset.value, button.getAttribute('aria-pressed') === 'true']));
    container.replaceChildren(); group.replaceChildren();
    for (const scene of scenes) {
      const panel = $(`${kind}Template`).content.firstElementChild.cloneNode(true);
      const button = document.createElement('button');
      button.type = 'button'; button.textContent = scene; button.dataset.value = scene;
      const visible = previous.get(scene) ?? true;
      panel.hidden = !visible; button.setAttribute('aria-pressed', String(visible));
      button.onclick = () => {
        panel.hidden = !panel.hidden; button.setAttribute('aria-pressed', String(!panel.hidden));
        charts[kind].forEach(chart => chart.resize());
      };
      group.append(button); container.append(panel);
      render(scene, id => panel.querySelector(`[data-part="${id}"]`));
    }
  }

  function view(name) {
    $('archiveCount').textContent = name === 'history' ? historyCount : `${data.runs.length} cross-engine snapshots`;
    for (const key of ['history','snapshot']) {
      $(`${key}View`).hidden = key !== name;
      $(`${key}Tab`).setAttribute('aria-pressed',String(key === name));
    }
    charts[name].forEach(chart => chart.resize());
  }
  $('historyTab').onclick = () => view('history');
  $('snapshotTab').onclick = () => view('snapshot');
  options('historyDevice', unique(history.map(r => r.device.model_identifier)).map(id => {
    const d = history.find(r => r.device.model_identifier === id).device;
    return [id, `${d.model} · ${id}`];
  }));
  switches('historyMetric', metrics.map(m => [m.id, m.label]), renderHistory);

  function deviceRows() { return history.filter(r => r.device.model_identifier === $('historyDevice').value); }
  function engineRows() { return deviceRows().filter(r => r.engine === $('historyEngine').value); }
  function selectEngine() {
    options('historyEngine',unique(deviceRows().map(r => r.engine)).map(e => [e,e])); renderHistory();
  }
  const conditionSelection = new Map();
  function renderHistory() {
    panels('history', unique(engineRows().map(r => r.scene)), (scene, part) => {
      const rows = engineRows().filter(r => r.scene === scene);
      const conditions = part('historyConditions');
      const keys = unique(rows.map(r => r.comparison_key));
      conditions.replaceChildren(...keys.map((key, index) => {
        const r = rows.find(r => r.comparison_key === key), config = r.configuration;
        return new Option(`${r.width} × ${r.height} · ${config.rate_hz ?? 'unspecified'} Hz · ${config.mode ?? (config.clocks ? 'fixed clocks' : 'recorded configuration')} · set ${index + 1}`, key);
      }));
      const saved = conditionSelection.get(scene);
      if (keys.includes(saved)) conditions.value = saved;
      const chartConfig = chartOptions();
      chartConfig.interaction = {mode:'index',intersect:false};
      chartConfig.scales.x.title = {display:true,text:'Commit · first measurement order',color:'#777'};
      const trend = new Chart(part('historyChart'), {type:'line',data:{labels:[],datasets:[]},options:chartConfig});
      charts.history.push(trend);
      const draw = () => drawHistory(rows.filter(r => r.comparison_key === conditions.value), part, trend, scene);
      conditions.onchange = () => { conditionSelection.set(scene, conditions.value); draw(); }; draw();
    });
  }
  function drawHistory(rows, part, trend, scene) {
    const metric = metrics.find(m => m.id === selected('historyMetric').dataset.value);
    const points = commitPoints(rows,metric);
    const first = points[0]?.value, last = points.at(-1)?.value;
    const delta = change(last,first);
    part('metricTitle').textContent = `${scene} · ${metric.label} · ${metric.unit}`;
    part('historyContext').textContent = rows.length ? `${rows[0].device.model} · ${rows[0].scene} · ${rows[0].engine}` : 'No archived measurements for this selection.';
    part('historyDelta').textContent = points.length > 1 ? percent(delta) : '—';
    part('historyDelta').className = delta < 0 ? 'improved' : delta > 0 ? 'regressed' : '';
    part('pointCount').textContent = `${points.length} commits · ${rows.length} runs`;
    const color = colors[rows[0]?.engine] ?? '#555';
    trend.data.labels = points.map(p => p.revision.slice(0,9));
    trend.data.datasets = [
      {label:'Run minimum',data:points.map(p => p.low),borderWidth:0,pointRadius:0},
      {label:'Run range',data:points.map(p => p.high),borderWidth:0,pointRadius:0,fill:'-1',backgroundColor:color+'20'},
      {label:'Median',data:points.map(p => p.value),borderColor:color,backgroundColor:color,borderWidth:2,pointRadius:4,pointHoverRadius:6,spanGaps:false,tension:0},
    ];
    trend.options.scales.y.beginAtZero = $('zeroAxis').checked;
    trend.options.scales.y.title.text = `${metric.unit} · ${$('zeroAxis').checked ? 'zero baseline' : 'scaled to measured range'}`;
    trend.options.plugins.tooltip = {filter:item => item.datasetIndex === 2,callbacks:{
      title:items => points[items[0].dataIndex].revision.slice(0,12),
      label:item => `${metric.label}: ${number(item.parsed.y)} ${metric.unit}`,
      afterLabel:item => { const p = points[item.dataIndex]; return [`Run range: ${number(p.low)}–${number(p.high)}`,`${p.count}/${p.total} runs have this metric`, `First measured: ${date(p.first)}`]; },
    }};
    trend.update();
    const notices = [];
    if (!points.length) notices.push('No measurements available.');
    else if (points.length === 1) notices.push('Only one measured commit: no trend can be inferred yet.');
    if (points.some(p => p.count < p.total)) notices.push('Some runs lack this metric. Missing values are not plotted as zero.');
    if (rows.some(r => r.configuration.mode === 'screening')) notices.push('Screening measurements: not a controlled idle-host comparison.');
    if (metric.id === 'gpu-energy') notices.push('Whole-device GPU rail energy includes other GPU users; it is not application-exclusive energy.');
    if (metric.id === 'process-energy') notices.push('Process-attributed energy is not GPU-only energy.');
    part('historyNotice').textContent = notices.join(' ');
    const conditions = part('conditionsText'); conditions.replaceChildren();
    if (rows.length) {
      const r = rows[0];
      paragraph(conditions, `${r.device.model} (${r.device.model_identifier}) · ${r.device.os} · ${r.device.gpu}`);
      paragraph(conditions, `Adapter: ${r.adapter.backend ?? 'unspecified'} · ${r.adapter.target_format ?? 'unspecified format'} · ${r.adapter.driver_info || 'driver version not separately reported'}`);
      for (const limitation of unique(rows.flatMap(r => r.metadata.limitations ?? []))) paragraph(conditions,limitation);
      const pre = document.createElement('pre'); pre.textContent = JSON.stringify(r.configuration,null,2); conditions.append(pre);
    }
    part('historyRows').replaceChildren();
    for (const point of points) {
      const tr = document.createElement('tr'); const diff = change(point.value,first);
      cell(tr,commitLink(point.revision,point.runs[0].metadata.source.repository));
      cell(tr,date(point.first)); cell(tr,number(point.value)); cell(tr,`${number(point.low)}–${number(point.high)}`);
      cell(tr,percent(diff),diff < 0 ? 'improved' : diff > 0 ? 'regressed' : ''); cell(tr,`${point.count}/${point.total}`);
      const details = document.createElement('details'), summary = document.createElement('summary'); summary.textContent = 'Raw JSON'; details.append(summary);
      point.runs.forEach((r,i) => details.append(link(`Run ${i+1} · ${date(r.metadata.recorded_at)}`,`https://github.com/water-rs/cherenkov-benchmarks/blob/main/${r.raw}`)));
      cell(tr,details); part('historyRows').append(tr);
    }
  }
  $('historyDevice').onchange = selectEngine;
  $('historyEngine').onchange = renderHistory;
  $('zeroAxis').onchange = renderHistory;
  selectEngine();

  switches('snapshotBackend', [['gpu','GPU'],['cpu','CPU']], selectSnapshotScene);
  switches('snapshotMetric', [
    ['frame_p50_ms','Frame p50'], ['frame_p99_ms','Frame p99'], ['gpu_p50_ms','GPU p50'],
    ['energy_mj','Energy'], ['process_mem_mib','Process memory'], ['driver_gpu_mem_mib','Driver GPU memory'],
  ], renderSnapshot);
  options('snapshotRun',data.runs.map((r,i) => [String(i),r.provenance.run_id]));
  $('snapshotRun').value = String(data.runs.length-1);
  function selectSnapshotDevice() {
    const run = data.runs[Number($('snapshotRun').value)];
    options('snapshotDevice',Object.keys(run.devices).map(d => [d,run.provenance.devices[d].name])); selectSnapshotScene();
  }
  function selectSnapshotScene() {
    renderSnapshot();
  }
  function renderSnapshot() {
    const run = data.runs[Number($('snapshotRun').value)], deviceKey = $('snapshotDevice').value;
    const backend = selected('snapshotBackend').dataset.value, metric = selected('snapshotMetric').dataset.value;
    $('snapshotCommit').replaceChildren('Source ',commitLink(run.provenance.commit));
    panels('snapshot', Object.keys(run.devices[deviceKey][backend]), (scene, part) => {
      const barOptions = chartOptions(); barOptions.indexAxis = 'y'; barOptions.scales.x.beginAtZero = true;
      const bars = new Chart(part('comparisonChart'), {type:'bar',data:{labels:[],datasets:[]},options:barOptions});
      charts.snapshot.push(bars);
    const entries = Object.entries(run.devices[deviceKey][backend][scene]);
    entries.sort((a,b) => (finite(a[1][metric]) ? a[1][metric] : Infinity) - (finite(b[1][metric]) ? b[1][metric] : Infinity));
    part('snapshotTitle').textContent = `${scene} · ${selected('snapshotMetric').textContent} · ${metric.endsWith('_ms') ? 'ms' : metric.endsWith('_mib') ? 'MiB' : 'mJ/frame'}`;
    bars.data.labels = entries.map(([engine]) => engine);
    bars.data.datasets = [{data:entries.map(([,r]) => finite(r[metric]) ? r[metric] : null),backgroundColor:entries.map(([e]) => colors[e] ?? '#777'),barThickness:18}];
    bars.update();
    part('snapshotNote').textContent = (scene === 'ui-list' ? 'This historical ui-list workload is static; it does not measure scrolling. ' : '') +
      (deviceKey === 'pixel-9-pro' ? 'Energy: selected whole-device GPU, prime-CPU and memory rails; not app-exclusive.' : 'Energy: process-attributed; not GPU-only.') +
      (entries.every(([,r]) => !finite(r[metric])) ? ' Selected metric is not reported for this backend.' : '');
    part('snapshotRows').replaceChildren();
    for (const [engine,r] of entries) {
      const tr = document.createElement('tr'), name = document.createElement('span'), dot = document.createElement('span');
      dot.className = 'engine-dot'; dot.style.backgroundColor = colors[engine] ?? '#777'; name.append(dot,engine); cell(tr,name);
      for (const key of ['frame_p50_ms','frame_p99_ms','gpu_p50_ms','gpu_p99_ms','cpu_p50_ms','energy_mj','process_mem_mib','driver_gpu_mem_mib']) cell(tr,number(r[key]));
      part('snapshotRows').append(tr);
    }
    });
  }
  $('snapshotRun').onchange = selectSnapshotDevice; $('snapshotDevice').onchange = selectSnapshotScene;
  if (data.runs.length) selectSnapshotDevice(); else $('snapshotTab').disabled = true;
}
main().catch(error => { $('error').hidden = false; $('error').textContent = error.message; });
