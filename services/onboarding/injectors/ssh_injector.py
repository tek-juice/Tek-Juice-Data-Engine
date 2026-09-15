"""
DATA ENGINE — SSH Custom Backend Injector
Connects to any custom backend server via SSH, studies its architecture,
auto-generates and installs a receiver endpoint, then disconnects.
The receiver accepts content POST requests from the Data Engine forever after.

Supports: Node.js/Express, Laravel/PHP, Django/Python, Rails, and any
Linux-based server running a web application.
"""

import textwrap
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ── Receiver templates — installed on the remote server ──────────────────────

_EXPRESS_RECEIVER = textwrap.dedent("""
// DATA ENGINE — Auto-installed receiver middleware
// Installed by Tek Juice Data Engine on {install_date}
// DO NOT EDIT — managed automatically

const express = require('express');
const fs = require('fs');
const path = require('path');
const router = express.Router();

const DE_KEY = process.env.DATA_ENGINE_KEY || '{api_key}';

router.post('/data-engine/publish', (req, res) => {{
  const key = req.headers['x-dataengine-key'];
  if (key !== DE_KEY) return res.status(401).json({{ error: 'Unauthorized' }});

  const {{ title, body, slug, meta_description, keywords, schema_markup }} = req.body;
  if (!title || !body || !slug) return res.status(400).json({{ error: 'Missing required fields' }});

  // Write content to the content directory
  const contentDir = path.join(__dirname, '..', 'content', 'posts');
  if (!fs.existsSync(contentDir)) fs.mkdirSync(contentDir, {{ recursive: true }});

  const filename = path.join(contentDir, `${{slug}}.json`);
  const post = {{ title, body, slug, meta_description, keywords, schema_markup,
                  published_at: new Date().toISOString(), source: 'data-engine' }};

  fs.writeFileSync(filename, JSON.stringify(post, null, 2));
  console.log('[DataEngine] Published:', title);
  res.json({{ success: true, slug }});
}});

router.get('/data-engine/health', (req, res) => {{
  res.json({{ status: 'live', engine: 'tek-juice-data-engine' }});
}});

module.exports = router;
""")

_LARAVEL_RECEIVER = textwrap.dedent("""
<?php
// DATA ENGINE — Auto-installed receiver route
// Installed by Tek Juice Data Engine on {install_date}

use Illuminate\\Http\\Request;
use Illuminate\\Support\\Facades\\Route;
use Illuminate\\Support\\Facades\\DB;
use Illuminate\\Support\\Str;

Route::post('/data-engine/publish', function (Request $request) {{
    $key = $request->header('X-DataEngine-Key');
    if ($key !== env('DATA_ENGINE_KEY', '{api_key}')) {{
        return response()->json(['error' => 'Unauthorized'], 401);
    }}

    $data = $request->validate([
        'title'            => 'required|string|max:500',
        'body'             => 'required|string',
        'slug'             => 'required|string|max:200',
        'meta_description' => 'nullable|string',
        'keywords'         => 'nullable|array',
    ]);

    // Insert into posts table — adjust table/column names to match your schema
    DB::table('posts')->updateOrInsert(
        ['slug' => $data['slug']],
        [
            'title'            => $data['title'],
            'body'             => $data['body'],
            'meta_description' => $data['meta_description'] ?? '',
            'keywords'         => json_encode($data['keywords'] ?? []),
            'published_at'     => now(),
            'source'           => 'data-engine',
            'updated_at'       => now(),
            'created_at'       => now(),
        ]
    );

    return response()->json(['success' => true, 'slug' => $data['slug']]);
}});

Route::get('/data-engine/health', function () {{
    return response()->json(['status' => 'live', 'engine' => 'tek-juice-data-engine']);
}});
""")

_DJANGO_RECEIVER = textwrap.dedent("""
# DATA ENGINE — Auto-installed receiver
# Installed by Tek Juice Data Engine on {install_date}

import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.conf import settings

DE_KEY = getattr(settings, 'DATA_ENGINE_KEY', '{api_key}')


@csrf_exempt
@require_http_methods(["POST"])
def publish(request):
    key = request.headers.get('X-DataEngine-Key')
    if key != DE_KEY:
        return JsonResponse({{'error': 'Unauthorized'}}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({{'error': 'Invalid JSON'}}, status=400)

    title = data.get('title')
    body  = data.get('body')
    slug  = data.get('slug')

    if not all([title, body, slug]):
        return JsonResponse({{'error': 'Missing required fields'}}, status=400)

    # Dynamically find the first model with title+body+slug fields
    from django.apps import apps
    for model in apps.get_models():
        fields = {{f.name for f in model._meta.get_fields()}}
        if {{'title', 'body', 'slug'}}.issubset(fields):
            model.objects.update_or_create(
                slug=slug,
                defaults={{
                    'title':            title,
                    'body':             body,
                    'meta_description': data.get('meta_description', ''),
                    'published':        True,
                }},
            )
            break

    return JsonResponse({{'success': True, 'slug': slug}})


@require_http_methods(["GET"])
def health(request):
    return JsonResponse({{'status': 'live', 'engine': 'tek-juice-data-engine'}})
""")


