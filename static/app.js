/* Hermes Agent Dashboard — Frontend */

const CHART_THEME = { mode: 'dark', palette: 'palette4' };
const COLORS = ['#38bdf8','#a855f7','#4ade80','#f59e0b','#f87171','#ec4899','#06b6d4','#84cc16'];
const MODEL_COLORS = {
    'claude-haiku': '#fde68a',
    'claude-sonnet': '#67e8f9',
    'claude-opus': '#c084fc',
    'deepseek-v4.1-flash': '#34d399',
    'minimax-m2.7': '#a3e635',
    'kimi-k2.6': '#fb923c',
};

function modelColor(name) {
    for (const [k,v] of Object.entries(MODEL_COLORS)) {
        if (name.includes(k)) return v;
    }
    return COLORS[Math.abs(hashCode(name)) % COLORS.length];
}
function hashCode(s) {
    let h = 0;
    for (let i = 0; i < s.length; i++) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
    return h;
}

function getDays() {
    return parseInt(document.getElementById('days-select').value, 10);
}

async function api(path) {
    const sep = path.includes('?') ? '&' : '?';
    const r = await fetch(path + sep + '_=' + Date.now());
    return r.json();
}

function fmt(n) {
    if (n >= 1e6) return (n/1e6).toFixed(1) + 'M';
    if (n >= 1e3) return (n/1e3).toFixed(1) + 'K';
    return String(n);
}
function usd(n) { return '$' + Number(n).toFixed(2); }
function usd6(n) { return '$' + Number(n).toFixed(6); }
function shortModel(m) { return m.replace(/^claude-/,'').replace(/-2025\d+$/,''); }
function shortTime(ts) {
    if (!ts) return '—';
    const hasTZ = /Z|[+-]\d{2}:\d{2}$/.test(ts);
    const d = new Date(ts.includes('T') && !hasTZ ? ts + 'Z' : ts);
    if (isNaN(d.getTime())) return ts;
    return d.toLocaleString('en-US', { month:'short', day:'numeric', hour:'2-digit', minute:'2-digit', second:'2-digit' });
}

const charts = {};
const DARK_GRID = { borderColor: 'rgba(255,255,255,0.06)' };
const DARK_TOOLTIP = { theme: 'dark' };
const DARK_AXIS = { style: { colors: '#94a3b8', fontSize: '10px' } };

function renderChart(id, opts) {
    if (charts[id]) { charts[id].destroy(); }
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = '';
    opts.grid = { ...DARK_GRID, ...(opts.grid || {}) };
    opts.tooltip = { ...DARK_TOOLTIP, ...(opts.tooltip || {}) };
    const c = new ApexCharts(el, { ...opts, chart: { ...opts.chart, background: 'transparent', foreColor: '#94a3b8' } });
    c.render();
    charts[id] = c;
}

// ── Page Routing ──

const PAGE_LOADERS = {
    overview:    [loadKPIs, loadDailyUsage, loadDailyCost, loadModelDist, loadOverviewBadges],
    tokens:      [loadTokenAnalysis],
    usage:       [loadContextDepth, loadOutputDist, loadHeatmap, loadRecentRequests, loadTopCostDays],
    platforms:   [loadPlatformUsage, loadPlatformSummary, loadRecentSessions],
    tools:       [loadToolUsage, loadToolTimeline],
    reliability: [loadErrorLogStats, loadErrors, loadCache, loadSystemHealth],
    forensics:   [loadTokenForensics],
    operations:  [loadCronJobs, loadUptime, loadSystemHealth],
};

let currentPage = 'overview';

function navigateTo(page) {
    if (!PAGE_LOADERS[page]) page = 'overview';
    currentPage = page;
    document.querySelectorAll('div[data-page]').forEach(el => el.classList.remove('active'));
    const target = document.querySelector(`div[data-page="${page}"]`);
    if (target) target.classList.add('active');
    document.querySelectorAll('.sidebar-link').forEach(link => {
        link.classList.toggle('active', link.dataset.page === page);
    });
    loadPage(page);
    if (location.hash.slice(1) !== page) {
        history.replaceState(null, '', '#' + page);
    }
}

async function loadPage(page) {
    const loaders = PAGE_LOADERS[page] || [];
    await Promise.all(loaders.map(fn => fn()));
}

// ── KPIs ──
async function loadKPIs() {
    const d = await api('/api/kpis');
    document.getElementById('kpi-tokens').textContent = fmt(d.tokens_today);
    document.getElementById('kpi-cost-today').textContent = usd(d.cost_today);
    document.getElementById('kpi-requests').textContent = d.requests_today;
    document.getElementById('kpi-sessions').textContent = d.sessions_today;
    document.getElementById('kpi-cost-month').textContent = usd(d.cost_month);
    document.getElementById('kpi-projected').textContent = usd(d.projected_monthly);

    const yestEl = document.getElementById('kpi-yesterday');
    if (yestEl) {
        const yt = d.tokens_yesterday || 0;
        const yc = d.cost_yesterday || 0;
        yestEl.innerHTML = `<div class="card-label">Yesterday</div><div class="card-value">${fmt(yt)}</div><div class="card-sub">${usd(yc)}</div>`;
    }
    const weekEl = document.getElementById('kpi-7d');
    if (weekEl) {
        const wt = d.tokens_7d || 0;
        const wc = d.cost_7d || 0;
        weekEl.innerHTML = `<div class="card-label">Last 7 Days</div><div class="card-value">${fmt(wt)}</div><div class="card-sub">${usd(wc)}</div>`;
    }

    const banner = document.getElementById('alert-banner');
    if (d.alerts && d.alerts.length) {
        banner.textContent = '\u26A0 ' + d.alerts.join(' \u00B7 ');
        banner.classList.remove('hidden');
    } else {
        banner.classList.add('hidden');
    }
}

