const body = document.body;
const themeBtn = document.getElementById('themeBtn');
const uploadBtn = document.getElementById('uploadBtn');
const heroUpload = document.getElementById('heroUpload');
const demoBtn = document.getElementById('demoBtn');
const fileInput = document.getElementById('fileInput');
const dropZone = document.getElementById('dropZone');
const toast = document.getElementById('toast');
const reportModal = document.getElementById('reportModal');
const readReport = document.getElementById('readReport');
const closeModal = document.getElementById('closeModal');
const seeWorkflow = document.getElementById('seeWorkflow');
const verifyBtn = document.getElementById('verifyBtn');
let latestAnalysis = null;
let selectedFile = null;

function setTheme(dark) {
  body.classList.toggle('dark', dark);
  localStorage.setItem('scriptsense-theme', dark ? 'dark' : 'light');
  themeBtn.innerHTML = dark ? '<i class="icon icon-sun"></i>' : '<i class="icon icon-moon"></i>';
}
setTheme(localStorage.getItem('scriptsense-theme') === 'dark');
themeBtn.addEventListener('click', () => setTheme(!body.classList.contains('dark')));

function showToast(message, icon = 'check-circle-2') {
  toast.innerHTML = `<i class="icon icon-${icon}"></i><span>${escapeHtml(message)}</span>`;
  toast.classList.add('show');
  clearTimeout(window.toastTimer);
  window.toastTimer = setTimeout(() => toast.classList.remove('show'), 3200);
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}

function openFilePicker() { fileInput.click(); }
function showReceived(name, imageUrl = null) {
  const chip = document.getElementById('scanChip');
  const cleanName = name.length > 31 ? name.slice(0,31) + '…' : name;
  chip.innerHTML = '<i class="icon icon-file-check-2"></i> ' + escapeHtml(cleanName);
  chip.style.background = '#059669';
  const paper = document.querySelector('.rx-paper');
  if (imageUrl && paper) {
    paper.style.backgroundImage = `url(${imageUrl})`;
    paper.style.backgroundSize = 'cover';
    paper.style.backgroundPosition = 'center';
    paper.querySelectorAll('.scribble,.rx-line,.rx-sign,.rx-top,.paper-meta').forEach(el => el.style.opacity = '0');
  }
}

uploadBtn.addEventListener('click', openFilePicker);
heroUpload.addEventListener('click', () => {
  document.getElementById('prescription').scrollIntoView({behavior:'smooth', block:'center'});
  setTimeout(openFilePicker, 450);
});

fileInput.addEventListener('change', async () => {
  if (!fileInput.files[0]) return;
  selectedFile = fileInput.files[0];
  const url = URL.createObjectURL(selectedFile);
  showReceived(selectedFile.name, url);
  showToast('Prescription captured — starting recognition…', 'scan-line');
  await analyzeFile(selectedFile);
});

async function analyzeFile(file) {
  const form = new FormData();
  form.append('prescription', file);
  try {
    setBusy(true, 'Analyzing handwriting…');
    const response = await fetch('/api/analyze', {method:'POST', body:form});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Analysis failed');
    applyAnalysis(data);
    showToast(`Analysis complete — ${data.stats.medications_detected} medication candidate(s) found`);
    requestAIReview(data);
  } catch (err) {
    showToast(err.message || 'Analysis failed', 'triangle-alert');
  } finally {
    setBusy(false);
  }
}

