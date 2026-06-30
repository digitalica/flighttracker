// ── State ─────────────────────────────────────────────────────────────────────
const REFRESH_INTERVAL_MS = 2000;
const RANGES  = [30, 60, 120, 240, 480, 960];
const RLABEL  = { 30: '30 min', 60: '1 h', 120: '2 h', 240: '4 h', 480: '8 h', 960: '16 h' };
const DEF_HEX = '484763'; // PH-TGC

let aircraft      = [];
let statusMap     = {};
let lastPost      = null;
let sessionStart  = null;
let lastTakeoffTs = null;
let lastAgl       = null;
let hex           = DEF_HEX;
let mins          = 30;
let useAGL        = true;
let useFake       = false;
let chart         = null;
let lastRoc       = 0;
let currentView  = 'altitude';
let _historyOpen = false;

const reg    = () => (aircraft.find(a => a.hex === hex) || {}).registration || hex;
const yLabel = () => useAGL ? 'ft  (AGL - calculated)' : 'ft  (baro)';

const ROC_ARROW_MIN = 150; // ft/min threshold to show climb/descent arrow
function climbArrow(roc) {
  if (roc >  ROC_ARROW_MIN) return ' 🛫';
  if (roc < -ROC_ARROW_MIN) return ' 🛬';
  return '';
}

// ── View switching ────────────────────────────────────────────────────────────
function showView(name) {
  currentView = name;
  document.getElementById('view-altitude').style.display  = name === 'altitude'  ? 'block' : 'none';
  document.getElementById('view-events').style.display    = name === 'events'    ? 'block' : 'none';
  document.getElementById('view-altimeter').style.display = name === 'altimeter' ? 'block' : 'none';
  document.getElementById('view-announce').style.display  = name === 'announce'  ? 'block' : 'none';
  document.getElementById('view-about').style.display     = name === 'about'     ? 'block' : 'none';
  document.getElementById('nav-altitude').className  = name === 'altitude'  ? 'active' : '';
  document.getElementById('nav-events').className    = name === 'events'    ? 'active' : '';
  document.getElementById('nav-altimeter').className = name === 'altimeter' ? 'active' : '';
  document.getElementById('nav-announce').className  = name === 'announce'  ? 'active' : '';
  document.getElementById('nav-about').className     = name === 'about'     ? 'active' : '';
  if (name === 'altimeter') animateAltimeter(lastAgl);
  document.getElementById('nav').style.display = 'none';
  updateUrl();
  if (name === 'altitude' && chart) chart.resize();
  if (name === 'events') {
    _historyOpen = false;
    document.getElementById('history-panel').style.display = 'none';
    refreshEvents();
  }
  if (name === 'announce') refreshAnnouncements();
}

document.getElementById('hamburger').addEventListener('click', e => {
  e.stopPropagation();
  const nav = document.getElementById('nav');
  nav.style.display = nav.style.display === 'block' ? 'none' : 'block';
});

document.addEventListener('click', () => {
  document.getElementById('nav').style.display = 'none';
});

// ── Status helpers ────────────────────────────────────────────────────────────
function acStatus(h) {
  const ls = statusMap[h];
  if (!ls) return 'inactive';
  const t = new Date(ls);
  if (Date.now() - t < 60_000) return 'active';
  const midnight = new Date(); midnight.setHours(0, 0, 0, 0);
  return t < midnight ? 'inactive' : 'sleeping';
}

const STATUS_SYMBOL = { active: '●', sleeping: '●', inactive: '○' };
const STATUS_COLOR  = { active: '#5c5', sleeping: '#fa4', inactive: '#444' };

