'use strict';
const $ = id => document.getElementById(id);
const state = {
  id: null, result: null, roi: null, roiTime: 0, calibrationLine: null,
  start: null, drawing: false, mode: 'target', fallbackPreview: false,
  objectUrl: null, serverPreview: null, toastTimer: null, pendingFile: null
};
const video = $('video'), fallbackPreview = $('fallbackPreview'), canvas = $('overlay'), ctx = canvas.getContext('2d');

function showToast(message, type = 'info') {
  const toast = $('toast');
  toast.textContent = String(message || 'Something went wrong.');
  toast.className = `toast show${type === 'error' ? ' error' : type === 'success' ? ' success' : ''}`;
  if (state.toastTimer) clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => { toast.className = 'toast'; }, 4200);
}
function setProgress(message, percent = null) {
  $('progress').textContent = message;
  if (percent !== null) $('bar').style.width = `${Math.max(0, Math.min(100, percent))}%`;
}
function setPipeline(stage) {
  const ids = ['stepUpload', 'stepTrack', 'stepPhysics', 'stepReport'];
  ids.forEach((id, i) => {
    const el = $(id);
    el.classList.toggle('done', stage > i);
    el.classList.toggle('active', stage === i);
  });
}
function setBusy(button, busy, busyText = 'Working…') {
  if (!button.dataset.originalLabel) button.dataset.originalLabel = button.innerHTML;
  button.disabled = busy;
  button.innerHTML = busy ? `<span class="button-spinner" aria-hidden="true"></span>${busyText}` : button.dataset.originalLabel;
}
async function responseJson(response, fallbackMessage) {
  let payload;
  try { payload = await response.json(); }
  catch (_) { throw new Error(`${fallbackMessage} (server returned an unreadable response)`); }
  if (!response.ok) throw new Error(payload.error || fallbackMessage);
  return payload;
}
async function health() {
  const badge = $('health');
  try {
    const response = await fetch('/api/health', { cache: 'no-store' });
    const j = await responseJson(response, 'Could not check local engines');
    const decoder = j.pyav ? 'PyAV / FFmpeg' : 'OpenCV decoder';
    const ocr = j.tesseract ? 'Tesseract ready' : 'Tesseract unavailable';
    badge.innerHTML = `<span class="status-dot"></span><span>${escapeHtml(decoder)} · ${escapeHtml(ocr)}</span>`;
    badge.className = 'status-pill good';
    badge.title = `OpenCV ${j.opencv || 'unknown'} · PyAV ${j.pyav_version || 'not installed'} · Tesseract ${j.tesseract_path || 'not found'}`;
  } catch (error) {
    badge.innerHTML = '<span class="status-dot"></span><span>Python engine offline</span>';
    badge.className = 'status-pill bad';
    badge.title = error.message;
  }
}
health();
$('provider').addEventListener('change', () => $('ollamaBox').classList.toggle('hide', $('provider').value !== 'ollama'));