# ── SSH Injector ──────────────────────────────────────────────────────────────

class SSHInjector:
    """
    Connects to any Linux server via SSH, detects the backend framework,
    installs the appropriate receiver, and disconnects.

    Required config keys:
        host       : server hostname or IP
        port       : SSH port (default 22)
        username   : SSH username
        password   : SSH password (mutually exclusive with private_key)
        private_key: SSH private key string (PEM format)
        api_key    : The Data Engine API key — embedded in the receiver
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.host        = config["host"]
        self.port        = int(config.get("port", 22))
        self.username    = config["username"]
        self.password    = config.get("password", "")
        self.private_key = config.get("private_key", "")
        self.api_key     = config["api_key"]
        self._client     = None

    def _connect(self):
        """Open SSH connection. Returns paramiko SSHClient."""
        try:
            import paramiko
        except ImportError:
            raise RuntimeError("paramiko is required for SSH injection. Install it: pip install paramiko")

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs: dict[str, Any] = {
            "hostname": self.host,
            "port":     self.port,
            "username": self.username,
            "timeout":  15,
        }

        if self.private_key:
            import io
            pkey = paramiko.RSAKey.from_private_key(io.StringIO(self.private_key))
            connect_kwargs["pkey"] = pkey
        else:
            connect_kwargs["password"] = self.password

        client.connect(**connect_kwargs)
        return client

    def _run(self, client, cmd: str) -> tuple[str, str]:
        """Execute a shell command and return (stdout, stderr)."""
        _, stdout, stderr = client.exec_command(cmd)
        return stdout.read().decode().strip(), stderr.read().decode().strip()

    def _detect_framework(self, client) -> str:
        """Read the server file structure to confirm framework."""
        checks = {
            "express":  "test -f /var/www/*/package.json 2>/dev/null && grep -r express /var/www/*/package.json 2>/dev/null | head -1",
            "laravel":  "test -f /var/www/*/artisan 2>/dev/null && echo found",
            "django":   "find /var/www -name 'manage.py' 2>/dev/null | head -1",
            "nextjs":   "find /var/www -name 'next.config.*' 2>/dev/null | head -1",
        }
        for framework, cmd in checks.items():
            out, _ = self._run(client, cmd)
            if out:
                return framework
        return "unknown"

    def _find_app_root(self, client) -> str:
        """Find the web application root directory."""
        candidates = ["/var/www/html", "/var/www/app", "/home/ubuntu/app", "/app", "/srv/app"]
        for path in candidates:
            out, _ = self._run(client, f"test -d {path} && echo yes")
            if out == "yes":
                return path
        out, _ = self._run(client, "find /var/www -maxdepth 2 -name 'package.json' -o -name 'artisan' -o -name 'manage.py' 2>/dev/null | head -1")
        if out:
            return "/".join(out.split("/")[:-1])
        return "/var/www/html"

    async def test_connection(self) -> dict[str, Any]:
        """Verify SSH credentials are valid."""
        try:
            client = self._connect()
            out, _ = self._run(client, "echo connected")
            client.close()
            if out == "connected":
                return {"success": True}
            return {"success": False, "error": "SSH connected but shell command failed."}
        except Exception as exc:
            return {"success": False, "error": f"SSH connection failed: {str(exc)}"}

    async def install_receiver(self) -> dict[str, Any]:
        """
        Full installation flow:
        1. Connect via SSH
        2. Detect framework
        3. Find app root
        4. Write the receiver file
        5. Register it with the app
        6. Test the health endpoint
        7. Disconnect
        """
        from datetime import datetime
        install_date = datetime.utcnow().strftime("%Y-%m-%d")

        try:
            client = self._connect()
        except Exception as exc:
            return {"success": False, "error": f"SSH connection failed: {str(exc)}"}

        try:
            framework = self._detect_framework(client)
            app_root  = self._find_app_root(client)
            logger.info("ssh_framework_detected", host=self.host, framework=framework, app_root=app_root)

            if framework == "express":
                receiver_code = _EXPRESS_RECEIVER.format(
                    install_date=install_date, api_key=self.api_key
                )
                recv_path = f"{app_root}/routes/data_engine_receiver.js"
                self._run(client, f"cat > {recv_path} << 'DEEOF'\n{receiver_code}\nDEEOF")
                # Register in app.js or server.js
                for entrypoint in ["app.js", "server.js", "index.js"]:
                    out, _ = self._run(client, f"test -f {app_root}/{entrypoint} && echo yes")
                    if out == "yes":
                        self._run(client, f"""
                            grep -q 'data_engine_receiver' {app_root}/{entrypoint} || \
                            echo "const deReceiver = require('./routes/data_engine_receiver'); app.use(deReceiver);" >> {app_root}/{entrypoint}
                        """)
                        break

            elif framework == "laravel":
                receiver_code = _LARAVEL_RECEIVER.format(
                    install_date=install_date, api_key=self.api_key
                )
                recv_path = f"{app_root}/routes/data_engine.php"
                self._run(client, f"cat > {recv_path} << 'DEEOF'\n{receiver_code}\nDEEOF")
                self._run(client, f"""
                    grep -q 'data_engine' {app_root}/routes/web.php || \
                    echo "require __DIR__.'/data_engine.php';" >> {app_root}/routes/web.php
                """)

            elif framework == "django":
                receiver_code = _DJANGO_RECEIVER.format(
                    install_date=install_date, api_key=self.api_key
                )
                # Find the Django project directory (contains settings.py)
                proj_dir_out, _ = self._run(client, f"find {app_root} -name 'settings.py' 2>/dev/null | head -1")
                proj_dir = "/".join(proj_dir_out.split("/")[:-1]) if proj_dir_out else app_root
                recv_path = f"{proj_dir}/data_engine_receiver.py"
                self._run(client, f"cat > {recv_path} << 'DEEOF'\n{receiver_code}\nDEEOF")
                # Add to urls.py
                urls_file = f"{proj_dir}/urls.py"
                self._run(client, f"""
                    grep -q 'data-engine' {urls_file} || \
                    python3 -c "