// ── Overview Badges ──
async function loadOverviewBadges() {
    try {
        const data = await api('/api/token-analysis');
        const s = data.summary || {};
        const el = document.getElementById('overview-badges');
        if (!el) return;
        const retries = s.retries_today || 0;
        const toolCalls = s.total_tool_calls || 0;
        const deepest = s.deepest_session_messages || 0;
        const waste = (s.estimated_waste_pct || 0).toFixed(1);
        el.innerHTML =
            `<span class="summary-badge ${retries > 0 ? 'alert' : ''}">\u21BB ${retries} retries today</span>` +
            `<span class="summary-badge">\u2699 ${toolCalls} total tool calls</span>` +
            `<span class="summary-badge">\u2195 ${deepest} deepest session msgs</span>` +
            `<span class="summary-badge ${parseFloat(waste)>5 ? 'alert' : ''}">\u26A0 ${waste}% est. waste</span>`;
    } catch {
        // token-analysis API not available yet
    }
}

// ── Daily Usage ──
async function loadDailyUsage() {
    const data = await api('/api/daily-usage?days=' + getDays());
    const models = [...new Set(data.map(r => r.model))];
    const days = [...new Set(data.map(r => r.day))].sort();
    const series = models.map(m => ({
        name: shortModel(m),
        data: days.map(d => {
            const row = data.find(r => r.day === d && r.model === m);
            return row ? row.total_tok : 0;
        }),
        color: modelColor(m),
    }));
    renderChart('chart-daily-usage', {
        chart: { type: 'bar', height: 280, stacked: true, toolbar: { show: false } },
        series, xaxis: { categories: days, labels: { style: { colors: '#94a3b8', fontSize: '10px' } } },
        yaxis: { labels: { formatter: fmt, style: { colors: '#94a3b8' } } },
        plotOptions: { bar: { borderRadius: 2, columnWidth: '70%' } },
        dataLabels: { enabled: false }, grid: { borderColor: 'rgba(148,163,184,0.08)' },
        tooltip: { theme: 'dark', y: { formatter: fmt } }, legend: { position: 'top' },
    });
}

// ── Daily Cost ──
async function loadDailyCost() {
    const data = await api('/api/daily-cost?days=' + getDays());
    const models = [...new Set(data.map(r => r.model))];
    const days = [...new Set(data.map(r => r.day))].sort();
    const series = models.map(m => ({
        name: shortModel(m),
        data: days.map(d => {
            const row = data.find(r => r.day === d && r.model === m);
            return row ? +row.cost.toFixed(2) : 0;
        }),
        color: modelColor(m),
    }));
    renderChart('chart-daily-cost', {
        chart: { type: 'line', height: 280, toolbar: { show: false } },
        series, xaxis: { categories: days, labels: { style: { colors: '#94a3b8', fontSize: '10px' } } },
        yaxis: { labels: { formatter: v => '$'+v.toFixed(0), style: { colors: '#94a3b8' } } },
        stroke: { width: 2, curve: 'smooth' },
        dataLabels: { enabled: false }, grid: { borderColor: 'rgba(148,163,184,0.08)' },
        tooltip: { theme: 'dark', y: { formatter: v => usd(v) } }, legend: { position: 'top' },
    });
}

// ── Model Distribution ──
async function loadModelDist() {
    const data = await api('/api/model-distribution?days=' + getDays());
    if (!data.length) {
        document.getElementById('chart-model-dist').innerHTML = '<p class="text-slate-400 text-sm p-8 text-center">No model data yet.</p>';
        return;
    }
    renderChart('chart-model-dist', {
        chart: { type: 'donut', height: 280 },
        series: data.map(r => r.total_tokens),
        labels: data.map(r => shortModel(r.model)),
        colors: data.map(r => modelColor(r.model)),
        dataLabels: { enabled: true, style: { fontSize: '11px' } },
        plotOptions: { pie: { donut: { size: '55%' } } },
        tooltip: { theme: 'dark', y: { formatter: v => fmt(v) + ' tokens' } },
        legend: { position: 'bottom', labels: { colors: '#94a3b8' } },
    });
}