function mediaSize() {
  if (!state.fallbackPreview && video.videoWidth > 0 && video.videoHeight > 0) return { w: video.videoWidth, h: video.videoHeight };
  return { w: fallbackPreview.naturalWidth || 0, h: fallbackPreview.naturalHeight || 0 };
}
function geometry() {
  const m = mediaSize(), cw = canvas.clientWidth, ch = canvas.clientHeight;
  if (!m.w || !m.h || !cw || !ch) return null;
  const scale = Math.min(cw / m.w, ch / m.h);
  const dw = m.w * scale, dh = m.h * scale;
  return { scale, ox: (cw - dw) / 2, oy: (ch - dh) / 2, w: m.w, h: m.h, cw, ch };
}
function syncCanvas() {
  const cw = Math.round(canvas.clientWidth || 1), ch = Math.round(canvas.clientHeight || 1);
  if (canvas.width !== cw) canvas.width = cw;
  if (canvas.height !== ch) canvas.height = ch;
  drawMarks();
}
function drawMarks() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const g = geometry(); if (!g) return;
  if (state.roi) {
    const [x, y, w, h] = state.roi;
    const sx = g.ox + x*g.scale, sy = g.oy + y*g.scale, sw = w*g.scale, sh = h*g.scale;
    ctx.fillStyle = 'rgba(120,234,223,.12)'; ctx.strokeStyle = '#78eadf'; ctx.lineWidth = 2;
    ctx.fillRect(sx, sy, sw, sh); ctx.strokeRect(sx, sy, sw, sh);
    ctx.fillStyle = '#78eadf'; ctx.fillRect(sx, sy - 3, Math.min(sw, 88), 3);
    ctx.font = '11px system-ui'; ctx.fillStyle = '#07121d';
    if (sw > 64 && sh > 20) { ctx.fillRect(sx + 5, sy + 5, 36, 17); ctx.fillStyle = '#78eadf'; ctx.fillText('TARGET', sx + 10, sy + 17); }
  }
  if (state.calibrationLine) {
    const [x1, y1, x2, y2] = state.calibrationLine;
    const ax=g.ox+x1*g.scale, ay=g.oy+y1*g.scale, bx=g.ox+x2*g.scale, by=g.oy+y2*g.scale;
    ctx.strokeStyle = '#ffd58b'; ctx.lineWidth = 2.5; ctx.setLineDash([7,4]);
    ctx.beginPath(); ctx.moveTo(ax,ay); ctx.lineTo(bx,by); ctx.stroke(); ctx.setLineDash([]);
    for (const [x,y] of [[ax,ay],[bx,by]]) { ctx.beginPath(); ctx.arc(x,y,4,0,Math.PI*2); ctx.fillStyle='#ffd58b';ctx.fill(); }
  }
}
function pointerToMedia(e) {
  const g = geometry(); if (!g) return null;
  const b = canvas.getBoundingClientRect();
  const x = (e.clientX - b.left - g.ox) / g.scale;
  const y = (e.clientY - b.top - g.oy) / g.scale;
  return { x: Math.min(g.w, Math.max(0, x)), y: Math.min(g.h, Math.max(0, y)) };
}
function getVideoTime() { return state.fallbackPreview ? 0 : (Number.isFinite(video.currentTime) ? video.currentTime : 0); }
function setMode(mode) {
  state.mode = mode;
  $('targetMode').classList.toggle('active', mode === 'target');
  $('calMode').classList.toggle('active', mode === 'calibrate');
  $('markInfo').textContent = mode === 'target'
    ? 'Draw a tight box around the object. The selected frame timestamp anchors the track.'
    : 'Draw a line from one end of a known ruler segment to the other, then enter its real-world length.';
  canvas.style.cursor = mode === 'target' ? 'crosshair' : 'cell';
}
function currentScale() {
  if (state.calibrationLine) {
    const [x1,y1,x2,y2] = state.calibrationLine;
    const pixelLength = Math.hypot(x2-x1, y2-y1);
    const realLength = Number($('knownLength').value);
    if (pixelLength > 1 && realLength > 0 && Number.isFinite(realLength)) {
      const scale = pixelLength / realLength;
      $('calculatedScale').value = scale.toFixed(3);
      $('scaleSource').value = `${pixelLength.toFixed(1)} px / ${realLength} m`;
      return { value: scale, source: { source: 'calibrated ruler line', pixel_length: pixelLength, known_length_m: realLength, px_per_m: scale } };
    }
  }
  $('calculatedScale').value = '';
  const manual = Number($('scale').value);
  $('scaleSource').value = Number.isFinite(manual) && manual > 0 ? 'Manual scale' : 'Not calibrated';
  return { value: manual, source: { source: 'manual pixels-per-metre input', px_per_m: manual } };
}
$('knownLength').addEventListener('input', currentScale);
$('scale').addEventListener('input', currentScale);
$('targetMode').onclick = () => setMode('target');
$('calMode').onclick = () => setMode('calibrate');
$('clearMarks').onclick = () => {
  state.roi = null; state.calibrationLine = null; state.roiTime = 0;
  $('analyze').disabled = true; currentScale(); drawMarks(); setPipeline(state.id ? 1 : 0);
  $('markInfo').textContent = 'Target and calibration marks cleared. Draw the target box again.';
};

