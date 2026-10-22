(() => {
  'use strict';
  const $ = (selector) => document.querySelector(selector);
  const state = { days: 30, timer: null, sites: [], siteId: null, user: null };
  const labels = {
    pages: { home: 'Home', events: 'Events', projects: 'Projects', register: 'Registration' },
    devices: { mobile: 'Mobile', desktop: 'Desktop', tablet: 'Tablet' },
    forms: { registration_started: 'Registration started', registration_details_reached: 'Details step reached', registration_submitted: 'Registration submitted' }
  };
  let toastTimer;
  const installDialog = $('#installDialog');
  const addSiteDialog = $('#addSiteDialog');

  function toast(message) {
    const node = $('#toast'); node.textContent = message; node.classList.add('show');
    clearTimeout(toastTimer); toastTimer = setTimeout(() => node.classList.remove('show'), 2800);
  }
  async function api(path, options = {}) {
    const response = await fetch(path, { credentials: 'same-origin', cache: 'no-store', ...options });
    const data = await response.json().catch(() => ({}));
    if (response.status === 401) { window.location.replace('/login'); throw new Error('Please sign in again.'); }
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    return data;
  }
  function value(metric) { return metric ? Number(metric.value ?? metric ?? 0) : 0; }
  function formatted(metric) { return value(metric).toLocaleString('en-IN'); }
  function makeEmpty(container, message) {
    container.replaceChildren(); const p = document.createElement('p'); p.className = 'empty'; p.textContent = message; container.append(p);
  }
  function addBar(container, label, n, max, color) {
    const row = document.createElement('div'); row.className = 'bar-row';
    const lab = document.createElement('div'); lab.className = 'bar-label';
    const name = document.createElement('span'); name.textContent = label;
    const val = document.createElement('b'); val.textContent = n.toLocaleString('en-IN'); lab.append(name, val);
    const track = document.createElement('div'); track.className = 'bar-track';
    const fill = document.createElement('i'); fill.style.width = max > 0 ? `${Math.max(0, Math.min(100, n / max * 100))}%` : '0%';
    if (color) fill.style.background = color;
    track.append(fill); row.append(lab, track); container.append(row);
  }
  function renderChart(series) {
    const container = $('#trendChart'); container.replaceChildren();
    if (!series.length || series.every(item => item.page_views === 0)) {
      makeEmpty(container, 'No page views recorded yet. Open the demo website or install the SDK on your registered site.');
      return;
    }
    const W = 620, H = 80, pad = 4, max = Math.max(1, ...series.map(x => x.page_views));
    const points = series.map((x, i) => ({ x: pad + i * (W - pad * 2) / Math.max(1, series.length - 1), y: H - pad - (x.page_views / max) * (H - pad * 2), ...x }));
    const path = points.map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
    const area = `${path} L${points[points.length - 1].x},${H} L${points[0].x},${H} Z`;
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.setAttribute('preserveAspectRatio', 'none'); svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', 'Daily aggregate page views; all counts are displayed');
    svg.innerHTML = `<defs><linearGradient id="trendFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#7ee8d8" stop-opacity=".4"/><stop offset="1" stop-color="#7ee8d8" stop-opacity="0"/></linearGradient></defs><path d="${area}" fill="url(#trendFill)"/><path d="${path}" fill="none" stroke="#7ee8d8" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>`;
    container.append(svg);
  }
  function renderDevices(data) {
    const container = $('#deviceSummary'); container.replaceChildren();
    const entries = Object.entries(data.device_page_views).map(([key, metric]) => ({ key, label: labels.devices[key] || key, n: value(metric) }));
    const total = entries.reduce((a, x) => a + x.n, 0);
    if (total > 0) {
      const stack = document.createElement('div'); stack.className = 'device-stack';
      const colors = { mobile: 'var(--teal)', desktop: 'var(--navy)', tablet: 'var(--amber)' };
      entries.forEach(x => { const i = document.createElement('i'); i.style.width = `${x.n / total * 100}%`; i.style.background = colors[x.key]; stack.append(i); });
      container.append(stack);
    }
    entries.forEach(x => {
      const row = document.createElement('div'); row.className = 'device-row';
      const dot = document.createElement('i'); dot.className = 'device-dot'; dot.style.background = ({ mobile: 'var(--teal)', desktop: 'var(--navy)', tablet: 'var(--amber)' })[x.key];
      const name = document.createElement('span'); name.textContent = x.label;
      const pct = document.createElement('small'); pct.textContent = `${total ? Math.round(x.n / total * 100) : 0}%`;
      const n = document.createElement('b'); n.textContent = x.n.toLocaleString('en-IN');
      row.append(dot, name, pct, n); container.append(row);
    });
    if (!total) { const p = document.createElement('p'); p.className = 'empty'; p.textContent = 'Device distribution will appear after the first SDK batch.'; container.append(p); }
  }
  function renderFunnel(data) {
    const container = $('#funnel'); container.replaceChildren();
    const entries = Object.entries(data.form_funnel).map(([key, metric]) => ({ label: labels.forms[key] || key, n: value(metric) }));
    const max = Math.max(1, ...entries.map(x => x.n));
    entries.forEach(x => {
      const row = document.createElement('div'); row.className = 'funnel-stage'; row.style.setProperty('--width', `${x.n / max * 100}%`);
      const name = document.createElement('span'); name.textContent = x.label;
      const n = document.createElement('b'); n.textContent = x.n.toLocaleString('en-IN');
      row.append(name, n); container.append(row);
    });
  }
  function renderPages(data) {
    const container = $('#pageSummary'); container.replaceChildren();
    const entries = Object.entries(data.page_views).map(([key, metric]) => ({ label: labels.pages[key] || key, n: value(metric) }));
    const max = Math.max(1, ...entries.map(x => x.n));
    entries.forEach(x => addBar(container, x.label, x.n, max));
  }
  async function refresh() {
    if (!state.siteId) return;
    $('#lastUpdated').textContent = 'Refreshing aggregate totals…';
    try {
      const data = await api(`/api/dashboard?site_id=${encodeURIComponent(state.siteId)}&days=${state.days}`);
      $('#periodText').textContent = `Aggregate measurements · Last ${state.days} days`;
      $('#siteEyebrow').textContent = data.origin;
      $('#overviewTitle').textContent = data.site_name;
      $('#totalViews').textContent = formatted(data.summary.page_views);
      $('#totalHint').textContent = `${formatted(data.summary.today_page_views)} page views today · all aggregate counts visible`;
      $('#formStarts').textContent = formatted(data.summary.form_starts);
      $('#formSubmits').textContent = formatted(data.summary.form_submissions);
      $('#conversion').textContent = data.summary.form_conversion_percent === null ? 'No form starts recorded yet' : `${data.summary.form_conversion_percent}% aggregate completion rate`;
      renderChart(data.series); renderDevices(data); renderPages(data); renderFunnel(data);
      $('#lastUpdated').textContent = `Updated ${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
    } catch (error) {
      if (!String(error.message).includes('sign in')) { $('#lastUpdated').textContent = 'Could not load aggregates'; toast(error.message || 'Backend connection failed.'); }
    }
  }
  async function loadSites(selectId = null) {
    const response = await api('/api/sites'); state.sites = response.sites;
    const select = $('#siteSelect'); select.replaceChildren();
    state.sites.forEach(site => {
      const option = document.createElement('option'); option.value = site.site_id; option.textContent = site.name; select.append(option);
    });
    if (!state.sites.length) {
      state.siteId = null; $('#siteOrigin').textContent = 'No website registered'; $('#siteInitial').textContent = '+';
      $('#overviewTitle').textContent = 'Register your first website'; $('#siteEyebrow').textContent = 'GET STARTED';
      $('#totalViews').textContent = '—'; $('#totalHint').textContent = 'Add a website origin to start collecting aggregate measurements.';
      ['#formStarts', '#formSubmits'].forEach(id => $(id).textContent = '—');
      makeEmpty($('#deviceSummary'), 'Register your website and install the SDK to begin.');
      makeEmpty($('#pageSummary'), 'No registered website yet.'); makeEmpty($('#funnel'), 'No registered website yet.');
      makeEmpty($('#trendChart'), 'Your daily aggregate trend will appear here.');
      addSiteDialog.showModal(); return;
    }
    state.siteId = (state.sites.some(s => s.site_id === selectId) ? selectId : null) || (state.sites.some(s => s.site_id === state.siteId) ? state.siteId : null) || state.sites[0].site_id;
    select.value = state.siteId; updateSelectedSiteInfo(); await refresh();
  }
  function selectedSite() { return state.sites.find(site => site.site_id === state.siteId) || null; }
  function updateSelectedSiteInfo() {
    const site = selectedSite(); if (!site) return;
    $('#siteInitial').textContent = site.name.trim().slice(0, 1).toUpperCase();
    $('#siteOrigin').textContent = site.origin;
    $('#deleteSite').hidden = site.site_id === 'demo';
  }
  function sdkSnippet() {
    const site = selectedSite(); if (!site) return '';
    const base = window.location.origin;
    return `<script src="${base}/sdk/zerotrace.js" data-site="${site.site_id}" data-endpoint="${base}/api/aggregate" defer><\/script>`;
  }
  async function loadAccount() {
    const result = await api('/api/auth/me');
    state.user = result.user; $('#userEmail').textContent = state.user.email;
    await loadSites();
  }

  document.querySelectorAll('[data-days]').forEach(button => button.addEventListener('click', () => {
    state.days = Number(button.dataset.days);
    document.querySelectorAll('[data-days]').forEach(x => x.classList.toggle('selected', x === button)); refresh();
  }));
  document.querySelectorAll('[data-scroll]').forEach(link => link.addEventListener('click', e => { e.preventDefault(); document.getElementById(link.dataset.scroll)?.scrollIntoView({ behavior: 'smooth' }); }));
  $('#siteSelect').addEventListener('change', async event => {
    state.siteId = event.target.value; updateSelectedSiteInfo(); await refresh();
  });
  $('#refresh').addEventListener('click', refresh);
  $('#addSiteBtn').addEventListener('click', () => { $('#siteFormError').textContent = ''; addSiteDialog.showModal(); });
  $('#addSiteForm').addEventListener('submit', async event => {
    event.preventDefault(); const button = $('#saveSiteBtn'); button.disabled = true; $('#siteFormError').textContent = '';
    try {
      const site = await api('/api/sites', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: $('#siteNameInput').value, origin: $('#siteOriginInput').value }) });
      addSiteDialog.close(); $('#addSiteForm').reset(); await loadSites(site.site.site_id); toast('Website registered. Copy the SDK snippet to install analytics.');
      installDialog.showModal(); updateSnippet();
    } catch (error) { $('#siteFormError').textContent = error.message; }
    finally { button.disabled = false; }
  });
  $('#installSnippet').addEventListener('click', () => { updateSnippet(); installDialog.showModal(); });
  function updateSnippet() {
    $('#installCode').textContent = sdkSnippet();
    const selected = selectedSite();
    $('#snippetNote').textContent = selected ? `Registered origin: ${selected.origin}. Requests from a different Origin will be rejected. Deploy ZeroTrace behind HTTPS before public use.` : 'Register a website first.';
  }
  $('#closeInstall').addEventListener('click', () => installDialog.close());
  $('#copyInstall').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(sdkSnippet()); toast('SDK snippet copied.'); }
    catch { toast('Clipboard unavailable. Select and copy the snippet manually.'); }
  });
  $('#themeToggle').addEventListener('click', () => {
    const root = document.documentElement; root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
    localStorage.setItem('zt-theme', root.dataset.theme);
  });
  const savedTheme = localStorage.getItem('zt-theme'); if (savedTheme) document.documentElement.dataset.theme = savedTheme;
  $('#copyReport').addEventListener('click', async () => {
    const report = 'ZeroTrace privacy report\n- SDK does not create persistent visitor IDs or analytics cookies\n- SDK counters remain in page memory and are batch-submitted\n- No individual event history or raw form values are stored\n- Daily aggregate counters persist in SQLite for configured retention\n- All counts, including small counts, are displayed; this is a deliberate product choice, not a privacy guarantee\n- A process-memory HMAC of client connection IP may be used transiently for rate limiting; raw IPs are not stored by the application\n- Differential privacy and secure aggregation are not implemented\n- HTTPS and operational security controls are required for public deployment.';
    try { await navigator.clipboard.writeText(report); toast('Privacy report copied.'); }
    catch { toast('Clipboard unavailable. Copy the report manually.'); }
  });
  $('#reset').addEventListener('click', async () => {
    if (!state.siteId || !confirm(`Permanently clear stored aggregate history for ${selectedSite()?.name || 'this site'}?`)) return;
    try { await api(`/api/sites/${encodeURIComponent(state.siteId)}/aggregates`, { method: 'DELETE' }); await refresh(); toast('Selected site aggregate history cleared.'); }
    catch (error) { toast(error.message || 'Could not clear aggregates.'); }
  });
  $('#deleteSite').addEventListener('click', async () => {
    const site = selectedSite(); if (!site || site.site_id === 'demo') return;
    if (!confirm(`Remove ${site.name} and delete its aggregate history? This cannot be undone.`)) return;
    try { await api(`/api/sites/${encodeURIComponent(site.site_id)}`, { method: 'DELETE' }); await loadSites(); toast('Website removed.'); }
    catch (error) { toast(error.message || 'Could not remove website.'); }
  });
  $('#logoutBtn').addEventListener('click', async () => {
    try { await api('/api/auth/logout', { method: 'POST' }); } catch (_) { /* navigate to sign-in regardless */ }
    window.location.replace('/login');
  });
  addSiteDialog.addEventListener('click', event => { if (event.target === addSiteDialog) addSiteDialog.close(); });
  installDialog.addEventListener('click', event => { if (event.target === installDialog) installDialog.close(); });

  loadAccount().catch(error => { if (!String(error.message).includes('sign in')) toast(error.message || 'Could not load account.'); });
  state.timer = setInterval(() => { if (state.siteId) refresh(); }, 5000);
})();
