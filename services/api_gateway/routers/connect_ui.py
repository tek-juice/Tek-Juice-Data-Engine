"""
DATA ENGINE — Connect Wizard UI
Serves the self-service onboarding wizard at GET /connect.
A single self-contained HTML page — no external dependencies, no CDN.
The entire 3-step flow lives here:
  Step 1 — Product details form
  Step 2 — Email verification holding page
  Step 3 — Architecture scan + credential entry (served from onboard_router after email click)
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from configs.settings import get_settings

router = APIRouter(tags=["Onboarding"])


@router.get("/connect", response_class=HTMLResponse, include_in_schema=False)
async def connect_wizard():
    """
    The public self-service connect page.
    Any company visits this URL to start connecting their product.
    No authentication, no secret in the URL — email verification is the guard.
    """
    s = get_settings()
    engine_url = s.engine_public_url
    return HTMLResponse(_wizard_html(engine_url))


def _wizard_html(engine_url: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Connect Your Product — Tek Juice Data Engine</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
    font-size: 15px;
    background: #f7f8fa;
    color: #1f2328;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
  }}

  .wrap {{
    width: 100%;
    max-width: 520px;
  }}

  /* ── Brand header ── */
  .brand {{
    text-align: center;
    margin-bottom: 28px;
  }}
  .brand-name {{
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #3b82d4;
    margin-bottom: 4px;
  }}
  .brand-tagline {{
    font-size: 13px;
    color: #57606a;
  }}

  /* ── Card ── */
  .card {{
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 12px;
    padding: 40px;
  }}

  /* ── Steps indicator ── */
  .steps {{
    display: flex;
    align-items: center;
    gap: 0;
    margin-bottom: 32px;
  }}
  .step-dot {{
    width: 28px;
    height: 28px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
    font-weight: 700;
    flex-shrink: 0;
    transition: all .3s;
  }}
  .step-dot.active   {{ background: #3b82d4; color: #fff; }}
  .step-dot.done     {{ background: #16a34a; color: #fff; }}
  .step-dot.inactive {{ background: #e5e7eb; color: #57606a; }}
  .step-line {{
    flex: 1;
    height: 2px;
    background: #e5e7eb;
    transition: background .3s;
  }}
  .step-line.done {{ background: #16a34a; }}

  /* ── Form ── */
  h2 {{
    font-size: 22px;
    font-weight: 700;
    margin-bottom: 6px;
  }}
  .sub {{
    color: #57606a;
    font-size: 14px;
    margin-bottom: 28px;
    line-height: 1.5;
  }}

  .field {{ margin-bottom: 18px; }}
  label {{
    display: block;
    font-size: 13px;
    font-weight: 600;
    color: #57606a;
    margin-bottom: 5px;
  }}
  input {{
    width: 100%;
    border: 1px solid #e5e7eb;
    border-radius: 8px;
    padding: 11px 14px;
    font-size: 15px;
    color: #1f2328;
    outline: none;
    transition: border-color .15s, box-shadow .15s;
    background: #fff;
  }}
  input:focus {{
    border-color: #3b82d4;
    box-shadow: 0 0 0 3px rgba(59,130,212,.12);
  }}
  input::placeholder {{ color: #adb5bd; }}

  /* ── Button ── */
  .btn {{
    width: 100%;
    padding: 14px;
    background: #3b82d4;
    color: #fff;
    border: none;
    border-radius: 8px;
    font-size: 15px;
    font-weight: 600;
    cursor: pointer;
    margin-top: 8px;
    transition: opacity .15s;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
  }}
  .btn:hover:not(:disabled) {{ opacity: .9; }}
  .btn:disabled {{ opacity: .55; cursor: not-allowed; }}

  /* ── Spinner ── */
  .spinner {{
    width: 18px;
    height: 18px;
    border: 2.5px solid rgba(255,255,255,.4);
    border-top-color: #fff;
    border-radius: 50%;
    animation: spin .7s linear infinite;
    display: none;
  }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}

  /* ── Error / info boxes ── */
  .err-box {{
    background: #fff1f2;
    border: 1px solid #fecdd3;
    color: #be123c;
    padding: 12px 14px;
    border-radius: 8px;
    margin-top: 14px;
    font-size: 13px;
    display: none;
  }}
  .info-box {{
    background: #eff6ff;
    border: 1px solid #bfdbfe;
    color: #1d4ed8;
    padding: 14px;
    border-radius: 8px;
    margin-bottom: 24px;
    font-size: 13px;
    line-height: 1.6;
  }}

  /* ── Check screen ── */
  .check-screen {{
    text-align: center;
    display: none;
  }}
  .check-icon {{
    font-size: 52px;
    margin-bottom: 16px;
  }}
  .check-screen h2 {{ margin-bottom: 8px; }}
  .check-screen p {{
    color: #57606a;
    font-size: 14px;
    line-height: 1.6;
    margin-bottom: 4px;
  }}
  .email-highlight {{
    font-weight: 700;
    color: #1f2328;
  }}
  .resend-link {{
    color: #3b82d4;
    cursor: pointer;
    font-size: 13px;
    margin-top: 20px;
    display: inline-block;
  }}
  .resend-link:hover {{ text-decoration: underline; }}

  /* ── What you get section ── */
  .benefits {{
    margin-top: 28px;
    padding-top: 24px;
    border-top: 1px solid #f0f0f0;
  }}
  .benefits-title {{
    font-size: 12px;
    font-weight: 700;
    letter-spacing: .06em;
    text-transform: uppercase;
    color: #57606a;
    margin-bottom: 12px;
  }}
  .benefit {{
    display: flex;
    align-items: flex-start;
    gap: 10px;
    margin-bottom: 10px;
    font-size: 13px;
    color: #57606a;
  }}
  .benefit-dot {{
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: #3b82d4;
    margin-top: 6px;
    flex-shrink: 0;
  }}

  /* ── Footer ── */
  .footer {{
    text-align: center;
    margin-top: 20px;
    font-size: 12px;
    color: #adb5bd;
  }}
  .footer a {{ color: #adb5bd; text-decoration: none; }}
  .footer a:hover {{ color: #57606a; }}
</style>
</head>
<body>
<div class="wrap">

  <!-- Brand -->
  <div class="brand">
    <div class="brand-name">Tek Juice Data Engine</div>
    <div class="brand-tagline">AI-powered search dominance for your product</div>
  </div>

  <!-- Card -->
  <div class="card">

    <!-- Steps indicator -->
    <div class="steps">
      <div class="step-dot active"  id="dot-1">1</div>
      <div class="step-line"        id="line-1"></div>
      <div class="step-dot inactive" id="dot-2">2</div>
      <div class="step-line"        id="line-2"></div>
      <div class="step-dot inactive" id="dot-3">3</div>
    </div>

    <!-- ── Step 1: The Form ── -->
    <div id="step-1">
      <h2>Connect your product</h2>
      <p class="sub">Fill in 3 fields and click Connect. We do the rest — automatically.</p>

      <div class="field">
        <label for="product_name">Product / Company Name</label>
        <input id="product_name" type="text"  placeholder="e.g. Acme Tourism Kenya" autocomplete="organization" />
      </div>
      <div class="field">
        <label for="website_url">Your Website URL</label>
        <input id="website_url"  type="url"   placeholder="https://your-website.com" autocomplete="url" />
      </div>
      <div class="field">
        <label for="admin_email">Your Email Address</label>
        <input id="admin_email"  type="email" placeholder="you@your-company.com" autocomplete="email" />
      </div>

      <button class="btn" id="connect-btn" onclick="submitForm()">
        <span id="btn-text">Connect My Product →</span>
        <div class="spinner" id="btn-spinner"></div>
      </button>
      <div class="err-box" id="err-box"></div>

      <div class="benefits">
        <div class="benefits-title">What happens after you connect</div>
        <div class="benefit"><div class="benefit-dot"></div><span>Your website is crawled and analysed automatically</span></div>
        <div class="benefit"><div class="benefit-dot"></div><span>AI writes and publishes content that ranks on Google, ChatGPT, and Gemini</span></div>
        <div class="benefit"><div class="benefit-dot"></div><span>Your visibility grows continuously — no ongoing effort from you</span></div>
        <div class="benefit"><div class="benefit-dot"></div><span>You watch the results in your dashboard</span></div>
      </div>
    </div>

    <!-- ── Step 2: Check email ── -->
    <div id="step-2" class="check-screen">
      <div class="check-icon">📬</div>
      <h2>Check your inbox</h2>
      <p>We've sent a verification link to</p>
      <p><span class="email-highlight" id="confirm-email"></span></p>
      <p style="margin-top:12px;">Click the link in that email to continue.<br>It takes less than a minute.</p>
      <p style="margin-top:16px;font-size:13px;color:#adb5bd;">Check your spam folder if you don't see it.</p>
      <span class="resend-link" onclick="resendEmail()">Didn't receive it? Send again</span>
      <div class="err-box" id="resend-err" style="text-align:left"></div>
    </div>

  </div>

  <div class="footer">
    Tek Juice Data Engine &nbsp;·&nbsp; <a href="/docs">API Docs</a>
  </div>
</div>

<script>
const ENGINE_URL = "{engine_url}";
let submittedEmail = '';
let submittedName  = '';

// ── Submit form ──────────────────────────────────────────────────────────────
async function submitForm() {{
  const name  = document.getElementById('product_name').value.trim();
  const url   = document.getElementById('website_url').value.trim();
  const email = document.getElementById('admin_email').value.trim();
  const err   = document.getElementById('err-box');
  err.style.display = 'none';

  // Basic validation
  if (!name)  return showErr('Please enter your product or company name.');
  if (!url)   return showErr('Please enter your website URL.');
  if (!url.startsWith('http')) return showErr('Website URL must start with https:// or http://');
  if (!email) return showErr('Please enter your email address.');
  if (!email.includes('@')) return showErr('Please enter a valid email address.');

  // Show spinner
  setLoading(true);

  try {{
    const r = await fetch(ENGINE_URL + '/onboard', {{
      method:  'POST',
      headers: {{'Content-Type': 'application/json'}},
      body:    JSON.stringify({{
        product_name: name,
        website_url:  url,
        admin_email:  email,
      }}),
    }});

    const d = await r.json();

    if (r.status === 409) {{
      setLoading(false);
      return showErr('An account with that email already exists. Check your inbox for the verification link.');
    }}
    if (!r.ok) {{
      setLoading(false);
      return showErr(d.detail || d.message || 'Something went wrong. Please try again.');
    }}

    // Success — advance to step 2
    submittedEmail = email;
    submittedName  = name;
    advanceTo(2);

  }} catch (e) {{
    setLoading(false);
    showErr('Network error. Please check your internet connection and try again.');
  }}
}}

// ── Resend verification email ────────────────────────────────────────────────
async function resendEmail() {{
  if (!submittedEmail) return;
  try {{
    await fetch(ENGINE_URL + '/onboard', {{
      method:  'POST',
      headers: {{'Content-Type': 'application/json'}},
      body:    JSON.stringify({{
        product_name: submittedName,
        website_url:  'https://resend.placeholder',
        admin_email:  submittedEmail,
      }}),
    }});
    document.getElementById('resend-err').style.display = 'block';
    document.getElementById('resend-err').style.background = '#f0fdf4';
    document.getElementById('resend-err').style.borderColor = '#bbf7d0';
    document.getElementById('resend-err').style.color = '#16a34a';
    document.getElementById('resend-err').textContent = '✅ Sent! Check your inbox again.';
  }} catch (e) {{
    document.getElementById('resend-err').style.display = 'block';
    document.getElementById('resend-err').textContent = 'Could not resend. Please try again.';
  }}
}}

// ── UI helpers ───────────────────────────────────────────────────────────────
function advanceTo(step) {{
  document.getElementById('step-1').style.display = step === 1 ? 'block' : 'none';
  document.getElementById('step-2').style.display = step === 2 ? 'block' : 'none';

  // Update step dots
  ['1','2','3'].forEach(n => {{
    const dot  = document.getElementById('dot-'  + n);
    const line = document.getElementById('line-' + n);
    const num  = parseInt(n);
    dot.className  = 'step-dot ' + (num < step ? 'done' : num === step ? 'active' : 'inactive');
    if (line) line.className = 'step-line ' + (num < step ? 'done' : '');
  }});

  if (step === 2) {{
    document.getElementById('confirm-email').textContent = submittedEmail;
  }}
  setLoading(false);
}}

function setLoading(on) {{
  const btn     = document.getElementById('connect-btn');
  const spinner = document.getElementById('btn-spinner');
  const text    = document.getElementById('btn-text');
  btn.disabled         = on;
  spinner.style.display= on ? 'block' : 'none';
  text.textContent     = on ? 'Connecting…' : 'Connect My Product →';
}}

function showErr(msg) {{
  const el = document.getElementById('err-box');
  el.textContent    = msg;
  el.style.display  = 'block';
}}

// Allow Enter key to submit
document.addEventListener('keydown', e => {{
  if (e.key === 'Enter') submitForm();
}});
</script>
</body>
</html>"""