// ── Token Analysis (full page) ──
async function loadTokenAnalysis() {
    let data;
    try {
        data = await api('/api/token-analysis');
    } catch {
        return;
    }
    const s = data.summary || {};

    // Cost Driver KPIs
    const kpiEl = document.getElementById('token-kpis');
    if (kpiEl) {
        kpiEl.innerHTML =
            `<div class="card"><div class="card-label">Total Retries</div><div class="card-value" style="color:#f87171">${s.total_retries || 0}</div></div>` +
            `<div class="card"><div class="card-label">Total Tool Calls</div><div class="card-value" style="color:#38bdf8">${fmt(s.total_tool_calls || 0)}</div></div>` +
            `<div class="card"><div class="card-label">Deepest Session</div><div class="card-value" style="color:#a855f7">${s.deepest_session_messages || 0} msgs</div></div>` +
            `<div class="card"><div class="card-label">Est. Waste</div><div class="card-value" style="color:#fbbf24">${(s.estimated_waste_pct || 0).toFixed(1)}%</div></div>`;
    }

    // Retry Waste Timeline
    const timeline = data.retry_timeline || [];
    if (timeline.length) {
        const days = timeline.map(r => r.day);
        renderChart('chart-retry-waste', {
            chart: { type: 'area', height: 280, stacked: true, toolbar: { show: false } },
            series: [
                { name: 'Warnings', data: timeline.map(r => r.warnings || 0), color: '#fbbf24' },
                { name: 'Errors', data: timeline.map(r => r.errors || 0), color: '#f87171' },
            ],
            xaxis: { categories: days, labels: { style: { colors: '#94a3b8', fontSize: '10px' } } },
            yaxis: { labels: { style: { colors: '#94a3b8' } } },
            stroke: { width: 2, curve: 'smooth' },
            fill: { type: 'solid', opacity: 0.3 },
            dataLabels: { enabled: false },
            grid: { borderColor: 'rgba(148,163,184,0.08)' },
            tooltip: { theme: 'dark' },
            legend: { position: 'top' },
        });
    } else {
        document.getElementById('chart-retry-waste').innerHTML = '<p class="text-slate-400 text-sm p-8 text-center">No retry data available yet.</p>';
    }

    // Tool Call Intensity
    const tools = (data.top_tools || []).slice(0, 15);
    if (tools.length) {
        renderChart('chart-tool-intensity', {
            chart: { type: 'bar', height: 280, toolbar: { show: false } },
            series: [{ name: 'Calls', data: tools.map(r => r.count), color: '#38bdf8' }],
            xaxis: { categories: tools.map(r => r.name), labels: { style: { colors: '#94a3b8', fontSize: '10px' } } },
            yaxis: { labels: { style: { colors: '#94a3b8' } } },
            plotOptions: { bar: { horizontal: true, borderRadius: 3, barHeight: '65%' } },
            dataLabels: { enabled: false },
            grid: { borderColor: 'rgba(148,163,184,0.08)' },
            tooltip: { theme: 'dark' },
        });
    } else {
        document.getElementById('chart-tool-intensity').innerHTML = '<p class="text-slate-400 text-sm p-8 text-center">No tool call data yet.</p>';
    }

    // Session Depth Ranking
    const sessions = data.session_depth || [];
    const depthEl = document.getElementById('session-depth-table');
    if (depthEl) {
        if (sessions.length) {
            const pColors = { telegram:'blue', whatsapp:'green', cli:'purple', discord:'yellow', slack:'red' };
            depthEl.innerHTML = '<table class="data-table"><thead><tr><th>#</th><th>Source</th><th>Title</th><th>Msgs</th><th>Tools</th><th>Est Tokens</th></tr></thead><tbody>'
                + sessions.map((r, i) => `<tr>
                    <td>${i + 1}</td>
                    <td><span class="badge badge-${pColors[r.source]||'gray'}">${r.source||'\u2014'}</span></td>
                    <td class="text-slate-200">${r.title || '\u2014'}</td>
                    <td>${r.messages || 0}</td>
                    <td>${r.tool_calls || 0}</td>
                    <td>${fmt(r.est_tokens || 0)}</td>
                </tr>`).join('')
                + '</tbody></table>';
        } else {
            depthEl.innerHTML = '<p class="text-slate-400 text-sm p-8 text-center">No session depth data yet.</p>';
        }
    }

    // Token Breakdown Estimate
    const breakdown = data.token_breakdown || {};
    const bValues = [breakdown.retry_waste || 0, breakdown.tool_overhead || 0, breakdown.useful_output || 0];
    if (bValues.some(v => v > 0)) {
        renderChart('chart-token-breakdown', {
            chart: { type: 'donut', height: 280 },
            series: bValues,
            labels: ['Retry Waste', 'Tool Overhead', 'Useful Output'],
            colors: ['#f87171', '#f59e0b', '#4ade80'],
            dataLabels: { enabled: true, style: { fontSize: '11px' } },
            plotOptions: { pie: { donut: { size: '55%' } } },
            tooltip: { theme: 'dark', y: { formatter: v => fmt(v) + ' tokens' } },
            legend: { position: 'bottom', labels: { colors: '#94a3b8' } },
        });
    } else {
        document.getElementById('chart-token-breakdown').innerHTML = '<p class="text-slate-400 text-sm p-8 text-center">No token breakdown data yet.</p>';
    }
}

// ── Platform Usage ──
async function loadPlatformUsage() {
    const data = await api('/api/platform-usage?days=' + getDays());
    if (!data.length) {
        document.getElementById('chart-platform-usage').innerHTML = '<p class="text-slate-400 text-sm p-8 text-center">Platform data from Hermes state.db will appear here once sessions are recorded.</p>';
        return;
    }
    const platforms = [...new Set(data.map(r => r.platform))];
    const days = [...new Set(data.map(r => r.day))].sort();
    const pColors = { telegram: '#38bdf8', whatsapp: '#4ade80', cli: '#a855f7', discord: '#f59e0b', slack: '#f87171' };
    const series = platforms.map(p => ({
        name: p, color: pColors[p] || COLORS[0],
        data: days.map(d => { const r = data.find(x => x.day===d && x.platform===p); return r ? (r.total_tokens||r.sessions||0) : 0; }),
    }));
    renderChart('chart-platform-usage', {
        chart: { type: 'bar', height: 280, stacked: true, toolbar: { show: false } },
        series, xaxis: { categories: days, labels: { style: { colors: '#94a3b8', fontSize: '10px' } } },
        yaxis: { labels: { formatter: fmt, style: { colors: '#94a3b8' } } },
        plotOptions: { bar: { borderRadius: 2, columnWidth: '70%' } },
        dataLabels: { enabled: false }, grid: { borderColor: 'rgba(148,163,184,0.08)' },
        tooltip: { theme: 'dark' }, legend: { position: 'top' },
    });
}