canvas.addEventListener('pointerdown', e => {
  const p = pointerToMedia(e); if (!p || !state.id) return;
  if (!video.paused && !state.fallbackPreview) { video.pause(); updatePlaybackControls(); }
  state.start = p; state.drawing = true;
  if (canvas.setPointerCapture) canvas.setPointerCapture(e.pointerId);
});
canvas.addEventListener('pointermove', e => {
  if (!state.drawing || !state.start) return;
  const p = pointerToMedia(e); if (!p) return;
  if (state.mode === 'target') {
    const x = Math.min(state.start.x,p.x), y = Math.min(state.start.y,p.y);
    state.roi = [x,y,Math.abs(p.x-state.start.x),Math.abs(p.y-state.start.y)];
  } else {
    state.calibrationLine = [state.start.x,state.start.y,p.x,p.y]; currentScale();
  }
  drawMarks();
});
function finishDrawing(e) {
  if (!state.drawing || !state.start) return;
  state.drawing = false;
  const p = pointerToMedia(e) || state.start;
  if (state.mode === 'target') {
    const x = Math.min(state.start.x,p.x), y = Math.min(state.start.y,p.y);
    const w = Math.abs(p.x-state.start.x), h = Math.abs(p.y-state.start.y);
    if (w < 6 || h < 6) {
      state.roi = null; $('analyze').disabled = true;
      $('markInfo').textContent = 'That selection is too small. Draw a larger box around the target.';
      showToast('Choose a target area at least 6 × 6 pixels.', 'error');
    } else {
      state.roi = [x,y,w,h]; state.roiTime = getVideoTime(); $('analyze').disabled = false;
      $('markInfo').textContent = `Target selected · ${w.toFixed(0)} × ${h.toFixed(0)} source px · start ${state.roiTime.toFixed(3)} s`;
      setPipeline(1);
    }
  } else {
    state.calibrationLine = [state.start.x,state.start.y,p.x,p.y];
    const scale = currentScale();
    if (!scale.value || scale.value <= 0) $('markInfo').textContent = 'Enter a valid positive reference length to finish calibration.';
    else $('markInfo').textContent = `Scale calibrated · ${scale.value.toFixed(3)} px/m · keep ruler and target in the same plane.`;
  }
  state.start = null; drawMarks();
}
canvas.addEventListener('pointerup', finishDrawing);
canvas.addEventListener('pointercancel', finishDrawing);

let seekingByUser = false;
video.addEventListener('seeking', () => { seekingByUser = true; });
video.addEventListener('seeked', () => {
  updatePlaybackControls();
  if (seekingByUser) {
    seekingByUser = false;
    if (state.roi) {
      state.roi = null; state.roiTime = getVideoTime(); $('analyze').disabled = true;
      $('markInfo').textContent = 'Frame changed. Redraw the target box so the selection matches the current frame.';
    }
    drawMarks();
  }
});
video.addEventListener('loadedmetadata', () => { requestAnimationFrame(syncCanvas); updatePlaybackControls(); });
video.addEventListener('loadeddata', () => { requestAnimationFrame(syncCanvas); updatePlaybackControls(); });
video.addEventListener('timeupdate', updatePlaybackControls);
video.addEventListener('play', updatePlaybackControls);
video.addEventListener('pause', updatePlaybackControls);
video.addEventListener('error', () => {
  if (!state.id) return;
  state.fallbackPreview = true; video.classList.add('hide'); fallbackPreview.classList.remove('hide');
  fallbackPreview.src = state.serverPreview || '';
  $('videoControls').classList.add('hide');
  $('markInfo').textContent = 'This browser cannot play the video codec. The decoded first frame is available for selection; re-export as H.264 MP4 to select a later frame.';
  requestAnimationFrame(syncCanvas);
});
fallbackPreview.addEventListener('load', () => requestAnimationFrame(syncCanvas));
window.addEventListener('resize', () => { syncCanvas(); if (state.result) drawChart(state.result); });