function flightTimeStr(startDate) {
  const totalMins = Math.floor((Date.now() - startDate) / 60_000);
  if (totalMins === 0) return '';
  const h = Math.floor(totalMins / 60);
  const m = totalMins % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

function lastSeenLabel(h) {
  const s = acStatus(h);
  if (s === 'active') {
    const ftStr = lastTakeoffTs ? flightTimeStr(lastTakeoffTs) : '';
    const ft  = ftStr ? `, ${ftStr}` : '';
    const alt = lastAgl != null ? `  ${lastAgl} ft AGL` : '';
    return `now${ft}${alt}`;
  }
  if (s === 'inactive') return 'not today';
  const diffMs = Date.now() - new Date(statusMap[h]);
  const mins = Math.floor(diffMs / 60_000);
  const alt  = lastAgl != null ? `  ${lastAgl} ft AGL` : '';
  return (mins < 60 ? `${mins} min ago` : `${Math.floor(mins / 60)} h ago`) + alt;
}

function updateDataStatus() {
  const el = document.getElementById('data-status');
  if (!lastPost) {
    el.textContent = 'no data';
    el.style.cssText = 'background:#2a0d0d;color:#c44;border-color:#4a1a1a';
    return;
  }
  const ageSecs = (Date.now() - lastPost) / 1000;
  if (ageSecs <= 10) {
    el.textContent = 'data ok';
    el.style.cssText = 'background:#0d2a0d;color:#5c5;border-color:#1a4a1a';
  } else if (ageSecs <= 60) {
    el.textContent = 'slow data';
    el.style.cssText = 'background:#2a2a0d;color:#cc5;border-color:#4a4a1a';
  } else {
    el.textContent = 'no data';
    el.style.cssText = 'background:#2a0d0d;color:#c44;border-color:#4a1a1a';
  }
}

// ── Aircraft picker ───────────────────────────────────────────────────────────
let pickerTimer = null;

function _resetPickerTimer() {
  clearTimeout(pickerTimer);
  pickerTimer = setTimeout(() => {
    document.getElementById('picker').style.display = 'none';
  }, 30_000);
}

function _openPicker(anchorRect, onSelect) {
  const picker = document.getElementById('picker');
  const statusRank = { active: 1, sleeping: 2, inactive: 3 };
  const sorted = [...aircraft].sort((a, b) => {
    if (a.hex === '484763') return -1;
    if (b.hex === '484763') return  1;
    const sr = statusRank[acStatus(a.hex)] - statusRank[acStatus(b.hex)];
    if (sr !== 0) return sr;
    return a.registration.localeCompare(b.registration);
  });
  picker.innerHTML = sorted.map(a => {
    const s = acStatus(a.hex);
    return `<div class="${a.hex === hex ? 'active' : ''}" data-hex="${a.hex}">` +
           `<span style="color:${STATUS_COLOR[s]}">${STATUS_SYMBOL[s]}</span> ${a.registration}</div>`;
  }).join('');
  picker.querySelectorAll('div').forEach(div => {
    div.addEventListener('click', () => {
      clearTimeout(pickerTimer);
      hex = div.dataset.hex;
      announcedEvents.clear();
      eventsInitialized = false;
      lastTakeoffTs = null;
      lastAgl       = null;
      _historyOpen  = false;
      document.getElementById('history-panel').style.display = 'none';
      picker.style.display = 'none';
      onSelect();
    });
    div.addEventListener('mouseenter', _resetPickerTimer);
  });
  // Position below the anchor, horizontally centred on it
  picker.style.display = 'block';
  const pw = picker.offsetWidth;
  const cx = anchorRect.left + anchorRect.width / 2;
  picker.style.left = Math.max(4, cx - pw / 2) + 'px';
  picker.style.top  = (anchorRect.bottom + 4) + 'px';
  _resetPickerTimer();
}

function togglePicker() {
  const picker = document.getElementById('picker');
  if (picker.style.display === 'block') { clearTimeout(pickerTimer); picker.style.display = 'none'; return; }
  const canvas = document.getElementById('chart');
  const cRect  = canvas.getBoundingClientRect();
  const tb     = chart.titleBlock;
  const anchorRect = {
    left:   cRect.left,
    width:  cRect.width,
    bottom: cRect.top + (tb ? tb.bottom : chart.chartArea.top),
  };
  _openPicker(anchorRect, () => {
    chart.options.plugins.title.text = reg();
    chart.update('none');
    updateUrl();
    refresh();
  });
}

function toggleEventsPicker() {
  const picker = document.getElementById('picker');
  if (picker.style.display === 'block') { clearTimeout(pickerTimer); picker.style.display = 'none'; return; }
  const anchor = document.getElementById('events-title');
  _openPicker(anchor.getBoundingClientRect(), () => {
    lastRoc = 0;
    refreshEvents();
  });
}

document.addEventListener('click', e => {
  const picker    = document.getElementById('picker');
  const canvas    = document.getElementById('chart');
  const evTitle   = document.getElementById('events-title');
  const altTitle  = document.getElementById('altimeter-title');
  if (picker.style.display === 'block'
      && !picker.contains(e.target)
      && e.target !== canvas
      && e.target !== evTitle
      && e.target !== altTitle) {
    clearTimeout(pickerTimer);
    picker.style.display = 'none';
  }
});

function toggleAltimeterPicker() {
  const picker = document.getElementById('picker');
  if (picker.style.display === 'block') { clearTimeout(pickerTimer); picker.style.display = 'none'; return; }
  const anchor = document.getElementById('altimeter-title');
  _openPicker(anchor.getBoundingClientRect(), () => {
    lastRoc = 0;
    refresh();
  });
}

document.getElementById('events-title').addEventListener('click', toggleEventsPicker);
document.getElementById('altimeter-title').addEventListener('click', toggleAltimeterPicker);

// ── URL state ─────────────────────────────────────────────────────────────────
function readUrlState() {
  const p = new URLSearchParams(window.location.search);
  const acReg = p.get('ac');
  if (acReg) {
    const found = aircraft.find(a => a.registration === acReg);
    if (found) hex = found.hex;
  }
  const minsParam = parseInt(p.get('mins'));
  if (RANGES.includes(minsParam)) mins = minsParam;
  if (p.has('agl')) useAGL = p.get('agl') === '1';
  const view = p.get('view');
  if (['events', 'altimeter', 'announce', 'about'].includes(view)) currentView = view;
  if (p.get('alti') === 'skydive') altimeterMode = 'skydive';
}

function updateUrl() {
  const p = new URLSearchParams({ ac: reg(), mins, agl: useAGL ? '1' : '0', view: currentView, alti: altimeterMode });
  history.replaceState(null, '', '?' + p.toString());
}

// ── Chart ─────────────────────────────────────────────────────────────────────
const GAP_SECS = 60;

function findGaps(data) {
  const gaps = [];
  for (let i = 1; i < data.length; i++) {
    const dt = (new Date(data[i].t) - new Date(data[i - 1].t)) / 1000;
    if (dt > GAP_SECS) gaps.push({ from: new Date(data[i - 1].t), to: new Date(data[i].t) });
  }
  return gaps;
}

const gapPlugin = {
  id: 'gaps',
  beforeDraw(chart) {
    const gaps = chart._gapRanges;
    if (!gaps || !gaps.length) return;
    const { ctx, chartArea: ca, scales } = chart;
    ctx.save();
    ctx.fillStyle = 'rgba(255, 80, 80, 0.10)';
    for (const g of gaps) {
      const x1 = Math.max(scales.x.getPixelForValue(g.from), ca.left);
      const x2 = Math.min(scales.x.getPixelForValue(g.to),   ca.right);
      if (x2 > x1) ctx.fillRect(x1, ca.top, x2 - x1, ca.height);
    }
    ctx.restore();
  }
};
Chart.register(gapPlugin);

function buildSeries(data, key) {
  const out = [];
  for (let i = 0; i < data.length; i++) {
    if (i > 0) {
      const dt = (new Date(data[i].t) - new Date(data[i - 1].t)) / 1000;
      if (dt > GAP_SECS) {
        const mid = (new Date(data[i - 1].t).getTime() + new Date(data[i].t).getTime()) / 2;
        out.push({ x: new Date(mid), y: null });
      }
    }
    out.push({ x: new Date(data[i].t), y: data[i][key], baro: data[i].baro, agl: data[i].agl });
  }
  return out;
}

function buildChart() {
  const canvas = document.getElementById('chart');
  chart = new Chart(canvas.getContext('2d'), {
    type: 'line',
    data: {
      datasets: [
        {
          yAxisID: 'y2',
          data: [],
          borderColor: '#4a9eff',
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.2,
          fill: false,
          spanGaps: false,
        },
        {
          yAxisID: 'y',
          data: [],
          borderColor: '#666',
          borderWidth: 1,
          pointRadius: 0,
          tension: 0.2,
          fill: false,
          spanGaps: false,
        },
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        title: {
          display: true,
          text: regSuffix(),
          color: '#ddd',
          font: { size: 20, family: 'monospace', weight: 'normal' },
          padding: { top: 4, bottom: 2 },
        },
        subtitle: {
          display: true,
          text: '',
          color: '#aaa',
          font: { size: 18, family: 'monospace', weight: 'bold' },
          padding: { bottom: 10 },
        },
        tooltip: {
          backgroundColor: '#1a1a1a',
          borderColor: '#333',
          borderWidth: 1,
          titleColor: '#888',
          bodyColor: '#ddd',
          bodyFont: { family: 'monospace' },
          callbacks: {
            label: ctx => {
              if (ctx.datasetIndex === 1) return `${ctx.parsed.y} ft/min`;
              const pt = ctx.raw;
              if (pt.y == null) return null;
              return useAGL
                ? `${pt.agl} ft AGL  (${pt.baro} ft baro)`
                : `${pt.baro} ft baro  (${pt.agl} ft AGL)`;
            }
          }
        }
      },
      scales: {
        x: {
          type: 'time',
          time: {
            tooltipFormat: 'HH:mm:ss',
            displayFormats: { minute: 'HH:mm', hour: 'HH:mm' }
          },
          title: { display: true, text: RLABEL[mins], color: '#ddd', font: { size: 17, family: 'monospace', weight: 'bold' } },
          ticks: { color: '#ddd', maxTicksLimit: 8, font: { family: 'monospace', size: 17, weight: 'bold' } },
          grid: { color: '#1a1a1a' },
        },
        y: {
          title: { display: true, text: 'ft/min', color: '#666', font: { size: 17, family: 'monospace', weight: 'bold' } },
          ticks: { color: '#666', font: { family: 'monospace', size: 17, weight: 'bold' } },
          grid: { drawOnChartArea: false },
        },
        y2: {
          position: 'right',
          title: { display: true, text: yLabel(), color: '#4a9eff', font: { size: 17, family: 'monospace', weight: 'bold' } },
          ticks: { color: '#4a9eff', font: { family: 'monospace', size: 17, weight: 'bold' } },
          grid: { color: '#1a1a1a' },
        }
      }
    }
  });

  canvas.addEventListener('click', function(e) {
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const ca = chart.chartArea;
    if (!ca) return;
    const titleBottom = (chart.subtitleBlock ?? chart.titleBlock)?.bottom ?? ca.top;
    if (y >= (chart.titleBlock?.top ?? 0) && y <= titleBottom) {
      togglePicker();
    } else if (x > ca.right && y >= ca.top && y <= ca.bottom) {
      useAGL = !useAGL;
      chart.options.scales.y2.title.text = yLabel();
      updateUrl();
      refresh();
    } else if (y > ca.bottom && x >= ca.left && x <= ca.right) {
      mins = RANGES[(RANGES.indexOf(mins) + 1) % RANGES.length];
      chart.options.scales.x.title.text = RLABEL[mins];
      chart.update('none');
      updateUrl();
      refresh();
    }
  });

  canvas.addEventListener('mousemove', function(e) {
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const ca = chart.chartArea;
    if (!ca) return;
    const _titleBottom = (chart.subtitleBlock ?? chart.titleBlock)?.bottom ?? ca.top;
    const hot = (y >= (chart.titleBlock?.top ?? 0) && y <= _titleBottom)
      || (x > ca.right && y >= ca.top && y <= ca.bottom)
      || (y > ca.bottom && x >= ca.left && x <= ca.right);
    canvas.style.cursor = hot ? 'pointer' : 'default';
  });
}

async function refresh() {
  const altFetch = currentView === 'altimeter'
    ? fetch(`/api/current?ac=${encodeURIComponent(reg())}${useFake ? '&fake' : ''}`).then(r => r.json())
    : fetch(`/api/altitude/${hex}?minutes=${mins}${useFake ? '&fake' : ''}`).then(r => r.json());

  // Skip events fetch on events view — refreshEvents() runs in parallel and owns lastTakeoffTs there
  const evFetch = currentView !== 'events'
    ? fetch(`/api/events/${hex}`).then(r => r.json())
    : Promise.resolve(null);

  const [altResp, newStatus, evResp] = await Promise.all([
    altFetch,
    fetch('/api/status').then(r => r.json()),
    evFetch,
  ]);

  statusMap = newStatus.aircraft;
  lastPost  = newStatus.last_post ? new Date(newStatus.last_post) : null;
  document.getElementById('user-count').textContent =
    newStatus.active_users === 1 ? '1 user' : `${newStatus.active_users} users`;

  if (evResp) {
    // Replay events to find whether the aircraft is currently airborne
    let _takeoffTs = null;
    for (const ev of evResp.events) {
      const t = new Date(ev.ts);
      if (ev.type === 'takeoff') { _takeoffTs = t; }
      if (ev.type === 'landing') { _takeoffTs = null; }
      // touch_and_go: still airborne, keep _takeoffTs as the original takeoff
    }
    lastTakeoffTs = _takeoffTs;
  }

  if (currentView === 'altimeter') {
    lastAgl = altResp.agl;
    const arrow = acStatus(hex) === 'active' ? climbArrow(lastRoc) : '';
    document.getElementById('altimeter-title').textContent    = regSuffix() + arrow;
    document.getElementById('altimeter-subtitle').textContent = lastSeenLabel(hex);
    document.title = `${regSuffix()} FlightTracker`;
    updateDataStatus();
    animateAltimeter(lastAgl);
    return;
  }

  sessionStart = altResp.session_start ? new Date(altResp.session_start) : null;
  const data   = altResp.points;

  const key = useAGL ? 'agl' : 'baro';
  const now = new Date();
  chart.data.datasets[0].data = buildSeries(data, key);
  chart.data.datasets[1].data = buildSeries(data, 'roc');
  chart._gapRanges = findGaps(data);
  chart.options.scales.x.min = new Date(now - mins * 60 * 1000);
  chart.options.scales.x.max = now;
  const altVals = data.map(d => d[key]).filter(v => v != null);
  const altMin = altVals.length ? Math.min(...altVals) : 0;
  chart.options.scales.y2.min = altMin >= 0 ? 0 : undefined;
  chart.options.scales.y2.suggestedMax = 1000;
  const maxAbs = Math.max(...data.map(d => Math.abs(d.roc)), 100);
  chart.options.scales.y.min = -maxAbs;
  chart.options.scales.y.max =  maxAbs;
  lastRoc = data.length ? data[data.length - 1].roc : 0;
  lastAgl = data.length ? data[data.length - 1].agl : null;
  const arrow = acStatus(hex) === 'active' ? climbArrow(lastRoc) : '';
  chart.options.plugins.title.text    = regSuffix() + arrow;
  chart.options.plugins.subtitle.text = lastSeenLabel(hex);
  document.title = `${regSuffix()} FlightTracker`;
  chart.update('none');
  updateDataStatus();

  document.getElementById('events-title').textContent       = regSuffix() + arrow;
  document.getElementById('events-subtitle').textContent    = lastSeenLabel(hex);
  document.getElementById('altimeter-title').textContent    = regSuffix() + arrow;
  document.getElementById('altimeter-subtitle').textContent = lastSeenLabel(hex);
}

// ── Events view ───────────────────────────────────────────────────────────────
function durStr(ms) {
  const totalMins = Math.round(ms / 60_000);
  const h = Math.floor(totalMins / 60);
  const m = totalMins % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

const EV_LABEL = {
  takeoff:         { icon: '↑', label: 'takeoff',              cls: 'ev-takeoff'  },
  landing:         { icon: '↓', label: 'landing',              cls: 'ev-landing'  },
  touch_and_go:    { icon: '↕', label: 'touch and go',         cls: 'ev-takeoff'  },
  climbing_3000:   { icon: '↑', label: 'approaching 3500 ft',  cls: 'ev-climb'    },
  climbing_5500:   { icon: '↑', label: 'approaching 6000 ft',  cls: 'ev-climb'    },
  descending_3000: { icon: '↓', label: 'descending (thru 3000 ft)', cls: 'ev-desc' },
  descending_5500: { icon: '↓', label: 'descending (thru 5500 ft)', cls: 'ev-desc' },
  active:          { icon: '●', label: 'signal',               cls: 'ev-active'   },
  inactive:        { icon: '○', label: 'signal lost',          cls: 'ev-inactive' },
};

function relTime(t) {
  const mins = Math.floor((Date.now() - t) / 60_000);
  if (mins < 1)  return 'just now';
  if (mins < 60) return `${mins} min ago`;
  return `${Math.floor(mins / 60)} h ago`;
}

function renderEvents(evs, aglOffset) {
  const el = document.getElementById('events-list');
  if (!evs.length) {
    el.innerHTML = '<div style="color:#555">No events today.</div>';
    return;
  }
  // Pre-compute durations (requires forward order)
  const rows = [];
  let lastTakeoffTs = null;
  for (const ev of evs) {
    const t = new Date(ev.ts);
    let dur = '';
    if (ev.type === 'takeoff') lastTakeoffTs = t;
    if (ev.type === 'landing' && lastTakeoffTs) {
      dur = durStr(t - lastTakeoffTs);
      lastTakeoffTs = null;
    }
    rows.push({ ev, t, dur });
  }

  let html = `<div style="color:#555;margin-bottom:12px;font-size:15px;">AGL offset: ${aglOffset} ft</div>`;
  html += '<table>';
  for (const { ev, t, dur } of [...rows].reverse()) {
    const time = t.toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const def  = EV_LABEL[ev.type] || { icon: '?', label: ev.type, cls: '' };
    const speech = eventSpeechText(ev.type);
    const clickAttr = speech ? `onclick="speak('${speech}')" style="cursor:pointer"` : '';
    html += `<tr ${clickAttr}>
      <td style="padding-right:14px">${time}</td>
      <td style="padding-right:20px;color:#555">${relTime(t)}</td>
      <td class="${def.cls}" style="padding-right:14px">${def.icon} ${def.label}</td>
      <td><span class="ev-dur">${dur}</span></td>
    </tr>`;
  }
  html += '</table>';
  el.innerHTML = html;
}

// ── Altimeter ────────────────────────────────────────────────────────────────

let _altCurrent    = null;       // altitude currently rendered on the dial
let _altTarget     = null;       // altitude we are animating towards
let _altAnimId     = null;       // requestAnimationFrame handle
let altimeterMode  = 'aviation'; // 'aviation' | 'skydive'

document.getElementById('altimeter-canvas').addEventListener('click', () => {
  altimeterMode = altimeterMode === 'aviation' ? 'skydive' : 'aviation';
  updateUrl();
  drawAltimeter(_altCurrent ?? lastAgl);
});

function animateAltimeter(target) {
  _altTarget = target;
  if (_altAnimId) { cancelAnimationFrame(_altAnimId); _altAnimId = null; }
  if (target == null) { _altCurrent = null; drawAltimeter(null); return; }
  if (_altCurrent == null) { _altCurrent = target; drawAltimeter(target); return; }

  const from  = _altCurrent;
  const start = performance.now();
  const DURATION = 600;

  function step(now) {
    const t    = Math.min(1, (now - start) / DURATION);
    const ease = t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t;
    _altCurrent = from + (target - from) * ease;
    drawAltimeter(_altCurrent);
    _altAnimId = t < 1 ? requestAnimationFrame(step) : null;
    if (t >= 1) _altCurrent = target;
  }
  _altAnimId = requestAnimationFrame(step);
}

function _drawBezel(c, cx, cy, r) {
  c.beginPath(); c.arc(cx, cy, r + 10, 0, Math.PI * 2);
  c.strokeStyle = '#333'; c.lineWidth = 18; c.stroke();
  c.beginPath(); c.arc(cx, cy, r, 0, Math.PI * 2);
  c.fillStyle = '#111'; c.fill();
  c.strokeStyle = '#555'; c.lineWidth = 3; c.stroke();
}

function _drawNeedle(c, cx, cy, r, angle, len, tailLen, width, color) {
  c.save(); c.translate(cx, cy); c.rotate(angle + Math.PI / 2);
  c.beginPath();
  c.moveTo(0,  tailLen);
  c.lineTo(-width, 0);
  c.lineTo(0, -len);
  c.lineTo( width, 0);
  c.closePath();
  c.fillStyle = color; c.fill();
  c.restore();
}

function _drawCap(c, cx, cy, r) {
  c.beginPath(); c.arc(cx, cy, r * 0.045, 0, Math.PI * 2);
  c.fillStyle = '#aaa'; c.fill();
}

function _drawNoData(c, cx, cy, r) {
  c.fillStyle = '#444';
  c.font = `${Math.round(r * 0.10)}px monospace`;
  c.textAlign = 'center'; c.textBaseline = 'middle';
  c.fillText('no data', cx, cy);
}

function drawAltimeter(agl) {
  if (altimeterMode === 'skydive') { _drawSkydive(agl); return; }

  const canvas = document.getElementById('altimeter-canvas');
  const s = canvas.width, cx = s / 2, cy = s / 2, r = s * 0.44;
  const c = canvas.getContext('2d');
  c.clearRect(0, 0, s, s);
  _drawBezel(c, cx, cy, r);

  for (let i = 0; i < 100; i++) {
    const angle  = (i / 100) * Math.PI * 2 - Math.PI / 2;
    const major  = i % 10 === 0, mid = i % 5 === 0 && !major;
    const len    = major ? r * 0.16 : mid ? r * 0.10 : r * 0.05;
    c.beginPath();
    c.moveTo(cx + Math.cos(angle) * (r - 3),       cy + Math.sin(angle) * (r - 3));
    c.lineTo(cx + Math.cos(angle) * (r - 3 - len), cy + Math.sin(angle) * (r - 3 - len));
    c.strokeStyle = major ? '#ddd' : mid ? '#777' : '#333';
    c.lineWidth   = major ? 3 : 1; c.stroke();
    if (major) {
      const lr = r * 0.73;
      c.fillStyle = '#ccc';
      c.font = `bold ${Math.round(r * 0.11)}px monospace`;
      c.textAlign = 'center'; c.textBaseline = 'middle';
      c.fillText(String(i / 10), cx + Math.cos(angle) * lr, cy + Math.sin(angle) * lr);
    }
  }

  // Mode label
  c.fillStyle = '#333'; c.font = `${Math.round(r * 0.07)}px monospace`;
  c.textAlign = 'center'; c.textBaseline = 'middle';
  c.fillText('aviation', cx, cy + r * 0.52);

  if (agl == null) { _drawNoData(c, cx, cy, r); return; }
  const ft = Math.max(0, agl);
  _drawNeedle(c, cx, cy, r, (ft / 10000) * Math.PI * 2 - Math.PI / 2, r * 0.48, r * 0.18, r * 0.04, '#888');
  _drawNeedle(c, cx, cy, r, ((ft % 1000) / 1000) * Math.PI * 2 - Math.PI / 2, r * 0.72, r * 0.22, r * 0.025, '#fff');
  _drawCap(c, cx, cy, r);
}

function _drawSkydive(agl) {
  const MAX_FT = 12000;
  const canvas = document.getElementById('altimeter-canvas');
  const s = canvas.width, cx = s / 2, cy = s / 2, r = s * 0.44;
  const c = canvas.getContext('2d');
  c.clearRect(0, 0, s, s);
  _drawBezel(c, cx, cy, r);

  const ftToAngle = ft => (ft / MAX_FT) * Math.PI * 2 - Math.PI / 2;

  // Coloured zone arcs
  const zones = [
    { from: 0,    to: 2500,  color: '#8b0000' },
    { from: 2500, to: 3500,  color: '#b8860b' },
    { from: 3500, to: 12000, color: '#1a5c1a' },
  ];
  const arcR = r * 0.87, arcW = r * 0.11;
  zones.forEach(z => {
    c.beginPath();
    c.arc(cx, cy, arcR, ftToAngle(z.from), ftToAngle(z.to));
    c.strokeStyle = z.color; c.lineWidth = arcW; c.stroke();
  });

  // Ticks every 500 ft (minor) and 1000 ft (major); skip label at 12000 (overlaps 0)
  for (let ft = 0; ft < MAX_FT; ft += 500) {
    const angle = ftToAngle(ft);
    const major = ft % 1000 === 0;
    const len   = major ? r * 0.16 : r * 0.08;
    c.beginPath();
    c.moveTo(cx + Math.cos(angle) * (r - 3),       cy + Math.sin(angle) * (r - 3));
    c.lineTo(cx + Math.cos(angle) * (r - 3 - len), cy + Math.sin(angle) * (r - 3 - len));
    c.strokeStyle = major ? '#ddd' : '#555'; c.lineWidth = major ? 3 : 1; c.stroke();
    if (major) {
      const lr = r * 0.71;
      c.fillStyle = '#ccc';
      c.font = `bold ${Math.round(r * 0.085)}px monospace`;
      c.textAlign = 'center'; c.textBaseline = 'middle';
      c.fillText(String(ft / 1000), cx + Math.cos(angle) * lr, cy + Math.sin(angle) * lr);
    }
  }

  // Mode label
  c.fillStyle = '#333'; c.font = `${Math.round(r * 0.07)}px monospace`;
  c.textAlign = 'center'; c.textBaseline = 'middle';
  c.fillText('skydive', cx, cy + r * 0.52);

  if (agl == null) { _drawNoData(c, cx, cy, r); return; }
  const ft = Math.max(0, Math.min(MAX_FT, agl));
  _drawNeedle(c, cx, cy, r, ftToAngle(ft), r * 0.74, r * 0.22, r * 0.032, '#fff');
  _drawCap(c, cx, cy, r);
}

// ── NATO phonetic alphabet ────────────────────────────────────────────────────
const NATO = {
  A:'Alpha', B:'Bravo', C:'Charlie', D:'Delta', E:'Echo', F:'Foxtrot',
  G:'Golf', H:'Hotel', I:'India', J:'Juliet', K:'Kilo', L:'Lima',
  M:'Mike', N:'November', O:'Oscar', P:'Papa', Q:'Quebec', R:'Romeo',
  S:'Sierra', T:'Tango', U:'Uniform', V:'Victor', W:'Whiskey',
  X:'X-ray', Y:'Yankee', Z:'Zulu',
};

function regSuffixFor(registration) {
  const r = registration.toUpperCase();
  const dashIdx = r.indexOf('-');
  const suffix = dashIdx >= 0 ? r.slice(dashIdx + 1) : r.slice(2);
  // If any of the last three characters is a digit, speak the full suffix
  return /\d/.test(suffix.slice(-3)) ? suffix : suffix.slice(-3);
}

function natoSuffixFor(registration) {
  return regSuffixFor(registration).split('').map(c => NATO[c] || c).join(' ');
}

function regSuffix() { return regSuffixFor(reg()); }
function natoSuffix() { return natoSuffixFor(reg()); }

// ── Speech ────────────────────────────────────────────────────────────────────
const announcedEvents = new Set();
let eventsInitialized = false;

// Pre-load voices so they're available on first speak()
if (window.speechSynthesis) window.speechSynthesis.getVoices();
if (window.speechSynthesis) window.speechSynthesis.addEventListener('voiceschanged', () => window.speechSynthesis.getVoices());

function speak(text) {
  if (!window.speechSynthesis) return;
  if (!document.getElementById('speech-enabled').checked) return;
  const utter = new SpeechSynthesisUtterance(text);
  utter.lang = 'en-GB';
  const voices = window.speechSynthesis.getVoices();
  const female = voices.find(v => v.lang === 'en-GB' && /female|woman|zira|samantha|karen|moira|fiona|victoria|tessa|alice|amelie|anna|emma|sara|susan/i.test(v.name))
              || voices.find(v => v.lang.startsWith('en') && /female|woman|zira|samantha|karen|moira|fiona|victoria|tessa|alice|amelie|anna|emma|sara|susan/i.test(v.name))
              || voices.find(v => v.lang === 'en-GB')
              || voices.find(v => v.lang.startsWith('en'));
  if (female) utter.voice = female;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utter);
}

function testSpeech() {
  speak(`${natoSuffix()} flight tracker speech test`);
}

function eventSpeechText(evType) {
  const id = natoSuffix();
  const phrases = {
    takeoff:         `${id} took off`,
    landing:         `${id} landed`,
    touch_and_go:    `${id} touch and go`,
    climbing_3000:   `${id} approaching 3500 feet`,
    climbing_5500:   `${id} approaching 6000 feet`,
    descending_3000: `${id} descending`,
    descending_5500: `${id} descending`,
  };
  return phrases[evType] || null;
}

function speakEvent(ev) {
  const text = eventSpeechText(ev.type);
  if (text) speak(text);
}

async function refreshEvents() {
  const resp = await fetch(`/api/events/${hex}`).then(r => r.json());

  // Update global lastTakeoffTs and announce any new events
  let _takeoffTs = null;
  for (const ev of resp.events) {
    const t = new Date(ev.ts);
    if (ev.type === 'takeoff')               { _takeoffTs = t; }
    if (ev.type === 'landing' && _takeoffTs) { _takeoffTs = null; }
    // touch_and_go: keep _takeoffTs (still airborne)
    if (!announcedEvents.has(ev.ts)) {
      announcedEvents.add(ev.ts);
      if (eventsInitialized) speakEvent(ev);
    }
  }
  lastTakeoffTs = _takeoffTs;
  eventsInitialized = true;

  renderEvents(resp.events, resp.agl_offset);
}

// ── 7-day history ─────────────────────────────────────────────────────────────
async function toggleHistory() {
  const panel = document.getElementById('history-panel');
  const btn   = document.getElementById('history-btn');
  if (_historyOpen) {
    panel.style.display = 'none';
    _historyOpen = false;
    return;
  }
  panel.innerHTML = '<div style="color:#555">Loading…</div>';
  panel.style.display = 'block';
  _historyOpen = true;
  const resp = await fetch(`/api/history/${hex}`).then(r => r.json());
  const days = resp.days;

  const th = s => `<th style="padding:4px 10px;text-align:right;color:#555;font-weight:normal">${s}</th>`;
  const td = (n, cls='') => `<td style="padding:4px 10px;text-align:right${cls ? ';color:'+cls : ''}">${n || '—'}</td>`;
  const fmt = d => new Date(d + 'T00:00:00').toLocaleDateString('nl-NL', { weekday: 'short', month: 'short', day: 'numeric' });

  let html = `<table style="font-size:13px;border-collapse:collapse">
    <thead><tr>
      ${th('date')}${th('T/O')}${th('LDG')}${th('T&G')}${th('↑3000')}${th('↑5500')}${th('↓5500')}${th('↓3000')}
    </tr></thead><tbody>`;
  for (const d of days) {
    const hasData = d.takeoffs || d.landings || d.touch_and_gos || d.climbing_3000 || d.descending_3000 || d.climbing_5500 || d.descending_5500;
    const rowColor = hasData ? '' : 'color:#444';
    html += `<tr style="${rowColor}">
      <td style="padding:4px 10px;color:#888">${fmt(d.date)}</td>
      ${td(d.takeoffs      || '')}
      ${td(d.landings      || '')}
      ${td(d.touch_and_gos || '')}
      ${td(d.climbing_3000   || '')}
      ${td(d.climbing_5500   || '')}
      ${td(d.descending_5500 || '')}
      ${td(d.descending_3000 || '')}
    </tr>`;
  }
  html += '</tbody></table>';
  panel.innerHTML = html;
}

// ── Announce view ─────────────────────────────────────────────────────────────
function _buildFollowSelect(currentHex) {
  const sel = document.getElementById('follow-select');
  if (!sel) return;
  const sorted = [...aircraft].sort((a, b) => {
    if (a.hex === DEF_HEX) return -1;
    if (b.hex === DEF_HEX) return  1;
    return a.registration.localeCompare(b.registration);
  });
  sel.innerHTML = sorted.map(a =>
    `<option value="${a.hex}"${a.hex === currentHex ? ' selected' : ''}>${a.registration}</option>`
  ).join('');
}

async function setFollowAircraft(hex) {
  await fetch('/api/feeder/follow', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ hex }),
  });
}