async function loadPlatformSummary() {
    const data = await api('/api/platform-summary?days=' + getDays());
    const el = document.getElementById('platform-summary');
    if (!data.length) { el.innerHTML = '<p class="text-slate-400 text-sm">No platform data yet.</p>'; return; }
    const pColors = { telegram: 'blue', whatsapp: 'green', cli: 'purple', discord: 'yellow', slack: 'red', cron: 'purple' };
    el.innerHTML = '<table class="data-table"><thead><tr><th>Platform</th><th>Sessions</th><th>Msgs</th><th>Tools</th><th>Tokens</th></tr></thead><tbody>'
        + data.map(r => `<tr><td><span class="badge badge-${pColors[r.platform]||'gray'}">${r.platform}</span></td><td>${r.sessions}</td><td>${r.total_messages||0}</td><td>${r.total_tool_calls||0}</td><td>${fmt(r.total_tokens||0)}</td></tr>`).join('')
        + '</tbody></table>';
}

// ── Tool Usage ──
async function loadToolUsage() {
    const data = await api('/api/tool-usage?days=' + getDays());
    if (!data.length) {
        document.getElementById('chart-tool-usage').innerHTML = '<p class="text-slate-400 text-sm p-8 text-center">Tool usage data will appear once Hermes state.db has tool call records.</p>';
        return;
    }
    renderChart('chart-tool-usage', {
        chart: { type: 'bar', height: 280, toolbar: { show: false } },
        series: [{ name: 'Calls', data: data.map(r => r.count), color: '#38bdf8' }],
        xaxis: { categories: data.map(r => r.tool_name), labels: { style: { colors: '#94a3b8', fontSize: '10px' } } },
        yaxis: { labels: { style: { colors: '#94a3b8' } } },
        plotOptions: { bar: { horizontal: true, borderRadius: 3, barHeight: '65%' } },
        dataLabels: { enabled: false }, grid: { borderColor: 'rgba(148,163,184,0.08)' },
        tooltip: { theme: 'dark' },
    });
}

async function loadToolTimeline() {
    const data = await api('/api/tool-usage-timeline?days=' + getDays());
    if (!data.length) {
        document.getElementById('chart-tool-timeline').innerHTML = '<p class="text-slate-400 text-sm p-8 text-center">Tool timeline will appear with usage data.</p>';
        return;
    }
    const cats = [...new Set(data.map(r => r.category))];
    const days = [...new Set(data.map(r => r.day))].sort();
    const catColors = { web:'#38bdf8', terminal:'#a855f7', file:'#4ade80', browser:'#f59e0b', memory:'#ec4899', orchestration:'#06b6d4', automation:'#84cc16', other:'#64748b' };
    const series = cats.map(c => ({
        name: c, color: catColors[c] || COLORS[0],
        data: days.map(d => { const r = data.find(x => x.day===d && x.category===c); return r ? r.count : 0; }),
    }));
    renderChart('chart-tool-timeline', {
        chart: { type: 'area', height: 280, stacked: true, toolbar: { show: false } },
        series, xaxis: { categories: days, labels: { style: { colors: '#94a3b8', fontSize: '10px' } } },
        yaxis: { labels: { style: { colors: '#94a3b8' } } },
        stroke: { width: 1, curve: 'smooth' }, fill: { type: 'solid', opacity: 0.3 },
        dataLabels: { enabled: false }, grid: { borderColor: 'rgba(148,163,184,0.08)' },
        tooltip: { theme: 'dark' }, legend: { position: 'top' },
    });
}

// ── Context Depth + Output Distribution ──
async function loadContextDepth() {
    const data = await api('/api/context-depth?days=' + getDays());
    if (!data.histogram || !data.histogram.length) {
        document.getElementById('chart-context-depth').innerHTML = '<p class="text-slate-400 text-sm p-8 text-center">Context depth data will appear after API requests are proxied.</p>';
        return;
    }
    renderChart('chart-context-depth', {
        chart: { type: 'bar', height: 280, toolbar: { show: false } },
        series: [{ name: 'Requests', data: data.histogram.map(r => r.count), color: '#a855f7' }],
        xaxis: { categories: data.histogram.map(r => r.label), labels: { style: { colors: '#94a3b8', fontSize: '9px' }, rotate: -45 } },
        yaxis: { labels: { style: { colors: '#94a3b8' } } },
        plotOptions: { bar: { borderRadius: 3, columnWidth: '70%' } },
        dataLabels: { enabled: false }, grid: { borderColor: 'rgba(148,163,184,0.08)' },
        tooltip: { theme: 'dark' },
    });
}

async function loadOutputDist() {
    const data = await api('/api/output-distribution?days=' + getDays());
    if (!data.length || data.every(r => r.count === 0)) {
        document.getElementById('chart-output-dist').innerHTML = '<p class="text-slate-400 text-sm p-8 text-center">Output distribution will appear after proxied requests.</p>';
        return;
    }
    renderChart('chart-output-dist', {
        chart: { type: 'bar', height: 280, toolbar: { show: false } },
        series: [{ name: 'Requests', data: data.map(r => r.count), color: '#4ade80' }],
        xaxis: { categories: data.map(r => r.label), labels: { style: { colors: '#94a3b8', fontSize: '10px' } } },
        yaxis: { labels: { style: { colors: '#94a3b8' } } },
        plotOptions: { bar: { borderRadius: 3, columnWidth: '60%' } },
        dataLabels: { enabled: false }, grid: { borderColor: 'rgba(148,163,184,0.08)' },
        tooltip: { theme: 'dark' },
    });
}