function formatTime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) seconds = 0;
  const mins = Math.floor(seconds / 60), secs = seconds - mins * 60;
  return `${mins}:${secs.toFixed(2).padStart(5,'0')}`;
}
function updatePlaybackControls() {
  const duration = Number.isFinite(video.duration) ? video.duration : 0;
  $('scrubber').max = String(duration);
  if (!$('scrubber').matches(':active')) $('scrubber').value = String(Number.isFinite(video.currentTime) ? video.currentTime : 0);
  $('currentTime').textContent = formatTime(video.currentTime || 0);
  $('duration').textContent = formatTime(duration);
  $('playPause').textContent = video.paused ? '▶' : 'Ⅱ';
  $('playPause').setAttribute('aria-label', video.paused ? 'Play video' : 'Pause video');
}
$('playPause').addEventListener('click', async () => {
  if (video.paused) {
    try { await video.play(); } catch (_) { showToast('The browser could not play this video. Try an H.264 MP4.', 'error'); }
  } else video.pause();
  updatePlaybackControls();
});
$('scrubber').addEventListener('input', () => {
  const next = Number($('scrubber').value);
  if (Number.isFinite(next)) video.currentTime = next;
  $('currentTime').textContent = formatTime(next);
});

function resetResults() {
  state.result = null;
  $('report').disabled = true; $('download').disabled = true; $('csv').disabled = true;
  $('m1').textContent = '—'; $('m2').textContent = '—'; $('m3').textContent = '—'; $('m4').textContent = '—';
  $('m1u').textContent = 'Waiting for analysis'; $('m4u').textContent = 'Model dependent';
  $('obs').innerHTML = '<div class="obs-empty"><strong>Waiting for analysis</strong><span>Tracked positions and OCR readings will be listed here after you run an experiment.</span></div>';
  $('quality').innerHTML = '<div class="quality-status"><span class="status-dot"></span>Awaiting measurements</div>Quality checks will appear after analysis. They indicate track/model consistency—not guaranteed laboratory accuracy.';
  $('reportBox').innerHTML = '<div class="report-placeholder"><div><strong>Your lab report will appear here</strong><span>Run an analysis, then generate an offline or Ollama-assisted report.</span></div></div>';
  $('chartLabel').textContent = 'Select a video and run the analysis.';
  const c=$('chart'), g=c.getContext('2d'); g.clearRect(0,0,c.width,c.height);
}
async function loadVideoFile(file, origin = 'upload') {
  if (!file) { showToast('Choose a video file first.', 'error'); return; }
  const allowed = /\.(mp4|mov|avi|mkv|webm|m4v)$/i.test(file.name);
  if (!allowed) { showToast('Unsupported file type. Use MP4, MOV, AVI, MKV or WebM.', 'error'); return; }
  $('selectedFile').textContent = `${file.name} · ${(file.size / (1024*1024)).toFixed(1)} MB`;
  setBusy($('upload'), true, origin === 'sample' ? 'Loading sample…' : 'Loading video…');
  ['sampleFreefall','samplePendulum','sampleSpring'].forEach(id => { $(id).disabled = true; });
  setProgress('Uploading clip and checking decoder…', 7); setPipeline(0);
  try {
    const fd = new FormData(); fd.append('video', file);
    const r = await fetch('/api/upload', { method:'POST', body:fd });
    const j = await responseJson(r, 'Video upload failed');
    state.id = j.id; state.result = null; state.roi = null; state.calibrationLine = null; state.roiTime = 0;
    state.serverPreview = j.preview; state.fallbackPreview = false;
    if (state.objectUrl) URL.revokeObjectURL(state.objectUrl);
    state.objectUrl = URL.createObjectURL(file);
    video.pause(); video.classList.remove('hide'); fallbackPreview.classList.add('hide');
    video.src = state.objectUrl; video.load();
    $('videoWrap').classList.remove('hide'); $('videoEmpty').classList.add('hide'); $('videoControls').classList.remove('hide');
    $('meta').classList.remove('hide');
    $('meta').innerHTML = `<div class="meta-strip"><span class="meta-chip">${escapeHtml(j.filename || file.name)}</span><span class="meta-chip">${Number(j.meta.width)} × ${Number(j.meta.height)}</span><span class="meta-chip">${Number(j.meta.fps).toFixed(2)} FPS</span><span class="meta-chip">${Number(j.meta.duration).toFixed(2)} sec</span><span class="meta-chip">${escapeHtml(j.meta.decoder || 'Decoder ready')}</span></div>`;
    $('analyze').disabled = true; resetResults(); currentScale(); updatePlaybackControls(); syncCanvas(); setMode('target'); setPipeline(1);
    setProgress('Video ready. Pause on a clear frame and draw the target box.', 17);
    showToast('Video decoded successfully. Select the object to track.', 'success');
  } catch (e) {
    setProgress(e.message, 0); showToast(e.message, 'error');
  } finally {
    setBusy($('upload'), false);
    ['sampleFreefall','samplePendulum','sampleSpring'].forEach(id => { $(id).disabled = false; });
  }
}
$('file').addEventListener('change', () => {
  const file = $('file').files && $('file').files[0];
  $('selectedFile').textContent = file ? `${file.name} · ${(file.size / (1024*1024)).toFixed(1)} MB` : 'No video selected yet';
});
$('upload').onclick = () => loadVideoFile(($('file').files || [])[0], 'upload');
const dropzone = $('dropzone');
['dragenter','dragover'].forEach(eventName => dropzone.addEventListener(eventName, e => { e.preventDefault(); e.stopPropagation(); dropzone.classList.add('dragover'); }));
['dragleave','drop'].forEach(eventName => dropzone.addEventListener(eventName, e => { e.preventDefault(); e.stopPropagation(); dropzone.classList.remove('dragover'); }));
dropzone.addEventListener('drop', e => {
  const file = [...(e.dataTransfer?.files || [])].find(f => /\.(mp4|mov|avi|mkv|webm|m4v)$/i.test(f.name));
  if (file) loadVideoFile(file, 'upload'); else showToast('Drop a supported video file to begin.', 'error');
});
async function loadSample(name) {
  try {
    setProgress('Fetching included sample clip…', 3); showToast('Loading bundled sample clip…');
    const response = await fetch(`/samples/${encodeURIComponent(name)}`);
    if (!response.ok) throw new Error('Could not load bundled sample. Check that the samples folder is present.');
    const blob = await response.blob();
    const file = new File([blob], name, { type: blob.type || 'video/mp4' });
    await loadVideoFile(file, 'sample');
    $('file').value = '';
  } catch (e) { setProgress(e.message, 0); showToast(e.message, 'error'); }
}
$('sampleFreefall').onclick = () => loadSample('synthetic-freefall.mp4');
$('samplePendulum').onclick = () => loadSample('synthetic-pendulum.mp4');
$('sampleSpring').onclick = () => loadSample('synthetic-spring-oscillator.mp4');