async function loadDemo() {
  try {
    setBusy(true, 'Loading demonstration case…');
    const response = await fetch('/api/analyze-demo', {method:'POST'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not load demo');
    applyAnalysis(data);
    showReceived('Demo prescription loaded');
    showToast('Demo prescription loaded — safety checks triggered');
    requestAIReview(data);
  } catch (err) {
    showToast(err.message || 'Demo failed', 'triangle-alert');
  } finally {
    setBusy(false);
  }
}

demoBtn.addEventListener('click', loadDemo);

function setBusy(busy, label='Analyzing…') {
  uploadBtn.disabled = busy;
  demoBtn.disabled = busy;
  uploadBtn.innerHTML = busy ? `<i class="icon icon-loader-circle"></i> ${label}` : '<i class="icon icon-upload"></i> Upload prescription';
  if (busy) uploadBtn.style.opacity = '.8'; else uploadBtn.style.opacity = '';
}

['dragenter','dragover'].forEach(eventName => dropZone.addEventListener(eventName, event => {
  event.preventDefault();
  dropZone.style.outline = '2px solid #60a5fa';
  dropZone.style.outlineOffset = '-2px';
}));
['dragleave','drop'].forEach(eventName => dropZone.addEventListener(eventName, event => {
  event.preventDefault();
  dropZone.style.outline = '';
}));
dropZone.addEventListener('drop', event => {
  const file = event.dataTransfer.files[0];
  if (!file) return;
  selectedFile = file;
  const url = URL.createObjectURL(file);
  showReceived(file.name, url);
  analyzeFile(file);
});
dropZone.addEventListener('keydown', event => {
  if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openFilePicker(); }
});

function applyAnalysis(data) {
  latestAnalysis = data;
  updateMetrics(data);
  updateMedications(data);
  updateTranscript(data);
  updateSafety(data);
  updateSchedule(data);
  updateAIStatus('Generating explanation…', 'Draft analysis is being assembled from structured evidence.');
  document.querySelector('.crumb-case').textContent = data.case_id;
  const caseStrong = document.querySelector('.case-card strong');
  if (caseStrong) caseStrong.textContent = data.case_id;
}

function updateMetrics(data) {
  const metrics = document.querySelectorAll('.metric');
  if (metrics[1]) {
    metrics[1].querySelector('strong').innerHTML = `${String(data.stats.medications_detected).padStart(2,'0')} <small>items</small>`;
    metrics[1].querySelector('small').textContent = `${Math.max(0, data.stats.medications_detected-data.stats.needs_review)} high-confidence · ${data.stats.needs_review} review`;
  }
  if (metrics[2]) {
    metrics[2].querySelector('strong').innerHTML = `${String(data.stats.findings).padStart(2,'0')} <small>review</small>`;
    metrics[2].querySelector('small').textContent = `${data.stats.high_findings} high · ${Math.max(0, data.stats.findings-data.stats.high_findings)} therapy check`;
  }
  if (metrics[3]) {
    metrics[3].querySelector('strong').textContent = `${data.overall_confidence}%`;
  }
  document.querySelector('.hero-panel .ring strong').textContent = `${data.overall_confidence}%`;
  document.querySelector('.confidence-banner strong').textContent = `${data.overall_confidence}%`;
  document.querySelector('.confidence-banner .bar span').style.width = `${data.overall_confidence}%`;
  document.querySelector('.confidence-banner small').textContent = data.stats.needs_review ? `${data.stats.needs_review} item(s) need a closer look` : 'All detected items are above the preferred threshold';
}

function updateMedications(data) {
  const list = document.querySelector('.med-list');
  if (!data.medications.length) {
    list.innerHTML = '<div class="empty-med"><i class="icon icon-search-x"></i><strong>No medication name read confidently</strong><span>The handwriting model could not produce a reliable candidate. Inspect the original image or try a clearer photo.</span></div>';
  } else {
  list.innerHTML = data.medications.map(med => {
    const confClass = med.confidence >= 90 ? 'high' : 'med';
    const iconClass = med.needs_review ? 'cyan' : 'green';
    const stateIcon = med.needs_review ? 'alert-circle' : 'check';
    return `<button class="med-row ${med.needs_review ? 'needs-review' : ''}" data-med="${escapeHtml(med.name)}">
      <div class="drug-icon ${iconClass}"><i class="icon icon-capsule"></i></div>
      <div class="med-info"><strong>${escapeHtml(med.name)}</strong><span>${escapeHtml(med.strength)} · ${escapeHtml(med.form)} · ${escapeHtml(med.frequency)}</span></div>
      <span class="conf ${confClass}">${med.confidence}%</span><span class="verify-state"><i class="icon icon-${stateIcon}"></i></span><i class="icon icon-chevron-right row-chevron"></i>
    </button>`;
  }).join('');
  }
  list.querySelectorAll('.med-row').forEach(row => row.addEventListener('click', () => {
    const name = row.dataset.med;
    const med = data.medications.find(x => x.name === name);
    if (med?.needs_review) showToast(`${name}: manual verification required`, 'alert-circle');
    else showToast(`${name}: recognition confidence ${med.confidence}%`);
  }));
  document.querySelector('.review-note span').innerHTML = data.stats.needs_review
    ? `<strong>Manual verification required.</strong> ${data.stats.needs_review} candidate(s) remain below the preferred confidence threshold.`
    : '<strong>Review gate clear.</strong> Every detected candidate is above the preferred confidence threshold, but the original prescription still requires human confirmation.';
}

function updateTranscript(data) {
  const el = document.getElementById('ocrTranscript');
  const status = document.getElementById('transcriptStatus');
  if (!el || !status) return;
  el.textContent = data.ocr_text || 'No transcription returned. Please verify the original prescription manually.';
  status.textContent = data.ocr_text ? `${data.stats.medications_detected} candidate(s) extracted` : 'No confident read';
}

function updateSafety(data) {
  const card = document.getElementById('safety');
  card.querySelector('#allFindings').innerHTML = `${data.stats.findings} findings <i class="icon icon-arrow-up-right"></i>`;
  const score = card.querySelector('.risk-score strong');
  if (score) score.textContent = data.stats.high_findings;
  const alerts = card.querySelectorAll('.alert');
  alerts.forEach(a => a.remove());
  const anchor = card.querySelector('.risk-summary');
  data.findings.slice(0, 3).forEach(f => {
    const critical = f.level === 'HIGH';
    const el = document.createElement('div');
    el.className = `alert ${critical ? 'critical' : 'warning'}`;
    el.innerHTML = `<div class="alert-icon"><i class="icon icon-${critical ? 'zap' : 'copy-check'}"></i></div>
      <div><div class="alert-title"><strong>${escapeHtml(f.title)}</strong><span>${escapeHtml(f.level)}</span></div>
      <p>${escapeHtml(f.summary)} ${escapeHtml(f.action)}</p><small><i class="icon icon-circle-alert"></i> Pharmacist review recommended</small></div>`;
    anchor.insertAdjacentElement('afterend', el);
  });
  if (!data.findings.length) {
    const el = document.createElement('div');
    el.className = 'alert warning';
    el.innerHTML = `<div class="alert-icon"><i class="icon icon-search-check"></i></div><div><div class="alert-title"><strong>No demo rules triggered</strong><span>NONE</span></div><p>No interaction in the prototype rule set matched the current recognized medication list.</p><small><i class="icon icon-info"></i> This is not proof of clinical safety.</small></div>`;
    anchor.insertAdjacentElement('afterend', el);
  }
}

function updateSchedule(data) {
  const summary = document.querySelector('.schedule-summary');
  summary.children[0].querySelector('strong').textContent = data.stats.scheduled_entries;
  summary.children[1].querySelector('strong').textContent = data.medications.filter(m => m.frequency !== 'as needed').length;
  summary.children[2].querySelector('strong').textContent = data.stats.prn;
  const timeline = document.querySelector('.timeline');
  timeline.innerHTML = data.schedule.length ? data.schedule.map(item => `<div class="time"><div class="time-dot"></div><span>${escapeHtml(item.time)}</span><div><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.detail)}</small></div><em>${escapeHtml(item.label)}</em></div>`).join('') : '<div class="time"><div class="time-dot"></div><span>—</span><div><strong>No scheduled dose</strong><small>Only as-needed directions were detected.</small></div><em>PRN</em></div>';
}

async function requestAIReview(data) {
  try {
    const evidence = {...data, ocr_text: undefined};
    const response = await fetch('/api/ai-review', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(evidence)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'AI review failed');
    updateAIStatus(result.engine, result.review);
    window.lastAIReview = result;
  } catch (err) {
    updateAIStatus('ScriptSense rules · fallback', 'AI service was unavailable, so the prototype assembled this explanation from structured rule output.');
  }
}

function updateAIStatus(engine, text) {
  const output = document.querySelector('.ai-output');
  output.querySelector('.ai-output-head span:first-child').innerHTML = `<span class="dot"></span> ${escapeHtml(engine)}`;
  output.querySelector('p').textContent = `“${text}”`;
}

seeWorkflow.addEventListener('click', () => document.getElementById('how').scrollIntoView({behavior:'smooth', block:'center'}));

verifyBtn.addEventListener('click', async () => {
  const target = document.querySelector('.needs-review');
  if (!target || !latestAnalysis) {
    showToast('No medication is currently awaiting verification', 'info');
    return;
  }
  const name = target.dataset.med;
  try {
    const r = await fetch('/api/verify', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({case_id:latestAnalysis.case_id, name})});
    const result = await r.json();
    if (!r.ok) throw new Error(result.error || 'Verification failed');
    latestAnalysis.medications = latestAnalysis.medications.map(m => m.name === name ? {...m, needs_review:false, verified:true} : m);
    latestAnalysis.stats.needs_review = latestAnalysis.medications.filter(m => m.needs_review).length;
    applyAnalysis(latestAnalysis);
    showToast(`${name} marked as manually verified`);
  } catch (e) {
    showToast(e.message, 'triangle-alert');
  }
});

document.getElementById('allFindings').addEventListener('click', () => {
  document.getElementById('safety').scrollIntoView({behavior:'smooth', block:'center'});
  showToast(`Showing ${latestAnalysis?.stats.findings ?? 2} safety finding(s)`);
});
document.getElementById('helpBtn').addEventListener('click', () => showToast('Upload a prescription image or load the demo to run the prototype pipeline'));

function toggleModal(open) {
  reportModal.classList.toggle('open', open);
  reportModal.setAttribute('aria-hidden', String(!open));
  if (open) closeModal.focus();
}
readReport.addEventListener('click', () => toggleModal(true));
closeModal.addEventListener('click', () => toggleModal(false));
reportModal.addEventListener('click', event => { if (event.target === reportModal) toggleModal(false); });
document.addEventListener('keydown', event => { if (event.key === 'Escape') toggleModal(false); });

// Run one demo case on first visit so the app looks alive immediately.
window.addEventListener('DOMContentLoaded', () => loadDemo());