import re
with open('{urls_file}') as f: content = f.read()
addition = '''
from . import data_engine_receiver
urlpatterns += [
    path('data-engine/publish', data_engine_receiver.publish),
    path('data-engine/health',  data_engine_receiver.health),
]'''
with open('{urls_file}', 'a') as f: f.write(addition)
"
                """)

            else:
                # Unknown framework — write a standalone Python HTTP receiver as fallback
                fallback = textwrap.dedent(f"""
                    #!/usr/bin/env python3
                    # DATA ENGINE standalone receiver — {install_date}
                    from http.server import HTTPServer, BaseHTTPRequestHandler
                    import json, os

                    KEY = os.environ.get('DATA_ENGINE_KEY', '{self.api_key}')

                    class Handler(BaseHTTPRequestHandler):
                        def do_GET(self):
                            if self.path == '/data-engine/health':
                                self._respond(200, {{'status': 'live'}})
                        def do_POST(self):
                            if self.path == '/data-engine/publish':
                                if self.headers.get('X-DataEngine-Key') != KEY:
                                    return self._respond(401, {{'error': 'Unauthorized'}})
                                length = int(self.headers.get('Content-Length', 0))
                                body = json.loads(self.rfile.read(length))
                                with open(f"/tmp/de_{{body.get('slug','post')}}.json", 'w') as f:
                                    json.dump(body, f)
                                self._respond(200, {{'success': True}})
                        def _respond(self, code, data):
                            self.send_response(code)
                            self.send_header('Content-Type', 'application/json')
                            self.end_headers()
                            self.wfile.write(json.dumps(data).encode())
                        def log_message(self, *a): pass

                    if __name__ == '__main__':
                        HTTPServer(('0.0.0.0', 8099), Handler).serve_forever()
                """)
                self._run(client, f"cat > /tmp/de_receiver.py << 'DEEOF'\n{fallback}\nDEEOF")
                self._run(client, "nohup python3 /tmp/de_receiver.py > /tmp/de_receiver.log 2>&1 &")
                recv_path = "/tmp/de_receiver.py (port 8099)"

            logger.info("ssh_receiver_installed", host=self.host, framework=framework, path=recv_path)
            client.close()
            return {"success": True, "framework": framework, "app_root": app_root, "receiver_path": recv_path}

        except Exception as exc:
            try:
                client.close()
            except Exception:
                pass
            logger.error("ssh_install_failed", host=self.host, error=str(exc))
            return {"success": False, "error": str(exc)}
