"""Interfaz web minima (una sola pagina, sin dependencias externas).

Se sirve desde la misma app FastAPI en `/`. No usa CDN ni frameworks: funciona offline
y sirve como demo visual del copiloto.
"""

INDEX_HTML = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WMS Data Copilot</title>
<style>
  :root {
    --bg: #0d1117; --panel: #161b22; --border: #30363d; --text: #e6edf3;
    --muted: #8b949e; --accent: #58a6ff; --ok: #3fb950; --warn: #d29922;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 32px 20px 64px; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }
  main { max-width: 900px; margin: 0 auto; }
  header { margin-bottom: 24px; }
  h1 { margin: 0 0 6px; font-size: 26px; letter-spacing: -0.02em; }
  h1 span { color: var(--accent); }
  .sub { color: var(--muted); font-size: 14px; margin: 0; }
  .badges { margin-top: 12px; display: flex; gap: 8px; flex-wrap: wrap; font-size: 12px; }
  .badge {
    background: var(--panel); border: 1px solid var(--border); border-radius: 999px;
    padding: 3px 10px; color: var(--muted); font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  form { display: flex; gap: 8px; margin: 20px 0 12px; }
  input[type=text] {
    flex: 1; padding: 13px 14px; border-radius: 8px; border: 1px solid var(--border);
    background: var(--panel); color: var(--text); font-size: 15px;
  }
  input[type=text]:focus { outline: none; border-color: var(--accent); }
  button {
    padding: 13px 20px; border-radius: 8px; border: 1px solid #1f6feb; background: #1f6feb;
    color: #fff; font-size: 15px; font-weight: 600; cursor: pointer;
  }
  button:disabled { opacity: 0.55; cursor: progress; }
  .chips { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 24px; }
  .chip {
    background: transparent; border: 1px solid var(--border); color: var(--muted);
    border-radius: 999px; padding: 6px 12px; font-size: 12.5px; cursor: pointer;
  }
  .chip:hover { border-color: var(--accent); color: var(--text); }
  .card {
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 18px 20px; margin-bottom: 16px;
  }
  .card h2 {
    margin: 0 0 12px; font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em;
    color: var(--muted); font-weight: 600;
  }
  pre {
    margin: 0; white-space: pre-wrap; word-break: break-word;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 13px; line-height: 1.6;
  }
  .answer { font-family: inherit; font-size: 14.5px; line-height: 1.65; }
  .meta { color: var(--muted); font-size: 12px; margin-top: 10px; font-family: ui-monospace, monospace; }
  .intent { color: var(--accent); font-family: ui-monospace, monospace; font-size: 12px; }
  .err { border-color: #f85149; color: #ffb3ae; }
  .hidden { display: none; }
  .sources li { font-size: 13px; color: var(--muted); margin-bottom: 4px; }
  ul { padding-left: 18px; margin: 0; }
</style>
</head>
<body>
<main>
  <header>
    <h1>WMS <span>Data Copilot</span></h1>
    <p class="sub">Pregunta en lenguaje natural sobre inventario, documentación operativa o calidad de datos.</p>
    <div class="badges" id="badges"><span class="badge">cargando estado…</span></div>
  </header>

  <form id="form">
    <input id="q" type="text" autocomplete="off" placeholder="Ej: ¿Qué bodegas tienen saldo negativo?" autofocus>
    <button id="send" type="submit">Preguntar</button>
  </form>

  <div class="chips" id="chips"></div>

  <section id="out" class="hidden">
    <div class="card">
      <h2>Respuesta</h2>
      <pre class="answer" id="answer"></pre>
      <div class="meta" id="meta"></div>
    </div>
    <div class="card hidden" id="sqlcard">
      <h2>SQL ejecutado</h2>
      <pre id="sql"></pre>
    </div>
    <div class="card hidden" id="srccard">
      <h2>Fuentes</h2>
      <ul class="sources" id="sources"></ul>
    </div>
    <div class="card hidden" id="findcard">
      <h2>Hallazgos de calidad</h2>
      <ul class="sources" id="findings"></ul>
    </div>
  </section>
</main>

<script>
const EXAMPLES = [
  "¿Qué bodegas tienen saldo negativo?",
  "¿Cuántos movimientos hay por tipo?",
  "¿Cuáles son los productos con más salidas?",
  "¿Qué significa el campo received_delta?",
  "¿Qué hago si la carga diaria falla por un duplicado?",
  "Haz una auditoría de calidad de datos"
];

const $ = (id) => document.getElementById(id);
const show = (id, on) => $(id).classList.toggle("hidden", !on);

function chips() {
  const box = $("chips");
  EXAMPLES.forEach((text) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip";
    b.textContent = text;
    b.onclick = () => { $("q").value = text; $("form").requestSubmit(); };
    box.appendChild(b);
  });
}

async function health() {
  try {
    const r = await fetch("/health");
    const d = await r.json();
    $("badges").innerHTML = "";
    const add = (t) => {
      const s = document.createElement("span");
      s.className = "badge";
      s.textContent = t;
      $("badges").appendChild(s);
    };
    add(`db: ${d.database}`);
    add(`llm: ${d.llm.provider} · ${d.llm.model}`);
    add(`${d.rows.inventory_movements ?? 0} movimientos`);
    add(`${d.kb.chunks} chunks`);
  } catch (e) {
    $("badges").innerHTML = '<span class="badge">estado no disponible</span>';
  }
}

function render(d) {
  show("out", true);
  $("answer").textContent = d.answer || "(sin respuesta)";
  const bits = [`intent=${d.intent}`, `provider=${d.provider}`, `${d.latency_ms} ms`];
  if (d.cost_usd) bits.push(`US$${Number(d.cost_usd).toFixed(6)}`);
  if (d.request_id) bits.push(`req=${d.request_id}`);
  $("meta").textContent = bits.join(" · ");
  $("meta").classList.remove("err");
  if (d.error) { $("meta").classList.add("err"); $("meta").textContent += ` · error: ${d.error}`; }

  show("sqlcard", Boolean(d.sql));
  if (d.sql) $("sql").textContent = d.sql;

  show("srccard", (d.citations || []).length > 0);
  $("sources").innerHTML = "";
  (d.citations || []).forEach((c) => {
    const li = document.createElement("li");
    li.textContent = `${c.ref} — ${c.title} (score ${c.score})`;
    $("sources").appendChild(li);
  });

  show("findcard", (d.findings || []).length > 0);
  $("findings").innerHTML = "";
  (d.findings || []).forEach((f) => {
    if (!f.count) return;
    const li = document.createElement("li");
    li.textContent = `${f.rule_code} [${f.severity}] ${f.count} · ${f.description}`;
    $("findings").appendChild(li);
  });
}

async function ask(question) {
  const btn = $("send");
  btn.disabled = true;
  btn.textContent = "Consultando…";
  try {
    const r = await fetch("/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question })
    });
    const d = await r.json();
    if (!r.ok) {
      show("out", true);
      $("answer").textContent = `Error ${r.status}: ${d.detail || "solicitud inválida"}`;
      $("meta").textContent = "";
      show("sqlcard", false); show("srccard", false); show("findcard", false);
      return;
    }
    render(d);
  } catch (e) {
    show("out", true);
    $("answer").textContent = "No se pudo contactar la API. ¿Está corriendo el servidor?";
  } finally {
    btn.disabled = false;
    btn.textContent = "Preguntar";
  }
}

$("form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const q = $("q").value.trim();
  if (q.length >= 3) ask(q);
});

chips();
health();
</script>
</body>
</html>
"""