$('analyze').onclick = async () => {
  if (!state.id || !state.roi) { showToast('Load a video and draw a tight box around its target first.', 'error'); return; }
  const experimentModel = $('model').value;
  const scale = currentScale();
  const validScale = Number.isFinite(scale.value) && scale.value > 0;
  if (experimentModel === 'freefall' && !validScale) { showToast('Free fall needs scale calibration. Draw a ruler line or enter a measured pixels-per-metre value.', 'error'); return; }
  const length = Number($('length').value), mass = Number($('mass').value), ocrCount = Number($('ocrCount').value);
  if (experimentModel === 'pendulum' && (!Number.isFinite(length) || length <= 0)) { showToast('Enter a valid positive pendulum length.', 'error'); return; }
  if (experimentModel === 'oscillator' && (!Number.isFinite(mass) || mass <= 0)) { showToast('Enter a valid positive oscillating mass.', 'error'); return; }
  if (!Number.isFinite(ocrCount) || ocrCount < 4 || ocrCount > 40) { showToast('OCR samples must be between 4 and 40.', 'error'); return; }
  const payload = { id: state.id, roi: state.roi, start_time_s: state.roiTime,
    model: experimentModel, px_per_m: validScale ? scale.value : 1.0,
    calibration: validScale ? scale.source : { source: 'not required by period-based model' },
    ocr_count: ocrCount, length_m: length, mass_kg: mass };
  $('analyze').disabled = true; $('report').disabled = true; $('download').disabled = true; $('csv').disabled = true;
  setPipeline(2); setProgress('Decoding sequential frames, tracking features and reading visible numbers…', 27);
  $('reportBox').textContent = 'Analysis in progress…';
  try {
    const r = await fetch('/api/analyze', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload) });
    const j = await responseJson(r, 'Analysis failed');
    state.result = j.result; setProgress('Analysis complete. Review the data quality before using the result.', 100); render(j.result);
    $('report').disabled = false; $('download').disabled = false; $('csv').disabled = false;
    setPipeline(3);
    const qualityTier = j.result.quality?.tier || 'review result';
    $('progress').textContent = `Complete · ${j.result.motion_count} positions · ${(j.result.ocr||[]).length} OCR samples · ${qualityTier}`;
    $('reportBox').innerHTML = '<div class="report-placeholder"><div><strong>Measurements are ready</strong><span>Generate the report to review interpretation, likely error sources and experimental limitations.</span></div></div>';
    showToast(`Analysis complete: ${j.result.motion_count} positions measured.`, 'success');
  } catch (e) {
    setProgress(e.message, 0); $('reportBox').textContent = `Analysis stopped safely: ${e.message}`; setPipeline(1); showToast(e.message, 'error');
  } finally { $('analyze').disabled = !state.roi; }
};