// ── Heatmap ──
async function loadHeatmap() {
    const data = await api('/api/hourly-heatmap?days=' + getDays());
    if (!data.length) {
        document.getElementById('chart-heatmap').innerHTML = '<p class="text-slate-400 text-sm p-8 text-center">Heatmap will populate with activity data.</p>';
        return;
    }
    const dayNames = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
    const series = dayNames.map((name, dow) => ({
        name,
        data: Array.from({length:24}, (_, h) => {
            const r = data.find(x => x.dow === dow && x.hour === h);
            return { x: String(h).padStart(2,'0'), y: r ? r.count : 0 };
        }),
    }));
    renderChart('chart-heatmap', {
        chart: { type: 'heatmap', height: 280, toolbar: { show: false } },
        series, dataLabels: { enabled: false },
        colors: ['#38bdf8'],
        plotOptions: { heatmap: { radius: 2, colorScale: { ranges: [
            { from: 0, to: 0, color: '#1e293b' },
            { from: 1, to: 5, color: '#0c4a6e' },
            { from: 6, to: 20, color: '#0284c7' },
            { from: 21, to: 100, color: '#38bdf8' },
            { from: 101, to: 10000, color: '#7dd3fc' },
        ]}}},
        xaxis: { labels: { style: { colors: '#94a3b8', fontSize: '9px' } } },
        yaxis: { labels: { style: { colors: '#94a3b8', fontSize: '10px' } } },
        tooltip: { theme: 'dark' },
    });
}

// ── Error Log Stats (chart from /api/error-log-stats) ──
async function loadErrorLogStats() {
    let data;
    try {
        data = await api('/api/error-log-stats?days=' + getDays());
    } catch {
        data = null;
    }
    if (!data || !data.daily || !data.daily.length) {
        // Fallback to proxy error data for the chart
        try {
            data = await api('/api/errors?days=' + getDays());
        } catch {
            data = null;
        }
    }
    if (!data || !data.daily || !data.daily.length) {
        document.getElementById('chart-errors').innerHTML = '<p class="text-slate-400 text-sm p-8 text-center">No error data yet \u2014 this is a good thing!</p>';
        return;
    }
    const days = data.daily.map(r => r.day);
    renderChart('chart-errors', {
        chart: { type: 'bar', height: 260, toolbar: { show: false } },
        series: [
            { name: 'Errors', data: data.daily.map(r => r.errors || 0), color: '#f87171' },
            { name: 'OK', data: data.daily.map(r => (r.total || 0) - (r.errors || 0)), color: '#4ade8050' },
        ],
        xaxis: { categories: days, labels: { style: { colors: '#94a3b8', fontSize: '10px' } } },
        yaxis: { labels: { style: { colors: '#94a3b8' } } },
        plotOptions: { bar: { stacked: true, borderRadius: 2 } },
        dataLabels: { enabled: false }, grid: { borderColor: 'rgba(148,163,184,0.08)' },
        tooltip: { theme: 'dark' }, legend: { position: 'top' },
    });
}

// ── Errors (breakdown only) ──
async function loadErrors() {
    let data;
    try {
        data = await api('/api/errors?days=' + getDays());
    } catch {
        data = null;
    }
    const eb = document.getElementById('error-breakdown');
    if (!data || !data.by_status || !data.by_status.length) {
        eb.innerHTML = '';
        return;
    }
    eb.innerHTML = '<table class="data-table"><tbody>'
        + data.by_status.map(r => `<tr><td><span class="badge badge-red">${r.status_code}</span></td><td>${r.count} requests</td></tr>`).join('')
        + '</tbody></table>';
}

// ── Cache Efficiency ──
async function loadCache() {
    const data = await api('/api/cache-efficiency?days=' + getDays());
    const el = document.getElementById('cache-efficiency');
    const pct = data.efficiency_pct || 0;
    const color = pct > 50 ? '#4ade80' : pct > 20 ? '#f59e0b' : '#f87171';
    el.innerHTML = `
        <div class="text-4xl font-bold" style="color:${color}">${pct}%</div>
        <div class="text-xs text-slate-400 mt-1">Cache Hit Rate</div>
        <div class="text-xs text-slate-500 mt-3">Read: ${fmt(data.cache_read_tokens)} \u00B7 Write: ${fmt(data.cache_write_tokens)}</div>
    `;
}

// ── Recent Requests ──
async function loadRecentRequests() {
    const data = await api('/api/recent-requests?limit=50');
    const tbody = document.querySelector('#requests-table tbody');
    tbody.innerHTML = data.map(r => {
        const statusClass = r.status_code >= 400 ? 'badge-red' : 'badge-green';
        return `<tr>
            <td>${shortTime(r.ts)}</td>
            <td>${shortModel(r.model)}</td>
            <td>${fmt(r.input_tokens)}</td>
            <td>${fmt(r.output_tokens)}</td>
            <td>${usd6(r.cost_usd)}</td>
            <td>${(r.latency_ms/1000).toFixed(1)}s</td>
            <td><span class="badge ${statusClass}">${r.status_code}</span></td>
        </tr>`;
    }).join('');
}

