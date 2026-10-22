/* ZeroTrace SDK — counts approved categories in memory and sends batched counters only. */
(() => {
  'use strict';
  if (window.__zeroTraceLoaded) return;
  window.__zeroTraceLoaded = true;

  const script = document.currentScript || document.querySelector('script[data-site]');
  const siteId = (script && script.dataset.site) || 'demo';
  const endpoint = (script && script.dataset.endpoint) || `${window.location.origin}/api/aggregate`;
  const allowedPages = ['home', 'events', 'projects', 'register'];
  const allowedSections = ['home', 'events', 'projects', 'register', 'about'];
  const initialGroup = (keys) => Object.fromEntries(keys.map(key => [key, 0]));
  const pending = {
    page_views: initialGroup(allowedPages),
    section_views: initialGroup(allowedSections),
    device_page_views: initialGroup(['mobile', 'desktop', 'tablet']),
    form_funnel: initialGroup(['registration_started', 'registration_details_reached', 'registration_submitted'])
  };
  const seenSections = new Set();
  const formStarted = new WeakSet();
  let flushing = false;
  let flushTimer = null;

  function deviceCategory() {
    const ua = navigator.userAgent || '';
    if (/iPad|Tablet|PlayBook|Silk/i.test(ua) || (window.innerWidth >= 640 && window.innerWidth < 1024 && !/Mobi/i.test(ua))) return 'tablet';
    if (/Mobi|Android|iPhone|iPod/i.test(ua) || window.innerWidth < 640) return 'mobile';
    return 'desktop';
  }
  function count(group, key, amount = 1) {
    if (!Object.prototype.hasOwnProperty.call(pending[group] || {}, key)) return;
    pending[group][key] += amount;
  }
  function recordPage(page) {
    if (!allowedPages.includes(page)) return;
    count('page_views', page);
    count('device_page_views', deviceCategory());
  }
  function snapshot() {
    return JSON.parse(JSON.stringify({ site_id: siteId, metrics: pending }));
  }
  function subtractSnapshot(sent) {
    Object.keys(sent.metrics).forEach(group => {
      Object.keys(sent.metrics[group]).forEach(key => {
        pending[group][key] = Math.max(0, pending[group][key] - sent.metrics[group][key]);
      });
    });
  }
  function hasCounters(payload) {
    return Object.values(payload.metrics).some(group => Object.values(group).some(value => value > 0));
  }
  async function flushNow() {
    if (flushing) return { ok: false, reason: 'flush-in-progress' };
    const sent = snapshot();
    if (!hasCounters(sent)) return { ok: true, empty: true };
    flushing = true;
    try {
      const response = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(sent),
        cache: 'no-store',
        keepalive: document.visibilityState === 'hidden'
      });
      if (!response.ok) throw new Error(`ZeroTrace endpoint returned ${response.status}`);
      subtractSnapshot(sent);
      window.dispatchEvent(new CustomEvent('zerotrace:flushed'));
      return { ok: true };
    } catch (error) {
      window.dispatchEvent(new CustomEvent('zerotrace:flush-error'));
      console.warn('[ZeroTrace] Aggregate batch was not sent; counters remain in memory.', error);
      return { ok: false, reason: 'network-or-validation-error' };
    } finally {
      flushing = false;
    }
  }

  // Count only known page keys. No raw URL or query string is ever transmitted.
  // For traditional pages set <body data-zt-page="events">. For the demo SPA, hash navigation is counted by the link handler below.
  const bodyPage = document.body && document.body.getAttribute('data-zt-page');
  const startingPage = bodyPage || (location.hash ? location.hash.slice(1) : 'home');
  recordPage(allowedPages.includes(startingPage) ? startingPage : 'home');

  document.addEventListener('click', event => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    const pageLink = target.closest('a[data-zt-page],button[data-zt-page]');
    if (pageLink) {
      const href = pageLink.getAttribute('href') || '';
      const isSamePageAnchor = href.startsWith('#');
      const isSpaAction = pageLink.getAttribute('data-zt-spa') === 'true';
      if ((isSamePageAnchor || isSpaAction) && allowedPages.includes(pageLink.getAttribute('data-zt-page'))) {
        recordPage(pageLink.getAttribute('data-zt-page'));
      }
    }
    const stageTrigger = target.closest('[data-zt-form-stage="details"]');
    if (stageTrigger) count('form_funnel', 'registration_details_reached');
  }, true);

  // A section is counted once per page lifetime when at least half of it is visible.
  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (!entry.isIntersecting || entry.intersectionRatio < 0.5) return;
        const section = entry.target.getAttribute('data-zt-section');
        if (!allowedSections.includes(section) || seenSections.has(section)) return;
        seenSections.add(section);
        count('section_views', section);
      });
    }, { threshold: [0.5] });
    document.querySelectorAll('[data-zt-section]').forEach(section => observer.observe(section));
  }

  // Track form milestones only. The SDK never reads input names, values, or contents.
  document.addEventListener('focusin', event => {
    const target = event.target instanceof Element ? event.target : null;
    const form = target && target.closest('form[data-zt-form="registration"]');
    if (form && !formStarted.has(form)) {
      formStarted.add(form);
      count('form_funnel', 'registration_started');
    }
  }, true);
  document.addEventListener('submit', event => {
    const form = event.target instanceof Element ? event.target : null;
    if (form && form.matches('form[data-zt-form="registration"]')) {
      count('form_funnel', 'registration_submitted');
    }
  }, true);

  // Batches are sent periodically; there is no event-by-event network stream. The registered site origin is checked by the API.
  flushTimer = window.setInterval(() => { void flushNow(); }, 8000);
  window.addEventListener('pagehide', () => {
    if (flushTimer) window.clearInterval(flushTimer);
    // Best-effort final batch. This sendBeacon path sends only counters and stores none locally.
    if (navigator.sendBeacon && !flushing) {
      const payload = snapshot();
      if (hasCounters(payload)) {
        const queued = navigator.sendBeacon(endpoint, new Blob([JSON.stringify(payload)], { type: 'application/json' }));
        if (queued) subtractSnapshot(payload);
      }
    }
  });

  window.ZeroTrace = Object.freeze({
    flushNow,
    version: '0.1.0',
    getPendingMetricCount() {
      return Object.values(pending).reduce((sum, group) => sum + Object.values(group).reduce((a, n) => a + n, 0), 0);
    }
  });
})();
