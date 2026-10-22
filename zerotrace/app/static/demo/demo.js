(() => {
  const $ = (selector) => document.querySelector(selector);
  const form = $('#registrationForm');
  const step1 = $('#formStep1');
  const step2 = $('#formStep2');
  const success = $('#formSuccess');
  const step1dot = $('#step1dot');
  const step2dot = $('#step2dot');
  $('#nextStep').addEventListener('click', () => {
    step1.hidden = true; step2.hidden = false;
    step1dot.classList.remove('active'); step2dot.classList.add('active');
    $('#demoInterest').focus();
  });
  $('#backStep').addEventListener('click', () => {
    step2.hidden = true; step1.hidden = false;
    step2dot.classList.remove('active'); step1dot.classList.add('active');
  });
  form.addEventListener('submit', event => {
    event.preventDefault();
    // The form fields are deliberately not sent to a backend or read by analytics.
    step1.hidden = true; step2.hidden = true; success.hidden = false;
    step1dot.classList.remove('active'); step2dot.classList.remove('active');
  });
  $('#again').addEventListener('click', () => {
    form.reset(); success.hidden = true; step1.hidden = false;
    step1dot.classList.add('active'); step2dot.classList.remove('active');
  });
  $('#flushNow').addEventListener('click', async () => {
    const button = $('#flushNow');
    button.disabled = true; button.textContent = 'Sending…';
    try {
      if (!window.ZeroTrace) throw new Error('SDK not loaded yet');
      const result = await window.ZeroTrace.flushNow();
      if (!result.ok) throw new Error('Batch was not accepted');
      button.textContent = result.empty ? 'No new counts' : 'Aggregate sent ✓';
      $('#ztStatus').classList.remove('error');
    } catch {
      button.textContent = 'Retry send'; $('#ztStatus').classList.add('error');
    }
    setTimeout(() => { button.textContent = 'Send aggregate batch'; button.disabled = false; }, 1800);
  });
  window.addEventListener('zerotrace:flush-error', () => $('#ztStatus').classList.add('error'));
})();