// ── Top Cost Days ──
async function loadTopCostDays() {
    const data = await api('/api/top-cost-days?limit=10');
    const el = document.getElementById('top-cost-days');
    if (!data.length) { el.innerHTML = '<p class="text-slate-400 text-sm">No data yet.</p>'; return; }
    el.innerHTML = '<table class="data-table"><thead><tr><th>Day</th><th>Cost</th><th>Tokens</th><th>Reqs</th></tr></thead><tbody>'
        + data.map(r => `<tr><td>${r.day}</td><td class="font-semibold">${usd(r.cost)}</td><td>${fmt(r.tokens)}</td><td>${r.requests}</td></tr>`).join('')
        + '</tbody></table>';
}

// ── Cron Jobs ──
async function loadCronJobs() {
    const data = await api('/api/cron-jobs');
    const el = document.getElementById('cron-jobs');
    if (!data.length) { el.innerHTML = '<p class="text-slate-400 text-sm">No cron jobs found or hermes CLI not available.</p>'; return; }

    if (data[0].schedule !== undefined) {
        el.innerHTML = '<table class="data-table"><thead><tr><th>Name</th><th>Schedule</th><th>Status</th><th>Next Run</th><th>Deliver</th></tr></thead><tbody>'
            + data.map(r => {
                const statusBadge = r.status === 'active'
                    ? '<span class="badge badge-green">active</span>'
                    : '<span class="badge badge-yellow">' + (r.status || 'unknown') + '</span>';
                return `<tr>
                    <td class="text-slate-200 font-semibold">${r.name}</td>
                    <td><code class="text-xs text-slate-400">${r.schedule}</code></td>
                    <td>${statusBadge}</td>
                    <td class="text-slate-400">${shortTime(r.next_run)}</td>
                    <td class="text-slate-500">${r.deliver || '\u2014'}</td>
                </tr>`;
            }).join('')
            + '</tbody></table>';
    } else {
        el.innerHTML = '<table class="data-table"><tbody>'
            + data.map(r => `<tr><td class="font-semibold text-slate-200">${r.name}</td><td class="text-slate-400">${r.details}</td></tr>`).join('')
            + '</tbody></table>';
    }
}

// ── Uptime ──
async function loadUptime() {
    const data = await api('/api/uptime-timeline?hours=168');
    if (!data.length) {
        document.getElementById('chart-uptime').innerHTML = '<p class="text-slate-400 text-sm p-8 text-center">Uptime data from journalctl will appear on the server.</p>';
        return;
    }
    const events = data.map(e => ({ x: 'Gateway', y: [e.ts * 1000, (e.ts + 3600) * 1000], fillColor: e.event === 'start' ? '#4ade80' : '#f87171' }));
    renderChart('chart-uptime', {
        chart: { type: 'rangeBar', height: 100, toolbar: { show: false } },
        series: [{ data: events }],
        plotOptions: { bar: { horizontal: true, rangeBarGroupRows: true } },
        xaxis: { type: 'datetime', labels: { style: { colors: '#94a3b8', fontSize: '10px' } } },
        yaxis: { labels: { style: { colors: '#94a3b8' } } },
        tooltip: { theme: 'dark' },
    });
}

// ── System Health ──
async function loadSystemHealth() {
    const data = await api('/api/system-health');

    const badge = document.getElementById('gateway-badge');
    const gw = data.gateway || {};
    if (gw.status === 'active') {
        badge.innerHTML = '<span class="status-dot status-active"></span>Active';
        badge.className = 'ml-2 text-xs px-2 py-0.5 rounded-full bg-green-900 bg-opacity-40 text-green-400';
    } else {
        badge.innerHTML = '<span class="status-dot status-inactive"></span>' + (gw.status || 'Unknown');
        badge.className = 'ml-2 text-xs px-2 py-0.5 rounded-full bg-red-900 bg-opacity-40 text-red-400';
    }

    const res = data.resources || {};
    const el = document.getElementById('system-resources');
    function gauge(label, pct, detail) {
        const color = pct > 90 ? '#f87171' : pct > 70 ? '#f59e0b' : '#4ade80';
        return `<div>
            <div class="flex justify-between text-xs mb-1"><span class="text-slate-300">${label}</span><span class="text-slate-400">${detail}</span></div>
            <div class="gauge-bar"><div class="gauge-fill" style="width:${pct}%;background:${color}"></div></div>
        </div>`;
    }
    if (el) {
        el.innerHTML = gauge('CPU', res.cpu_percent||0, (res.cpu_percent||0)+'%')
            + gauge('Memory', res.memory_percent||0, `${res.memory_used_gb||0}/${res.memory_total_gb||0} GB`)
            + gauge('Disk', res.disk_percent||0, `${res.disk_used_gb||0}/${res.disk_total_gb||0} GB`)
            + `<div class="text-xs text-slate-500 mt-2">Uptime: ${res.uptime_hours||0}h \u00B7 Gateway since: ${gw.active_since||'\u2014'}</div>`;
        const db = data.db_sizes || {};
        if (db.state_db_mb || db.dashboard_db_mb) {
            el.innerHTML += `<div class="text-xs text-slate-500 mt-1">state.db: ${db.state_db_mb||'\u2014'}MB \u00B7 dashboard.db: ${db.dashboard_db_mb||'\u2014'}MB</div>`;
        }
    }

    const skillsEl = document.getElementById('skills-list');
    const skills = data.skills || [];
    if (skillsEl) {
        if (skills.length) {
            skillsEl.innerHTML = skills.map(s => `<div class="flex justify-between text-xs py-1 border-b border-slate-700"><span class="text-slate-200">${s.name}</span><span class="text-slate-500">${s.created}</span></div>`).join('');
        } else {
            skillsEl.innerHTML = '<p class="text-slate-400 text-sm">No skills installed.</p>';
        }
    }

    const errEl = document.getElementById('error-log');
    const errs = data.recent_errors || [];
    if (errEl) {
        errEl.innerHTML = errs.length ? errs.map(e => `<div class="py-0.5">${e}</div>`).join('') : '<p class="text-slate-500">No recent errors.</p>';
    }
}