function format(x, digits=3) { const n = Number(x); return Number.isFinite(n) ? n.toFixed(digits) : '—'; }
function render(x) {
  const primary = x.model === 'oscillator' ? x.k : x.g;
  $('m1l').textContent = x.model === 'oscillator' ? 'Estimated spring constant' : 'Estimated gravity';
  $('m1').textContent = format(primary,4); $('m1u').textContent = x.model === 'oscillator' ? 'N/m' : 'm/s²';
  $('m2').textContent = Number.isFinite(Number(x.motion_count)) ? String(x.motion_count) : '—';
  $('m3').textContent = `${format(x.tracking?.tracking_coverage_pct,1)}%`;
  const fitRmse = x.rmse != null ? x.rmse : x.period_fit_rmse_px;
  $('m4').textContent = fitRmse == null ? '—' : format(fitRmse,5);
  $('m4u').textContent = x.model === 'freefall' ? 'metres · RMS residual' : (x.period_fit_rmse_px == null ? 'Model dependent' : `pixels · ${x.axis_used} axis`);
  let lines = `MODEL       ${x.model}\nDECODER     ${x.tracking?.decoder || 'unknown'}\nTIME SOURCE ${x.tracking?.timestamp_source || 'unknown'}\nSTART TIME  ${format(x.tracking?.actual_start_time_s,5)} s\nCOVERAGE    ${format(x.tracking?.tracking_coverage_pct,2)}%\nMATCH SCORE ${format(x.tracking?.mean_match_score,3)}\nSCALE       ${format(x.px_per_m,3)} px/m\n\nFRAME  TIME(s)  X(px)  Y(px)  SCORE  METHOD\n`;
  const raw = x.raw_motion || [];
  lines += raw.slice(0,500).map(o=>`${o.frame}\t${format(o.t,5)}\t${format(o.x,2)}\t${format(o.y,2)}\t${format(o.score,3)}\t${o.method}`).join('\n');
  if (raw.length > 500) lines += `\n… ${raw.length-500} additional observations are available in the JSON/CSV export.`;
  if ((x.ocr||[]).length) {
    lines += '\n\nOCR · visible numeric text only\n';
    lines += x.ocr.map(o=>`${o.t}s  ${(o.tokens||[]).map(t=>`${t.text} [${t.confidence}%]`).join(' | ')}`).join('\n');
  } else lines += `\n\nOCR: No legible numeric readings detected in sampled frames.${x.ocr_error ? `\nOCR note: ${x.ocr_error}` : ''}`;
  $('obs').textContent = lines;
  const q = x.quality || {}, w = q.warnings || [];
  const tier = q.tier || 'Quality not classified';
  const tierLower = tier.toLowerCase();
  const tone = tierLower.includes('high') ? 'good' : tierLower.includes('moderate') ? 'moderate' : 'review';
  $('quality').innerHTML = `<div class="quality-status ${tone}"><span class="status-dot"></span>${escapeHtml(tier)}</div><div class="quality-lines">Model-fit R²: <b>${q.model_fit_r2 == null ? 'not supplied' : format(q.model_fit_r2,4)}</b><br>Tracking score: <b>${format(q.mean_tracking_score,3)}</b><br>Decoder: <b>${escapeHtml(x.tracking?.decoder || 'unknown')}</b><br>Scale source: <b>${escapeHtml(x.scale_calibration?.source || 'unknown')}</b>${w.length ? `<br><br><b class="warn">Review before use</b><br>${w.map(v=>'• '+escapeHtml(v)).join('<br>')}` : '<br><br>No automated warning was triggered; this does not guarantee experimental accuracy.'}</div>`;
  $('chartLabel').textContent = x.model === 'freefall' ? 'Vertical displacement in metres (screen y is positive down) versus actual frame timestamps.' : `Tracked ${x.axis_used || 'position'} axis in source pixels versus actual frame timestamps; see period estimate in the report.`;
  drawChart(x);
}
function escapeHtml(s) { return String(s).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function drawChart(x) {
  const c = $('chart');
  const displayW = Math.max(280, Math.floor(c.clientWidth || 900));
  const displayH = Math.max(200, Math.floor(c.clientHeight || 280));
  const dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
  if (c.width !== Math.floor(displayW*dpr) || c.height !== Math.floor(displayH*dpr)) { c.width = Math.floor(displayW*dpr); c.height = Math.floor(displayH*dpr); }
  const g = c.getContext('2d'); g.setTransform(dpr,0,0,dpr,0,0);
  const w=displayW,h=displayH;
  g.clearRect(0,0,w,h);
  const t=x.t||[], y=x.observed||[]; if (!t.length || !y.length) {
    g.fillStyle='#7182a2';g.font='12px system-ui';g.textAlign='center';g.fillText('Motion chart will appear after analysis',w/2,h/2);g.textAlign='left';return;
  }
  const vals = y.concat(x.fitted||[]).filter(v=>Number.isFinite(Number(v))).map(Number); if(!vals.length)return;
  const minT=Math.min(...t), maxT=Math.max(...t), spread=Math.max(1e-6,Math.max(...vals)-Math.min(...vals)), pad=spread*.09;
  const minY=Math.min(...vals)-pad,maxY=Math.max(...vals)+pad;
  const left=52,right=14,top=18,bottom=31,plotW=w-left-right,plotH=h-top-bottom;
  const sx=v=>left+(v-minT)/(maxT-minT||1)*plotW, sy=v=>top+plotH-(v-minY)/(maxY-minY||1)*plotH;
  g.font='10px system-ui';g.strokeStyle='rgba(130,153,190,.16)';g.fillStyle='#8293b2';g.lineWidth=1;
  for(let i=0;i<5;i++){const yy=top+i*plotH/4;g.beginPath();g.moveTo(left,yy);g.lineTo(w-right,yy);g.stroke();const val=maxY-(maxY-minY)*i/4;g.fillText(val.toFixed(2),5,yy+3);}
  for(let i=0;i<5;i++){const xx=left+i*plotW/4;g.beginPath();g.moveTo(xx,top);g.lineTo(xx,top+plotH);g.stroke();g.fillText((minT+(maxT-minT)*i/4).toFixed(2),xx-12,h-10);}
  g.fillStyle='#78eadf';for(let i=0;i<t.length;i++){const yy=Number(y[i]);if(!Number.isFinite(yy))continue;g.beginPath();g.arc(sx(Number(t[i])),sy(yy),2.4,0,Math.PI*2);g.fill();}
  if(x.fitted){g.strokeStyle='#8bb7ff';g.lineWidth=2;g.beginPath();let started=false;x.fitted.forEach((v,i)=>{if(!Number.isFinite(Number(v)))return;const X=sx(Number(t[i])),Y=sy(Number(v));if(!started){g.moveTo(X,Y);started=true;}else g.lineTo(X,Y);});g.stroke();}
}
$('report').onclick = async () => {
  if (!state.result) return;
  const payload={id:state.result._result_id,provider:$('provider').value,base_url:$('ollamaUrl').value.trim(),model:$('ollamaModel').value.trim()};
  $('report').disabled = true; $('reportBox').textContent='Preparing the report from calculated observations…';
  try {
    const r=await fetch('/api/report',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const j=await responseJson(r,'Report generation failed');
    const finalReport = j.report+(j.warning?'\n\n[Offline report used because the AI provider was unavailable] '+j.warning:'');
    $('reportBox').innerHTML = renderReport(finalReport);
    setPipeline(4);
    if (j.warning) showToast('AI report unavailable; a deterministic local report was generated instead.');
    else showToast('Lab report generated.', 'success');
  } catch(e) { $('reportBox').textContent=`Could not generate report: ${e.message}`;showToast(e.message,'error'); }
  finally { $('report').disabled = false; }
};
function downloadFile(name, text, type) {
  const blob=new Blob([text],{type}),url=URL.createObjectURL(blob),a=document.createElement('a');
  a.href=url;a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1200);
}

function renderReport(text) {
  // Deliberately small, safe Markdown subset: escape first, then format only simple text syntax.
  const escaped = escapeHtml(String(text || ''));
  const lines = escaped.split(/\r?\n/);
  const out = [];
  let listOpen = false;
  const closeList = () => { if (listOpen) { out.push('</ul>'); listOpen = false; } };
  const inline = s => s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/`([^`]+)`/g, '<code>$1</code>');
  for (const raw of lines) {
    const line = raw.trimEnd();
    if (!line.trim()) { closeList(); continue; }
    if (/^###\s+/.test(line)) { closeList(); out.push(`<h3>${inline(line.replace(/^###\s+/,''))}</h3>`); }
    else if (/^##\s+/.test(line)) { closeList(); out.push(`<h3>${inline(line.replace(/^##\s+/,''))}</h3>`); }
    else if (/^#\s+/.test(line)) { closeList(); out.push(`<h2>${inline(line.replace(/^#\s+/,''))}</h2>`); }
    else if (/^[-*]\s+/.test(line)) { if (!listOpen) { out.push('<ul>'); listOpen = true; } out.push(`<li>${inline(line.replace(/^[-*]\s+/,''))}</li>`); }
    else { closeList(); out.push(`<p>${inline(line)}</p>`); }
  }
  closeList();
  return out.join('');
}

$('download').onclick = () => { if(state.result)downloadFile('kinetiq-observations.json',JSON.stringify(state.result,null,2),'application/json'); };
$('csv').onclick = () => {
  if(!state.result)return;
  const rows=[['frame','time_s','x_px','y_px','tracking_score','peak_margin','method']];
  for(const o of state.result.raw_motion||[])rows.push([o.frame,o.t,o.x,o.y,o.score,o.peak_margin,o.method]);
  const csv=rows.map(row=>row.map(v=>{const s=String(v??'');return /[",\n]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s;}).join(',')).join('\r\n');
  downloadFile('kinetiq-motion-observations.csv',csv,'text/csv;charset=utf-8');
};
setMode('target');
