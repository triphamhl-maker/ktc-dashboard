/**
 * KTC Operational Dashboard — Frontend Controller
 * API-first architecture: all data comes from FastAPI backend.
 * Chart.js for visualization, auto-refresh for live monitoring.
 */

document.addEventListener('DOMContentLoaded', () => {
    // ─── State ────────────────────────────────────────────────
    let slaThreshold = 0.5; // Default SLA threshold %
    let chartTrend = null;
    let chartDist = null;
    let chartVolume = null;
    let autoRefreshTimer = null;
    let tablePage = 1;
    const tableLimit = 20;
    let tableSearch = '';

    // Date range filter state (null = no filter / all data)
    let dateStart = null;
    let dateEnd = null;

    // ─── DOM References ───────────────────────────────────────
    const $ = (id) => document.getElementById(id);
    const thresholdSlider = $('thresholdSlider');
    const thresholdValue = $('thresholdValue');

    // KPI Elements
    const kpiVolumeValue = $('kpiVolumeValue');
    const kpiVolumeChange = $('kpiVolumeChange');
    const kpiVolumeNote = $('kpiVolumeNote');
    const kpiBacklogValue = $('kpiBacklogValue');
    const kpiBacklogCard = $('kpiBacklog');
    const kpiSlaStatus = $('kpiSlaStatus');
    const kpiBacklogProgress = $('kpiBacklogProgress');
    const kpiLeadtimeValue = $('kpiLeadtimeValue');
    const kpiLeadtimeChange = $('kpiLeadtimeChange');
    const kpiBacklogVolValue = $('kpiBacklogVolValue');
    const kpiBacklogVolChange = $('kpiBacklogVolChange');

    // SLA
    const slaIndicator = $('slaIndicator');
    const updateBadge = $('updateBadge');
    const lastUpdateText = $('lastUpdateText');

    // Crawler
    const crawlerDot = $('crawlerDot');
    const crawlerDetail = $('crawlerDetail');
    const crawlerRecords = $('crawlerRecords');
    const crawlerInterval = $('crawlerInterval');
    const btnTriggerCrawl = $('btnTriggerCrawl');

    // Table
    const tableBody = $('tableBody');
    const tableCount = $('tableCount');
    const tableSearch_el = $('tableSearch');
    const tableInfo = $('tableInfo');
    const tablePagination = $('tablePagination');

    // Other
    const themeToggle = $('themeToggle');
    const scrollTopBtn = $('scrollTopBtn');
    const mobileMenuBtn = $('mobileMenuBtn');
    const sidebar = $('sidebar');
    const sidebarBackdrop = $('sidebarBackdrop');

    // Date toolbar
    const dateStartInput = $('dateStart');
    const dateEndInput = $('dateEnd');
    const btnApplyDate = $('btnApplyDate');
    const btnResetDate = $('btnResetDate');
    const presetBtns = document.querySelectorAll('.preset-btn');

    // Init lucide icons
    lucide.createIcons();

    // ─── Theme ────────────────────────────────────────────────
    const initTheme = () => {
        const saved = localStorage.getItem('ktc-theme') || 'dark-theme';
        document.body.className = saved;
    };

    themeToggle.addEventListener('click', () => {
        const isDark = document.body.classList.contains('dark-theme');
        document.body.className = isDark ? 'light-theme' : 'dark-theme';
        localStorage.setItem('ktc-theme', document.body.className);
        // Re-render charts with new theme
        renderAllCharts();
    });

    initTheme();

    // ─── Mobile Sidebar ───────────────────────────────────────
    mobileMenuBtn?.addEventListener('click', () => {
        sidebar.classList.toggle('open');
        sidebarBackdrop.classList.toggle('open');
    });
    sidebarBackdrop?.addEventListener('click', () => {
        sidebar.classList.remove('open');
        sidebarBackdrop.classList.remove('open');
    });

    // ─── Scroll to Top ───────────────────────────────────────
    window.addEventListener('scroll', () => {
        scrollTopBtn?.classList.toggle('visible', window.scrollY > 400);
    }, { passive: true });
    scrollTopBtn?.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));

    // ─── SLA Threshold Slider ─────────────────────────────────
    thresholdSlider.addEventListener('input', (e) => {
        slaThreshold = parseFloat(e.target.value);
        thresholdValue.textContent = slaThreshold.toFixed(2) + '%';
        // Re-apply SLA status to KPIs and re-render trend chart
        refreshDashboard();
    });

    // ─── Date Range Toolbar ───────────────────────────────────
    const formatDateISO = (d) => d.toISOString().split('T')[0];

    // Apply date filter
    btnApplyDate.addEventListener('click', () => {
        const s = dateStartInput.value;
        const e = dateEndInput.value;
        dateStart = s || null;
        dateEnd = e || null;
        // Deactivate all presets
        presetBtns.forEach(b => b.classList.remove('active'));
        tablePage = 1;
        refreshDashboard();
    });

    // Reset date filter
    btnResetDate.addEventListener('click', () => {
        dateStart = null;
        dateEnd = null;
        dateStartInput.value = '';
        dateEndInput.value = '';
        presetBtns.forEach(b => b.classList.remove('active'));
        document.querySelector('.preset-btn[data-days="0"]')?.classList.add('active');
        tablePage = 1;
        refreshDashboard();
    });

    // Preset buttons
    presetBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const days = parseInt(btn.dataset.days);
            presetBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');

            if (days === 0) {
                // All data
                dateStart = null;
                dateEnd = null;
                dateStartInput.value = '';
                dateEndInput.value = '';
            } else {
                const today = new Date();
                const start = new Date(today);
                start.setDate(today.getDate() - days + 1);
                dateStart = formatDateISO(start);
                dateEnd = formatDateISO(today);
                dateStartInput.value = dateStart;
                dateEndInput.value = dateEnd;
            }
            tablePage = 1;
            refreshDashboard();
        });
    });

    // Also apply when pressing Enter in date inputs
    dateStartInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') btnApplyDate.click(); });
    dateEndInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') btnApplyDate.click(); });

    // ─── Trigger Crawl ────────────────────────────────────────
    btnTriggerCrawl.addEventListener('click', async () => {
        btnTriggerCrawl.disabled = true;
        btnTriggerCrawl.classList.add('spinning');
        try {
            await fetch('/api/crawler/trigger', { method: 'POST' });
            crawlerDetail.textContent = 'Đang crawl dữ liệu...';
            crawlerDot.className = 'crawler-status-dot running';
            // Wait a bit then refresh
            setTimeout(() => refreshDashboard(), 5000);
        } catch (e) {
            console.error('Trigger crawl failed:', e);
        } finally {
            setTimeout(() => {
                btnTriggerCrawl.disabled = false;
                btnTriggerCrawl.classList.remove('spinning');
            }, 3000);
        }
    });

    // ─── Number Formatting ────────────────────────────────────
    const formatNum = (n, decimals = 0) => {
        return new Intl.NumberFormat('vi-VN', {
            minimumFractionDigits: decimals,
            maximumFractionDigits: decimals,
        }).format(n);
    };

    // ─── XSS Protection ──────────────────────────────────────
    const escapeHtml = (str) => {
        if (str === null || str === undefined) return '';
        const s = String(str);
        const div = document.createElement('div');
        div.appendChild(document.createTextNode(s));
        return div.innerHTML;
    };

    // ─── Animated Counter ─────────────────────────────────────
    const animateValue = (el, target, suffix = '', decimals = 0, duration = 900) => {
        const raw = el.textContent.replace(/[^\d.,-]/g, '').replace(/\./g, '').replace(',', '.');
        const start = parseFloat(raw) || 0;
        const startTime = performance.now();

        const step = (now) => {
            const elapsed = now - startTime;
            const progress = Math.min(elapsed / duration, 1);
            const eased = 1 - Math.pow(1 - progress, 4); // ease-out quart
            const current = start + (target - start) * eased;
            el.textContent = formatNum(current, decimals) + suffix;
            if (progress < 1) requestAnimationFrame(step);
        };
        requestAnimationFrame(step);
    };

    // ─── Change Indicator ─────────────────────────────────────
    const renderChange = (el, value, suffix = '', invert = false) => {
        if (!el) return;
        if (value === 0 || value === null || value === undefined) {
            el.textContent = '—';
            el.className = 'kpi-change neutral';
            return;
        }
        // For backlog: up is bad (positive), down is good (negative)
        // For volume: up can be good
        const isUp = value > 0;
        const icon = isUp ? '↑' : '↓';
        const cls = invert ? (isUp ? 'positive' : 'negative') : (isUp ? 'negative' : 'positive');
        el.className = 'kpi-change ' + cls;
        el.textContent = `${icon} ${formatNum(Math.abs(value), 2)}${suffix}`;
    };

    // ─── Theme Colors Helper ──────────────────────────────────
    const getColors = () => {
        const isDark = document.body.classList.contains('dark-theme');
        return {
            text: isDark ? '#94a3b8' : '#475569',
            grid: isDark ? 'rgba(148,163,184,0.06)' : 'rgba(15,23,42,0.06)',
            accent: '#f97316',
            danger: '#ef4444',
            safe: '#10b981',
            info: '#3b82f6',
            warn: '#f59e0b',
            bg: isDark ? '#0f172a' : '#ffffff',
        };
    };

    // ─── Cached Data ──────────────────────────────────────────
    let cachedOverview = null;
    let cachedTrend = null;
    let cachedDistribution = null;
    let cachedBacklogDaily = null;

    // ─── API Calls ────────────────────────────────────────────
    const buildDateParams = () => {
        const p = new URLSearchParams();
        if (dateStart) p.set('start_date', dateStart);
        if (dateEnd) p.set('end_date', dateEnd);
        return p;
    };

    const api = {
        overview: () => {
            const p = buildDateParams();
            return fetch(`/api/overview?${p}`).then(r => r.json());
        },
        trend: () => {
            const p = buildDateParams();
            return fetch(`/api/trend?${p}`).then(r => r.json());
        },
        backlogDaily: () => {
            const p = buildDateParams();
            return fetch(`/api/backlog-daily?${p}`).then(r => r.json());
        },
        distribution: () => fetch('/api/distribution').then(r => r.json()),
        crawlerStatus: () => fetch('/api/crawler/status').then(r => r.json()),
        snapshots: (page, limit, search) => {
            const p = new URLSearchParams({ page, limit });
            if (search) p.set('search', search);
            if (dateStart) p.set('start_date', dateStart);
            if (dateEnd) p.set('end_date', dateEnd);
            return fetch(`/api/snapshots?${p}`).then(r => r.json());
        },
    };

    // ─── Dashboard Refresh ────────────────────────────────────
    const refreshDashboard = async () => {
        try {
            const [overview, trend, dist, daily, crawlerSt] = await Promise.all([
                api.overview(),
                api.trend(),
                api.distribution(),
                api.backlogDaily(),
                api.crawlerStatus(),
            ]);

            cachedOverview = overview;
            cachedTrend = trend;
            cachedDistribution = dist;
            cachedBacklogDaily = daily;

            renderKPIs(overview);
            renderAllCharts();
            renderCrawlerStatus(crawlerSt);
            renderTable();
            updateTimestamp();
        } catch (e) {
            console.error('[Dashboard] Refresh failed:', e);
            crawlerDetail.textContent = 'Lỗi kết nối backend';
            crawlerDot.className = 'crawler-status-dot error';
        }
    };

    // ─── KPIs ─────────────────────────────────────────────────
    const renderKPIs = (data) => {
        if (!data || !data.latest_date) {
            kpiVolumeValue.textContent = '—';
            kpiBacklogValue.textContent = '—';
            kpiLeadtimeValue.textContent = '—';
            kpiBacklogVolValue.textContent = '—';
            return;
        }

        // Volume
        animateValue(kpiVolumeValue, data.total_volume, '', 0);
        renderChange(kpiVolumeChange, data.volume_change, '', false);
        kpiVolumeNote.textContent = `Ngày ${data.latest_date}`;

        // Backlog %
        const bp = data.backlog_gt24h_percent;
        animateValue(kpiBacklogValue, bp, '%', 2);

        // SLA Status
        let slaStatus = 'safe';
        let slaLabel = 'An toàn';
        if (bp >= slaThreshold) {
            slaStatus = 'danger';
            slaLabel = 'Vượt SLA';
        } else if (bp >= slaThreshold * 0.6) {
            slaStatus = 'warning';
            slaLabel = 'Cảnh báo';
        }

        kpiSlaStatus.textContent = '';
        const slaPill = document.createElement('span');
        slaPill.className = `sla-pill ${slaStatus}`;
        slaPill.textContent = slaLabel;
        kpiSlaStatus.appendChild(slaPill);

        // Backlog card state
        kpiBacklogCard.classList.remove('sla-safe', 'sla-warning', 'sla-danger');
        kpiBacklogCard.classList.add('sla-' + slaStatus);

        // Progress bar
        const pctWidth = Math.min(bp / (slaThreshold * 2) * 100, 100);
        kpiBacklogProgress.style.width = pctWidth + '%';
        kpiBacklogProgress.className = 'kpi-progress-bar' + (slaStatus === 'danger' ? ' danger' : slaStatus === 'warning' ? ' warn' : '');

        // SLA indicator in header
        slaIndicator.className = 'sla-indicator ' + slaStatus;

        // Backlog % change (up is bad)
        renderChange(document.querySelector('#kpiBacklog .kpi-change'), data.backlog_percent_change, '%', true);

        // LeadTime
        animateValue(kpiLeadtimeValue, data.avg_lead_time, 'h', 1);
        renderChange(kpiLeadtimeChange, data.lead_time_change, 'h', true);

        // Backlog Volume
        animateValue(kpiBacklogVolValue, data.backlog_gt24h_volume, '', 0);
    };

    // ─── Charts ───────────────────────────────────────────────
    const renderAllCharts = () => {
        renderTrendChart();
        renderDistChart();
        renderVolumeChart();
    };

    // Trend Chart: %Backlog >24h over time with SLA threshold line
    const renderTrendChart = () => {
        if (!cachedTrend?.points?.length) return;
        const c = getColors();
        const pts = cachedTrend.points;
        const labels = pts.map(p => {
            const d = new Date(p.time_date);
            return `${d.getDate()}/${d.getMonth() + 1}`;
        });
        const data = pts.map(p => p.backlog_percent);

        if (chartTrend) chartTrend.destroy();

        const ctx = document.getElementById('trendChart').getContext('2d');

        // Gradient fill
        const gradient = ctx.createLinearGradient(0, 0, 0, 300);
        gradient.addColorStop(0, 'rgba(249, 115, 22, 0.2)');
        gradient.addColorStop(1, 'rgba(249, 115, 22, 0)');

        chartTrend = new Chart(ctx, {
            type: 'line',
            data: {
                labels,
                datasets: [{
                    label: '% Backlog >24h',
                    data,
                    borderColor: c.accent,
                    backgroundColor: gradient,
                    fill: true,
                    tension: 0.4,
                    borderWidth: 2.5,
                    pointRadius: 3,
                    pointHoverRadius: 6,
                    pointBackgroundColor: c.accent,
                    pointBorderColor: c.bg,
                    pointBorderWidth: 2,
                    pointHoverBackgroundColor: '#fff',
                    pointHoverBorderColor: c.accent,
                    pointHoverBorderWidth: 3,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    mode: 'index',
                    intersect: false,
                },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: 'rgba(15, 23, 42, 0.9)',
                        titleColor: '#f1f5f9',
                        bodyColor: '#94a3b8',
                        borderColor: 'rgba(148, 163, 184, 0.1)',
                        borderWidth: 1,
                        cornerRadius: 8,
                        padding: 12,
                        callbacks: {
                            label: (ctx) => `Backlog >24h: ${ctx.parsed.y.toFixed(3)}%`,
                        },
                    },
                    annotation: {
                        annotations: {
                            slaLine: {
                                type: 'line',
                                yMin: slaThreshold,
                                yMax: slaThreshold,
                                borderColor: c.danger,
                                borderWidth: 2,
                                borderDash: [6, 4],
                                label: {
                                    display: true,
                                    content: `SLA ${slaThreshold}%`,
                                    position: 'end',
                                    backgroundColor: 'rgba(239, 68, 68, 0.85)',
                                    color: '#fff',
                                    font: { size: 10, weight: '600' },
                                    padding: { x: 6, y: 3 },
                                    borderRadius: 4,
                                },
                            },
                        },
                    },
                },
                scales: {
                    x: {
                        grid: { color: c.grid, drawBorder: false },
                        ticks: {
                            color: c.text,
                            font: { size: 11 },
                            maxRotation: 45,
                        },
                    },
                    y: {
                        beginAtZero: true,
                        grid: { color: c.grid, drawBorder: false },
                        ticks: {
                            color: c.text,
                            font: { size: 11 },
                            callback: (v) => v.toFixed(2) + '%',
                        },
                    },
                },
            },
        });
    };

    // Distribution Doughnut Chart
    const renderDistChart = () => {
        if (!cachedDistribution?.length) return;
        const c = getColors();

        const labels = cachedDistribution.map(d => d.aging_bucket);
        const data = cachedDistribution.map(d => d.volume);
        const colors = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444'];
        const bgColors = ['rgba(59,130,246,0.15)', 'rgba(16,185,129,0.15)', 'rgba(245,158,11,0.15)', 'rgba(239,68,68,0.15)'];

        if (chartDist) chartDist.destroy();

        const ctx = document.getElementById('distChart').getContext('2d');
        chartDist = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels,
                datasets: [{
                    data,
                    backgroundColor: colors,
                    hoverBackgroundColor: colors.map(c => c),
                    borderColor: 'transparent',
                    borderWidth: 2,
                    hoverBorderColor: colors,
                    spacing: 3,
                    borderRadius: 4,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '65%',
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: {
                            color: c.text,
                            font: { size: 11, weight: '500' },
                            padding: 16,
                            usePointStyle: true,
                            pointStyleWidth: 10,
                        },
                    },
                    tooltip: {
                        backgroundColor: 'rgba(15, 23, 42, 0.9)',
                        titleColor: '#f1f5f9',
                        bodyColor: '#94a3b8',
                        borderColor: 'rgba(148, 163, 184, 0.1)',
                        borderWidth: 1,
                        cornerRadius: 8,
                        padding: 12,
                        callbacks: {
                            label: (ctx) => {
                                const total = ctx.dataset.data.reduce((a, b) => a + b, 0);
                                const pct = ((ctx.parsed / total) * 100).toFixed(1);
                                return `${ctx.label}: ${formatNum(ctx.parsed)} (${pct}%)`;
                            },
                        },
                    },
                },
            },
        });
    };

    // Volume + LeadTime Combo Chart
    const renderVolumeChart = () => {
        if (!cachedTrend?.points?.length) return;
        const c = getColors();
        const pts = cachedTrend.points;
        const labels = pts.map(p => {
            const d = new Date(p.time_date);
            return `${d.getDate()}/${d.getMonth() + 1}`;
        });

        if (chartVolume) chartVolume.destroy();

        const ctx = document.getElementById('volumeChart').getContext('2d');

        // Gradient for volume bars
        const barGradient = ctx.createLinearGradient(0, 0, 0, 300);
        barGradient.addColorStop(0, 'rgba(59, 130, 246, 0.6)');
        barGradient.addColorStop(1, 'rgba(59, 130, 246, 0.1)');

        chartVolume = new Chart(ctx, {
            type: 'bar',
            data: {
                labels,
                datasets: [
                    {
                        label: 'Volume',
                        data: pts.map(p => p.total_volume),
                        backgroundColor: barGradient,
                        borderColor: c.info,
                        borderWidth: 1,
                        borderRadius: 4,
                        borderSkipped: false,
                        yAxisID: 'y',
                        order: 2,
                    },
                    {
                        label: 'LeadTime (h)',
                        data: pts.map(p => p.avg_lead_time),
                        type: 'line',
                        borderColor: c.warn,
                        backgroundColor: 'transparent',
                        borderWidth: 2,
                        tension: 0.4,
                        pointRadius: 3,
                        pointHoverRadius: 5,
                        pointBackgroundColor: c.warn,
                        pointBorderColor: c.bg,
                        pointBorderWidth: 2,
                        yAxisID: 'y1',
                        order: 1,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    mode: 'index',
                    intersect: false,
                },
                plugins: {
                    legend: {
                        labels: {
                            color: c.text,
                            font: { size: 11, weight: '500' },
                            usePointStyle: true,
                            padding: 20,
                        },
                    },
                    tooltip: {
                        backgroundColor: 'rgba(15, 23, 42, 0.9)',
                        titleColor: '#f1f5f9',
                        bodyColor: '#94a3b8',
                        borderColor: 'rgba(148, 163, 184, 0.1)',
                        borderWidth: 1,
                        cornerRadius: 8,
                        padding: 12,
                        callbacks: {
                            label: (ctx) => {
                                if (ctx.dataset.label === 'Volume') {
                                    return `Volume: ${formatNum(ctx.parsed.y)}`;
                                }
                                return `LeadTime: ${ctx.parsed.y.toFixed(1)}h`;
                            },
                        },
                    },
                },
                scales: {
                    x: {
                        grid: { color: c.grid, drawBorder: false },
                        ticks: {
                            color: c.text,
                            font: { size: 11 },
                            maxRotation: 45,
                        },
                    },
                    y: {
                        position: 'left',
                        beginAtZero: true,
                        grid: { color: c.grid, drawBorder: false },
                        ticks: {
                            color: c.info,
                            font: { size: 11 },
                            callback: (v) => v >= 1000 ? (v / 1000).toFixed(0) + 'K' : v,
                        },
                        title: {
                            display: true,
                            text: 'Volume',
                            color: c.info,
                            font: { size: 11, weight: '600' },
                        },
                    },
                    y1: {
                        position: 'right',
                        beginAtZero: true,
                        grid: { display: false },
                        ticks: {
                            color: c.warn,
                            font: { size: 11 },
                            callback: (v) => v.toFixed(1) + 'h',
                        },
                        title: {
                            display: true,
                            text: 'LeadTime',
                            color: c.warn,
                            font: { size: 11, weight: '600' },
                        },
                    },
                },
            },
        });
    };

    // ─── Table ────────────────────────────────────────────────
    const renderTable = async () => {
        try {
            const data = await api.snapshots(tablePage, tableLimit, tableSearch);
            const { rows, total, page, total_pages } = data;

            tableCount.textContent = `${formatNum(total)} dòng dữ liệu`;
            tableBody.innerHTML = '';

            if (!rows || rows.length === 0) {
                const emptyRow = document.createElement('tr');
                const emptyTd = document.createElement('td');
                emptyTd.colSpan = 6;
                emptyTd.style.cssText = 'text-align:center;padding:40px;color:var(--text-muted);';
                emptyTd.textContent = 'Chưa có dữ liệu. Crawler đang tải dữ liệu từ Google Sheets...';
                emptyRow.appendChild(emptyTd);
                tableBody.appendChild(emptyRow);
                tableInfo.textContent = '0 dòng';
                tablePagination.innerHTML = '';
                return;
            }

            rows.forEach(row => {
                const tr = document.createElement('tr');
                if (row.is_backlog) tr.className = 'row-backlog';

                const pctDisplay = (row.percent_volume * 100).toFixed(2);
                const ltDisplay = row.lead_time.toFixed(1);

                const createTd = (text, cls) => {
                    const td = document.createElement('td');
                    if (cls) td.className = cls;
                    td.textContent = text;
                    return td;
                };

                tr.appendChild(createTd(row.time_date));
                tr.appendChild(createTd(row.aging_bucket));
                tr.appendChild(createTd(formatNum(row.volume), 'text-right'));
                tr.appendChild(createTd(pctDisplay + '%', 'text-right'));
                tr.appendChild(createTd(ltDisplay + 'h', 'text-right'));

                const statusTd = document.createElement('td');
                statusTd.className = 'text-center';
                const badge = document.createElement('span');
                badge.className = row.is_backlog ? 'status-badge backlog' : 'status-badge safe';
                badge.textContent = row.is_backlog ? 'Backlog' : 'OK';
                statusTd.appendChild(badge);
                tr.appendChild(statusTd);

                tableBody.appendChild(tr);
            });

            // Pagination info
            const start = (page - 1) * tableLimit + 1;
            const end = Math.min(page * tableLimit, total);
            tableInfo.textContent = `Hiển thị ${start}—${end} / ${formatNum(total)}`;

            // Pagination buttons
            renderPagination(page, total_pages);
        } catch (e) {
            console.error('Table render error:', e);
            tableBody.innerHTML = '';
            const errRow = document.createElement('tr');
            const errTd = document.createElement('td');
            errTd.colSpan = 6;
            errTd.style.cssText = 'text-align:center;padding:40px;color:var(--text-muted);';
            errTd.textContent = 'Lỗi tải dữ liệu';
            errRow.appendChild(errTd);
            tableBody.appendChild(errRow);
        }
    };

    const renderPagination = (current, totalPages) => {
        tablePagination.innerHTML = '';
        if (totalPages <= 1) return;

        // Prev
        const prevBtn = document.createElement('button');
        prevBtn.className = 'page-btn';
        prevBtn.innerHTML = '‹';
        prevBtn.disabled = current <= 1;
        prevBtn.addEventListener('click', () => { tablePage = current - 1; renderTable(); });
        tablePagination.appendChild(prevBtn);

        // Page numbers
        const maxVisible = 5;
        let startPage = Math.max(1, current - Math.floor(maxVisible / 2));
        let endPage = Math.min(totalPages, startPage + maxVisible - 1);
        if (endPage - startPage < maxVisible - 1) startPage = Math.max(1, endPage - maxVisible + 1);

        for (let i = startPage; i <= endPage; i++) {
            const btn = document.createElement('button');
            btn.className = 'page-btn' + (i === current ? ' active' : '');
            btn.textContent = i;
            btn.addEventListener('click', () => { tablePage = i; renderTable(); });
            tablePagination.appendChild(btn);
        }

        // Next
        const nextBtn = document.createElement('button');
        nextBtn.className = 'page-btn';
        nextBtn.innerHTML = '›';
        nextBtn.disabled = current >= totalPages;
        nextBtn.addEventListener('click', () => { tablePage = current + 1; renderTable(); });
        tablePagination.appendChild(nextBtn);
    };

    // Table search
    let searchDebounce = null;
    tableSearch_el?.addEventListener('input', (e) => {
        clearTimeout(searchDebounce);
        searchDebounce = setTimeout(() => {
            tableSearch = e.target.value.trim();
            tablePage = 1;
            renderTable();
        }, 300);
    });

    // ─── Crawler Status ───────────────────────────────────────
    const renderCrawlerStatus = (status) => {
        if (!status) return;

        if (status.is_running) {
            crawlerDot.className = 'crawler-status-dot running';
            crawlerDetail.textContent = 'Đang crawl dữ liệu...';
        } else if (status.last_error) {
            crawlerDot.className = 'crawler-status-dot error';
            crawlerDetail.textContent = `Lỗi: ${String(status.last_error).substring(0, 60).replace(/[<>"'&]/g, '')}`;
        } else if (status.last_run_at) {
            crawlerDot.className = 'crawler-status-dot ready';
            crawlerDetail.textContent = `Cập nhật: ${timeAgo(status.last_run_at)}`;
        } else {
            crawlerDot.className = 'crawler-status-dot';
            crawlerDetail.textContent = 'Chờ crawl lần đầu...';
        }

        crawlerRecords.textContent = status.last_records_count > 0
            ? `${status.last_records_count} records`
            : '—';
        crawlerInterval.textContent = `Mỗi ${status.crawl_interval_minutes} phút`;
    };

    const timeAgo = (iso) => {
        try {
            const diff = (Date.now() - new Date(iso).getTime()) / 1000;
            if (diff < 60) return 'vừa xong';
            if (diff < 3600) return Math.floor(diff / 60) + ' phút trước';
            if (diff < 86400) return Math.floor(diff / 3600) + ' giờ trước';
            return Math.floor(diff / 86400) + ' ngày trước';
        } catch { return iso; }
    };

    // ─── Update Timestamp ─────────────────────────────────────
    const updateTimestamp = () => {
        const now = new Date();
        const time = now.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });
        const date = now.toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit' });
        lastUpdateText.textContent = `${date} lúc ${time}`;
        updateBadge.classList.add('fresh');
        setTimeout(() => updateBadge.classList.remove('fresh'), 5000);
    };

    // ─── Auto Refresh ─────────────────────────────────────────
    const startAutoRefresh = () => {
        if (autoRefreshTimer) clearInterval(autoRefreshTimer);
        autoRefreshTimer = setInterval(() => {
            refreshDashboard();
        }, 60000); // Every 60s
    };

    // ─── Initial Load ─────────────────────────────────────────
    refreshDashboard();
    startAutoRefresh();
});
