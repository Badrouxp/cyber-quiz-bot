"""Web app version of the test bot — opens as a Telegram WebApp (mini app).

Routes:
  GET  /                       — subject list (Topshiriqlar ro'yxati)
  GET  /test/{stem}            — 30 random questions on one page
  POST /api/submit             — receive answers, save attempt, return redirect
  GET  /result/{attempt_id}    — final score page
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import random
from typing import Optional
from urllib.parse import parse_qsl

from aiohttp import web

import database as db
from config import BOT_TOKEN, SUBJECTS_DIR
from parser import list_subjects, load_subject

log = logging.getLogger(__name__)

QUESTIONS_PER_TEST = 30
TEST_DURATION_MIN = 30

# In-memory store: test_id -> {stem, display_name, questions: [{n, original_number, correct}]}
_active_tests: dict[str, dict] = {}


def verify_init_data(init_data: str) -> Optional[dict]:
    """Verify Telegram WebApp initData; return user dict on success."""
    if not init_data:
        return None
    try:
        parsed = dict(parse_qsl(init_data, strict_parsing=False))
    except Exception:
        return None
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return None
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(computed, received_hash):
        return None
    user_json = parsed.get("user")
    if not user_json:
        return None
    try:
        return json.loads(user_json)
    except json.JSONDecodeError:
        return None


def _esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _ball_5(pct: float) -> int:
    if pct >= 90:
        return 5
    if pct >= 75:
        return 4
    if pct >= 60:
        return 3
    return 2


# ─── pages ───

async def handle_index(request: web.Request) -> web.Response:
    subjects = list_subjects()
    if not subjects:
        body = '<p class="text-muted">Hozircha fanlar yo\'q.</p>'
    else:
        items = []
        for name, path in subjects:
            try:
                count = len(load_subject(path))
            except Exception:
                count = 0
            items.append(
                f'<a href="/test/{path.stem}" class="list-group-item list-group-item-action">'
                f'<div class="d-flex justify-content-between align-items-center">'
                f'<div><h5 class="mb-1">📘 {_esc(name)}</h5>'
                f'<small class="text-muted">{count} savoldan {QUESTIONS_PER_TEST} tasi · {TEST_DURATION_MIN} daqiqa</small></div>'
                f'<span class="badge bg-primary rounded-pill">Boshlash →</span>'
                f'</div></a>'
            )
        body = '<div class="list-group">' + "\n".join(items) + "</div>"
    return web.Response(text=INDEX_HTML.replace("{{body}}", body), content_type="text/html")


async def handle_test_page(request: web.Request) -> web.Response:
    stem = request.match_info["stem"]
    path = SUBJECTS_DIR / f"{stem}.txt"
    if not path.exists():
        return web.Response(text="Fan topilmadi", status=404)
    all_q = load_subject(path)
    if not all_q:
        return web.Response(text="Bu fanda savollar yo'q.", status=404)

    if len(all_q) <= QUESTIONS_PER_TEST:
        selected = list(all_q)
        random.shuffle(selected)
    else:
        selected = random.sample(all_q, QUESTIONS_PER_TEST)

    display_name = stem.replace("_", " ").replace("-", " ").title()
    test_id = f"{stem}-{random.randint(10**9, 10**10 - 1)}"

    cards = []
    qmeta = []
    for i, q in enumerate(selected, 1):
        opts = list(q.options)
        random.shuffle(opts)
        opt_html = []
        for letter, text in opts:
            opt_html.append(
                f'<label class="option d-flex align-items-start p-2 rounded mb-1">'
                f'<input type="radio" name="q{i}" value="{letter}" class="form-check-input me-2 mt-1">'
                f'<span>{_esc(text)}</span></label>'
            )
        cards.append(
            f'<div class="card mb-3 question-card" data-qnum="{i}">'
            f'<div class="card-body">'
            f'<h6 class="card-title mb-3">{i}. {_esc(q.text)}</h6>'
            f'<div class="options">{"".join(opt_html)}</div>'
            f'</div></div>'
        )
        qmeta.append({"n": i, "original_number": q.number, "correct": q.correct})

    _active_tests[test_id] = {
        "stem": stem,
        "display_name": display_name,
        "questions": qmeta,
    }

    html = (
        TEST_HTML
        .replace("{{subject_name}}", _esc(display_name))
        .replace("{{stem}}", stem)
        .replace("{{test_id}}", test_id)
        .replace("{{duration_sec}}", str(TEST_DURATION_MIN * 60))
        .replace("{{questions}}", "\n".join(cards))
        .replace("{{total}}", str(len(selected)))
    )
    return web.Response(text=html, content_type="text/html")


async def handle_submit(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Noto'g'ri so'rov"}, status=400)
    test_id = data.get("test_id")
    answers = data.get("answers") or {}
    init_data = data.get("init_data") or ""

    test = _active_tests.get(test_id)
    if not test:
        return web.json_response({"error": "Test muddati tugagan yoki topilmadi"}, status=400)

    user = verify_init_data(init_data)
    if user:
        uid = int(user["id"])
        db.upsert_user(
            uid,
            user.get("username") or "",
            user.get("first_name") or "",
            user.get("last_name") or "",
        )
    else:
        uid = 0  # anonymous

    attempt_id = db.start_attempt(uid, test["display_name"], len(test["questions"]))
    for q in test["questions"]:
        chosen = answers.get(str(q["n"]))
        db.record_answer(attempt_id, q["original_number"], chosen, q["correct"])
    db.finish_attempt(attempt_id)

    _active_tests.pop(test_id, None)
    return web.json_response({"redirect": f"/result/{attempt_id}"})


async def handle_result(request: web.Request) -> web.Response:
    try:
        attempt_id = int(request.match_info["attempt_id"])
    except ValueError:
        return web.Response(text="Noto'g'ri ID", status=400)
    a = db.get_attempt(attempt_id)
    if not a:
        return web.Response(text="Natija topilmadi", status=404)

    full_name = "Guest"
    user = db.get_user(a["user_id"]) if a["user_id"] else None
    if user:
        full_name = " ".join(filter(None, [user["first_name"], user["last_name"]])) or "Guest"

    user_attempts = db.get_user_attempts(a["user_id"], limit=1000) if a["user_id"] else []
    attempts_count = sum(1 for x in user_attempts if x["subject"] == a["subject"])

    pct = (a["correct"] / a["total"] * 100) if a["total"] else 0
    ball = _ball_5(pct)
    stem = a["subject"].lower().replace(" ", "_").replace("'", "")

    html = (
        RESULT_HTML
        .replace("{{full_name}}", _esc(full_name.upper()))
        .replace("{{subject}}", _esc(a["subject"]))
        .replace("{{attempts_count}}", str(attempts_count))
        .replace("{{total}}", str(a["total"]))
        .replace("{{correct}}", str(a["correct"]))
        .replace("{{pct}}", f"{pct:.1f}")
        .replace("{{ball}}", str(ball))
        .replace("{{stem}}", stem)
    )
    return web.Response(text=html, content_type="text/html")


# ─── HTML ───

_BASE_HEAD = """<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{title}}</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
  body { background:#f5f7fa; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; padding-bottom: 80px; }
  .container { max-width: 900px; }
  .question-card { border: 1px solid #e3e6ea; box-shadow: 0 1px 2px rgba(0,0,0,.04); }
  .question-card .card-title { font-weight: 500; color: #2c3e50; white-space: pre-wrap; }
  .option { cursor: pointer; transition: background .15s; border: 1px solid transparent; }
  .option:hover { background: #f0f3f7; }
  .option.selected { background: #fdf6e3; border-color: #e0d49a; }
  .timer-bar { position: sticky; top: 0; z-index: 100; background: #fff; border-bottom: 1px solid #e3e6ea; padding: 12px 16px; }
  .timer { font-weight: 600; font-size: 1.1rem; color: #2c3e50; }
  .timer.warn { color: #d9534f; }
  .submit-bar { position: fixed; bottom: 0; left: 0; right: 0; background: #fff; border-top: 1px solid #e3e6ea; padding: 12px; z-index: 100; }
  .result-table th { background: #f5f7fa; font-weight: 500; width: 35%; }
  .result-table td, .result-table th { padding: 12px 14px; vertical-align: middle; }
</style>
</head>
<body>
"""

INDEX_HTML = (
    _BASE_HEAD.replace("{{title}}", "Topshiriqlar")
    + """<div class="container py-4">
<h3 class="mb-4">📚 Topshiriqlar ro'yxati</h3>
{{body}}
</div>
</body></html>"""
)

TEST_HTML = (
    _BASE_HEAD.replace("{{title}}", "Test")
    + """<div class="timer-bar">
<div class="container d-flex justify-content-between align-items-center">
  <div><strong>{{subject_name}}</strong> · {{total}} savol</div>
  <div class="timer" id="timer">⏱ —:—</div>
</div>
</div>
<div class="container py-4">
<form id="test-form">
{{questions}}
</form>
</div>
<div class="submit-bar">
<div class="container d-flex justify-content-between align-items-center">
  <div class="text-muted small" id="progress">0 / {{total}} javob berildi</div>
  <button type="button" class="btn btn-primary" id="submit-btn">Topshirish</button>
</div>
</div>
<script>
(function(){
  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg) { tg.ready(); tg.expand(); }
  const total = {{total}};
  let remaining = {{duration_sec}};
  const timer = document.getElementById('timer');
  const progress = document.getElementById('progress');

  function fmt(s){
    const m = Math.floor(s/60).toString().padStart(2,'0');
    const ss = (s%60).toString().padStart(2,'0');
    return m + ':' + ss;
  }
  function tick(){
    if (remaining <= 0) { submit(true); return; }
    timer.textContent = '⏱ ' + fmt(remaining);
    if (remaining <= 60) timer.classList.add('warn');
    remaining--;
  }
  tick();
  const interval = setInterval(tick, 1000);

  // option click -> highlight
  document.querySelectorAll('.question-card').forEach(card => {
    card.querySelectorAll('input[type=radio]').forEach(inp => {
      inp.addEventListener('change', () => {
        card.querySelectorAll('.option').forEach(o => o.classList.remove('selected'));
        inp.closest('.option').classList.add('selected');
        updateProgress();
      });
    });
  });
  function updateProgress(){
    let answered = 0;
    for (let i=1; i<=total; i++) {
      if (document.querySelector('input[name="q'+i+'"]:checked')) answered++;
    }
    progress.textContent = answered + ' / ' + total + ' javob berildi';
  }

  let submitting = false;
  async function submit(forced){
    if (submitting) return;
    if (!forced) {
      let answered = 0;
      for (let i=1; i<=total; i++) {
        if (document.querySelector('input[name="q'+i+'"]:checked')) answered++;
      }
      if (answered < total) {
        if (!confirm((total-answered) + ' ta savolga javob bermadingiz. Topshirilsinmi?')) return;
      }
    }
    submitting = true;
    clearInterval(interval);
    const answers = {};
    for (let i=1; i<=total; i++) {
      const v = document.querySelector('input[name="q'+i+'"]:checked');
      if (v) answers[i] = v.value;
    }
    const payload = {
      test_id: "{{test_id}}",
      answers: answers,
      init_data: (tg && tg.initData) || ""
    };
    try {
      const resp = await fetch('/api/submit', {
        method:'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify(payload)
      });
      const data = await resp.json();
      if (data.redirect) { window.location.href = data.redirect; }
      else { alert(data.error || 'Xato'); submitting = false; }
    } catch(e) {
      alert('Tarmoq xatosi: ' + e);
      submitting = false;
    }
  }
  document.getElementById('submit-btn').addEventListener('click', () => submit(false));
})();
</script>
</body></html>"""
)

RESULT_HTML = (
    _BASE_HEAD.replace("{{title}}", "Test natijasi")
    + """<div class="container py-4" style="max-width: 600px;">
<div class="card">
  <div class="card-header bg-white">
    <h5 class="mb-0">Test natijasi</h5>
  </div>
  <div class="card-body p-0">
    <table class="table mb-0 result-table">
      <tbody>
        <tr><th>Talaba</th><td>{{full_name}}</td></tr>
        <tr><th>Fanlar</th><td>{{subject}}</td></tr>
        <tr><th>Topshiriq nomi</th><td>YAKUNIY TEST</td></tr>
        <tr><th>Urinishlar</th><td>{{attempts_count}}</td></tr>
        <tr><th>Savollar soni</th><td>{{total}}</td></tr>
        <tr><th>Ball</th><td>{{ball}}</td></tr>
        <tr><th>Natija</th><td><strong>{{correct}} / {{pct}}%</strong></td></tr>
      </tbody>
    </table>
  </div>
  <div class="card-footer bg-white d-flex justify-content-between">
    <a href="/" class="btn btn-outline-secondary">‹ Topshiriqlar ro'yxatiga qaytish</a>
    <a href="/test/{{stem}}" class="btn btn-primary">Qayta topshirish</a>
  </div>
</div>
</div>
<script>
const tg = window.Telegram && window.Telegram.WebApp;
if (tg) { tg.ready(); tg.expand(); }
</script>
</body></html>"""
)


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/test/{stem}", handle_test_page)
    app.router.add_post("/api/submit", handle_submit)
    app.router.add_get("/result/{attempt_id}", handle_result)
    return app


async def start_webapp(host: str = "0.0.0.0", port: int = 8080) -> web.AppRunner:
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("WebApp listening on http://%s:%s", host, port)
    return runner