// ── Recent Sessions ──
async function loadRecentSessions() {
    const data = await api('/api/recent-sessions?limit=15');
    const el = document.getElementById('recent-sessions');
    if (!data.length) { el.innerHTML = '<p class="text-slate-400 text-sm">No sessions from state.db.</p>'; return; }
    const pColors = { telegram: 'blue', whatsapp: 'green', cli: 'purple', discord: 'yellow', slack: 'red', cron: 'purple' };
    el.innerHTML = data.map(s => `
        <div class="flex items-start gap-2 py-2 border-b border-slate-700">
            <span class="badge badge-${pColors[s.source]||'gray'} mt-0.5">${s.source||'?'}</span>
            <div class="flex-1 min-w-0">
                <div class="text-xs text-slate-200 truncate">${s.title || s.id}</div>
                <div class="text-xs text-slate-500">${shortTime(s.started_at)} \u00B7 ${shortModel(s.model||'\u2014')} \u00B7 ${s.message_count||0} msgs \u00B7 ${fmt(s.total_tokens||0)} tok</div>
            </div>
        </div>
    `).join('');
}

// ── Token Forensics ──
async function loadTokenForensics() {
    let data;
    try { data = await api('/api/token-forensics'); } catch { return; }

    const fo = data.fixed_overhead || {};
    const totals = data.totals || {};
    const kpiEl = document.getElementById('forensics-kpis');
    if (kpiEl) {
        kpiEl.innerHTML =
            `<div class="card"><div class="card-label">Fixed Overhead / Request</div><div class="card-value" style="color:#f87171">${fmt(fo.total_fixed || 0)}</div><div class="card-sub">system + ${fo.tool_count || 0} tools</div></div>` +
            `<div class="card"><div class="card-label">System Prompt</div><div class="card-value" style="color:#a855f7">${fmt(fo.system_prompt_tokens || 0)}</div><div class="card-sub">tokens per request</div></div>` +
            `<div class="card"><div class="card-label">Tool Definitions</div><div class="card-value" style="color:#f59e0b">${fmt(fo.tool_definitions_tokens || 0)}</div><div class="card-sub">${fo.tool_count || 0} tools loaded</div></div>` +
            `<div class="card"><div class="card-label">Est. Total Input</div><div class="card-value" style="color:#38bdf8">${fmt(totals.total_estimated_input || 0)}</div><div class="card-sub">~${totals.total_api_calls || 0} API calls</div></div>`;
    }

    const dumps = data.request_dumps || [];
    if (dumps.length) {
        const labels = dumps.map(d => d.timestamp ? d.timestamp.split('T')[1] || d.timestamp.slice(11) : '?');
        renderChart('chart-request-breakdown', {
            chart: { type: 'bar', height: 300, stacked: true, toolbar: { show: false } },
            series: [
                { name: 'System Prompt', data: dumps.map(d => d.system_tokens), color: '#a855f7' },
                { name: 'Tool Definitions', data: dumps.map(d => d.tool_def_tokens), color: '#f59e0b' },
                { name: 'Messages', data: dumps.map(d => d.message_tokens), color: '#38bdf8' },
            ],
            xaxis: { categories: labels, labels: { style: { colors: '#94a3b8', fontSize: '10px' }, rotate: -45 } },
            yaxis: { labels: { formatter: fmt, style: { colors: '#94a3b8' } } },
            plotOptions: { bar: { borderRadius: 2, columnWidth: '70%' } },
            dataLabels: { enabled: false },
            tooltip: { theme: 'dark', y: { formatter: v => fmt(v) + ' tokens' } },
            legend: { position: 'top' },
        });
    } else {
        document.getElementById('chart-request-breakdown').innerHTML = '<p class="text-slate-400 text-sm p-8 text-center">No request dumps found in ~/.hermes/sessions/. Hermes creates these on API failures.</p>';
    }

    const toolCosts = data.tool_token_costs || [];
    if (toolCosts.length) {
        renderChart('chart-tool-costs', {
            chart: { type: 'bar', height: 300, toolbar: { show: false } },
            series: [{ name: 'Tokens', data: toolCosts.map(t => t.tokens), color: '#f59e0b' }],
            xaxis: { categories: toolCosts.map(t => t.name), labels: { style: { colors: '#94a3b8', fontSize: '9px' } } },
            yaxis: { labels: { formatter: fmt, style: { colors: '#94a3b8' } } },
            plotOptions: { bar: { horizontal: true, borderRadius: 3, barHeight: '60%' } },
            dataLabels: { enabled: false },
            tooltip: { theme: 'dark', y: { formatter: v => v + ' tokens per request' } },
        });
    } else {
        document.getElementById('chart-tool-costs').innerHTML = '<p class="text-slate-400 text-sm p-8 text-center">No tool definition data available yet.</p>';
    }

    if (fo.total_fixed > 0) {
        const variableAvg = Math.max((totals.avg_input_per_call || 0) - (fo.total_fixed || 0), 0);
        renderChart('chart-cost-composition', {
            chart: { type: 'donut', height: 280 },
            series: [fo.system_prompt_tokens || 0, fo.tool_definitions_tokens || 0, variableAvg],
            labels: ['System Prompt', 'Tool Definitions', 'Messages (avg)'],
            colors: ['#a855f7', '#f59e0b', '#38bdf8'],
            dataLabels: { enabled: true, style: { fontSize: '11px' } },
            plotOptions: { pie: { donut: { size: '55%' } } },
            tooltip: { theme: 'dark', y: { formatter: v => fmt(v) + ' tokens' } },
            legend: { position: 'bottom', labels: { colors: '#94a3b8' } },
        });
    } else {
        document.getElementById('chart-cost-composition').innerHTML = '<p class="text-slate-400 text-sm p-8 text-center">Composition data will appear with request dumps.</p>';
    }

    const sessions = data.session_estimates || [];
    const tableEl = document.getElementById('session-estimates-table');
    if (tableEl && sessions.length) {
        const pColors = { telegram:'blue', whatsapp:'green', cli:'purple', discord:'yellow', cron:'purple' };
        tableEl.innerHTML = '<table class="data-table"><thead><tr><th>#</th><th>Source</th><th>Started</th><th>Msgs</th><th>Tools</th><th>API Calls</th><th>Est Input</th><th>Real Input</th></tr></thead><tbody>'
            + sessions.map((s, i) => `<tr>
                <td>${i+1}</td>
                <td><span class="badge badge-${pColors[s.source]||'gray'}">${s.source}</span></td>
                <td class="text-xs">${shortTime(s.started_at)}</td>
                <td>${s.messages}</td>
                <td>${s.tool_calls}</td>
                <td>${s.api_calls_est}</td>
                <td class="font-semibold">${fmt(s.est_input_tokens)}</td>
                <td>${s.real_input_tokens > 0 ? fmt(s.real_input_tokens) : '<span class=\"text-slate-500\">n/a</span>'}</td>
            </tr>`).join('')
            + '</tbody></table>';
    }

    const findingsEl = document.getElementById('forensics-findings');
    if (findingsEl) {
        const findings = [];
        if (fo.total_fixed > 0) {
            findings.push(`<div class="p-3 rounded-lg bg-red-950/30 border border-red-800/30"><span class="font-semibold text-red-400">Fixed overhead:</span> <span class="text-slate-200">Every API request costs <b>${fmt(fo.total_fixed)} tokens</b> before any conversation content — ${fmt(fo.system_prompt_tokens)} for system prompt + ${fmt(fo.tool_definitions_tokens)} for ${fo.tool_count} tool definitions.</span></div>`);
        }
        if (totals.total_estimated_input > 0) {
            const perMsg = totals.avg_input_per_call || 0;
            findings.push(`<div class="p-3 rounded-lg bg-amber-950/30 border border-amber-800/30"><span class="font-semibold text-amber-400">Avg request size:</span> <span class="text-slate-200">~${fmt(perMsg)} input tokens per API call (${fmt(fo.total_fixed)} fixed + ~${fmt(Math.max(perMsg - fo.total_fixed, 0))} conversation context).</span></div>`);
        }
        if (fo.tool_count > 20) {
            findings.push(`<div class="p-3 rounded-lg bg-amber-950/30 border border-amber-800/30"><span class="font-semibold text-amber-400">Tool bloat:</span> <span class="text-slate-200">${fo.tool_count} tools loaded = ${fmt(fo.tool_definitions_tokens)} tokens per request. Consider if all tools are needed for every conversation.</span></div>`);
        }
        if (totals.total_estimated_input > 500000) {
            findings.push(`<div class="p-3 rounded-lg bg-purple-950/30 border border-purple-800/30"><span class="font-semibold text-purple-400">High consumption:</span> <span class="text-slate-200">~${fmt(totals.total_estimated_input)} total estimated input tokens across ${totals.total_api_calls} API calls. At ~$3/M tokens (Sonnet), that's ~$${(totals.total_estimated_input * 3 / 1000000).toFixed(2)}.</span></div>`);
        }
        if (fo.system_prompt_tokens > 3000) {
            findings.push(`<div class="p-3 rounded-lg bg-purple-950/30 border border-purple-800/30"><span class="font-semibold text-purple-400">Large system prompt:</span> <span class="text-slate-200">${fmt(fo.system_prompt_tokens)} tokens in system prompt (personality + skills catalog). Skills autoload adds ~2.2K tokens per request.</span></div>`);
        }
        findingsEl.innerHTML = findings.join('') || '<p class="text-slate-400">No forensics data available yet. Request dumps are created when API errors occur.</p>';
    }
}

// ── Init ──

window.addEventListener('hashchange', () => navigateTo(location.hash.slice(1) || 'overview'));

document.addEventListener('DOMContentLoaded', () => {
    // Sidebar click handlers
    document.querySelectorAll('.sidebar-link[data-page]').forEach(link => {
        link.addEventListener('click', e => {
            e.preventDefault();
            location.hash = link.dataset.page;
        });
    });

    // Refresh button
    document.getElementById('refresh-btn').addEventListener('click', () => {
        loadKPIs();
        loadPage(currentPage);
    });

    // Days filter
    document.getElementById('days-select').addEventListener('change', () => {
        loadKPIs();
        loadPage(currentPage);
    });

    // Initial load
    loadKPIs();
    loadSystemHealth();
    navigateTo(location.hash.slice(1) || 'overview');
});

// Auto-refresh active page every 60s
setInterval(() => { loadKPIs(); loadPage(currentPage); }, 60000);
