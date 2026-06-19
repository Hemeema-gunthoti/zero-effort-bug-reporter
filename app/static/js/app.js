function toggleTheme() {
  const html = document.documentElement;
  const icon = document.getElementById('theme-icon');
  const current = html.getAttribute('data-theme');
  const next = current === 'light' ? 'dark' : 'light';
  html.setAttribute('data-theme', next);
  localStorage.setItem('theme', next);
  if (icon) icon.className = next === 'dark' ? 'fas fa-sun' : 'fas fa-moon';
}

const savedTheme = localStorage.getItem('theme') || 'light';
document.documentElement.setAttribute('data-theme', savedTheme);
const themeIcon = document.getElementById('theme-icon');
if (themeIcon) themeIcon.className = savedTheme === 'dark' ? 'fas fa-sun' : 'fas fa-moon';

function togglePassword() {
  const input = document.getElementById('password');
  const icon = document.getElementById('eye-icon');
  if (input.type === 'password') {
    input.type = 'text';
    icon.className = 'fas fa-eye-slash';
  } else {
    input.type = 'password';
    icon.className = 'fas fa-eye';
  }
}

async function doLogin() {
  const username = document.getElementById('username').value.trim();
  const password = document.getElementById('password').value.trim();
  const btn = document.getElementById('login-btn');
  const errorMsg = document.getElementById('error-message');
  const validationMsg = document.getElementById('validation-error');
  const successMsg = document.getElementById('success-message');

  errorMsg.classList.remove('show');
  validationMsg.classList.remove('show');
  successMsg.classList.remove('show');

  if (!username || !password) {
    validationMsg.classList.add('show');
    return;
  }

  btn.classList.add('loading');
  btn.disabled = true;

  try {
    const form = new FormData();
    form.append('username', username);
    form.append('password', password);

    const res = await fetch('/login', { method: 'POST', body: form });
    const data = await res.json();

    if (res.ok && data.status === 'success') {
      successMsg.classList.add('show');
      setTimeout(() => {
        window.location.href = data.redirect;
      }, 800);
    } else {
      errorMsg.classList.add('show');
      btn.classList.remove('loading');
      btn.disabled = false;
    }
  } catch (err) {
    console.error('Login error:', err);
    errorMsg.classList.add('show');
    btn.classList.remove('loading');
    btn.disabled = false;
  }
}

document.addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    const loginForm = document.getElementById('login-form');
    if (loginForm && document.activeElement.closest('#login-form')) {
      doLogin();
    }
  }
});