(() => {
  const $ = (selector) => document.querySelector(selector);
  let mode = 'login';
  const setMode = (next) => {
    mode = next;
    const register = mode === 'register';
    $('#loginTab').classList.toggle('active', !register);
    $('#registerTab').classList.toggle('active', register);
    $('#title').textContent = register ? 'Create your workspace' : 'Welcome back';
    $('#modeEyebrow').textContent = register ? 'GET STARTED' : 'YOUR WORKSPACE';
    $('#intro').textContent = register ? 'Create an account to register your website and view its aggregate analytics.' : 'Sign in to manage registered websites and view aggregate analytics.';
    $('#submitBtn').textContent = register ? 'Create account' : 'Sign in';
    $('#passwordHelp').textContent = register ? 'Use at least 10 characters. A passphrase is a good choice.' : 'Use your account password.';
    $('#password').autocomplete = register ? 'new-password' : 'current-password';
    $('#error').textContent = ''; $('#success').textContent = '';
  };
  $('#loginTab').addEventListener('click', () => setMode('login'));
  $('#registerTab').addEventListener('click', () => setMode('register'));
  $('#authForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = $('#submitBtn'); button.disabled = true; $('#error').textContent = ''; $('#success').textContent = '';
    try {
      const response = await fetch(`/api/auth/${mode}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
        body: JSON.stringify({ email: $('#email').value, password: $('#password').value })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
      $('#success').textContent = registerNotice(mode);
      window.location.replace('/');
    } catch (error) {
      $('#error').textContent = error.message || 'Could not complete the request.';
    } finally { button.disabled = false; }
  });
  function registerNotice(currentMode) { return currentMode === 'register' ? 'Account created. Opening your workspace…' : 'Signed in. Opening your workspace…'; }
  fetch('/api/auth/me', { cache: 'no-store' }).then(response => { if (response.ok) window.location.replace('/'); }).catch(() => {});
  setMode('login');
})();