async function testFeederSound() {
  await fetch('/api/feeder/test', { method: 'POST' });
}

async function refreshAnnouncements() {
  const resp = await fetch('/api/announcements').then(r => r.json());

  _buildFollowSelect(resp.follow_hex);

  const statusEl = document.getElementById('feeder-status');
  if (resp.feeder_last_poll) {
    const secs = Math.round((Date.now() - new Date(resp.feeder_last_poll)) / 1000);
    statusEl.textContent = `feeder last polled ${secs}s ago`;
  } else {
    statusEl.textContent = 'feeder has not polled yet';
  }

  const logEl = document.getElementById('announce-log');
  if (!resp.announcements.length) {
    logEl.innerHTML = '<div style="color:#555">No announcements yet.</div>';
    return;
  }
  let html = '<table style="font-size:13px;border-collapse:collapse">';
  for (const a of resp.announcements) {
    const t = new Date(a.announced_at);
    const time = t.toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    html += `<tr>
      <td style="padding:4px 14px 4px 0;color:#555">${time}</td>
      <td style="padding:4px 0">${a.label}</td>
    </tr>`;
  }
  html += '</table>';
  logEl.innerHTML = html;
}

// ── Init ──────────────────────────────────────────────────────────────────────
async function init() {
  aircraft = await fetch('/api/aircraft').then(r => r.json());
  readUrlState();
  buildChart();
  showView(currentView);
  await refresh();
  setInterval(refresh, REFRESH_INTERVAL_MS);
  setInterval(() => { if (currentView === 'events')   refreshEvents(); }, REFRESH_INTERVAL_MS);
  setInterval(() => { if (currentView === 'announce') refreshAnnouncements(); }, REFRESH_INTERVAL_MS);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
}

init();

// ── Exports (for unit testing in Node.js) ────────────────────────────────────
if (typeof module !== 'undefined') {
  module.exports = { climbArrow, durStr, relTime, findGaps, buildSeries, regSuffixFor, natoSuffixFor };
}
