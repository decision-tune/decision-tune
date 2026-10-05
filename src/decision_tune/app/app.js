"use strict";
// DecisionTune local app. All data goes to the DOM with textContent / setAttribute, never innerHTML.
const $ = (id) => document.getElementById(id);
const NAME_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const TYPES = [["choose", "Pick one"], ["yes_no", "Yes or no"], ["scale", "Scale"]];
const MAX_ROWS_SHOWN = 500;
const PORT = location.port || "80";

function h(tag, props, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v === true) e.setAttribute(k, "");
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c != null) e.append(c);
  return e;
}

async function api(path, body) {
  let r;
  try {
    r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  } catch (e) {
    throw new Error("Cannot reach DecisionTune. Is it still running?");
  }
  if (!r.ok) {
    let msg = "Something went wrong (" + r.status + ").";
    try { msg = (await r.json()).error || msg; } catch (e) { /* keep the fixed message */ }
    throw new Error(msg);
  }
  return r;
}
const getJSON = async (path) => (await api(path)).json();
const postJSON = async (path, body) => (await api(path, body)).json();

function copyButton(getText) {
  const b = h("button", { type: "button", class: "copy" }, "Copy");
  b.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(getText()); b.textContent = "Copied"; } catch (e) { b.textContent = "Press Cmd+C"; }
    setTimeout(() => { b.textContent = "Copy"; }, 1400);
  });
  return b;
}
const codeBlock = (text) => h("div", { class: "code" }, h("pre", null, text), copyButton(() => text));
const pct = (v) => (v * 100).toFixed(1) + "%";
const label = (key, desc) => (typeof desc === "string" && desc.includes(":") ? desc.split(":")[0].trim() : key);

// ---- status and tabs
getJSON("/api/status").then((s) => {
  const el = $("status");
  el.replaceChildren(h("span", { class: "on" }, "Running on this computer"), h("span", null, s.backend), h("span", null, "Offline OK"));
}).catch(() => { $("status").replaceChildren(h("span", null, "Not connected")); });

function showTab(id) {
  document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.p === id)));
  document.querySelectorAll(".panel").forEach((p) => { p.hidden = p.id !== id; });
  if (id === "recipes" || id === "run") refreshRecipes();
}
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => { showTab(b.dataset.p); history.replaceState(null, "", "#tab-" + b.dataset.p); }));

// ---- 1. Try it
const EX = [
  { s: "The order arrived broken.", q: "Which team should handle this customer message?", mode: "choose",
    o: ["Billing: charges, refunds, invoices", "Shipping: delivery, lost or damaged packages", "Tech support: bugs, crashes, login problems"] },
  { s: "The order arrived broken. I want my money back.", q: "Is the customer asking for a refund?", mode: "yes", o: [] },
  { s: "What is the weather tomorrow in Paris?", q: "Which tool should be called?", mode: "choose",
    o: ["get_weather: forecast for a city and date", "send_email: send an email to a contact", "create_calendar_event: add an event to the calendar"] },
];
const mode = () => document.querySelector("input[name=mode]:checked").value;
const optValues = () => [...document.querySelectorAll("#opts input")].map((i) => i.value.trim()).filter(Boolean);

function addOpt(v) {
  const i = h("input", { type: "text", "aria-label": "Option" });
  i.value = v || "";
  const row = h("div", { class: "opt" }, i, h("button", { type: "button", "aria-label": "Remove option", onclick: () => row.remove() }, "×"));
  $("opts").append(row);
}
function setMode(m) {
  document.querySelector(`input[name=mode][value=${m}]`).checked = true;
  $("optbox").hidden = m === "yes";
}
function loadExample(i) {
  const e = EX[i];
  $("st").value = e.s;
  $("qu").value = e.q;
  setMode(e.mode);
  $("opts").replaceChildren();
  (e.o.length ? e.o : ["", ""]).forEach(addOpt);
}
document.querySelectorAll(".ex button").forEach((b) => b.addEventListener("click", () => loadExample(+b.dataset.e)));
document.querySelectorAll("input[name=mode]").forEach((r) => r.addEventListener("change", () => setMode(mode())));
$("addopt").addEventListener("click", () => addOpt(""));

let snippet = "";
$("copycode").replaceWith(copyButton(() => snippet));

async function decide() {
  const body = { state: $("st").value, question: $("qu").value.trim() };
  const yes = mode() === "yes";
  if (!body.question) return fail("Write a question first.");
  if (!yes) {
    body.options = optValues();
    if (body.options.length < 2) return fail("Add at least two options.");
  }
  $("decide").disabled = true;
  try {
    const out = await postJSON("/decide", body);
    const names = yes ? ["Yes", "No"] : body.options;
    const probs = yes ? [out.p_yes, 1 - out.p_yes] : body.options.map((o) => out.probabilities[o]);
    const top = yes ? (out.answer === "yes" ? 0 : 1) : body.options.indexOf(out.choice);
    $("meta").className = "meta";
    $("meta").textContent = "Decision · " + out.ms + " ms · on this computer";
    $("win").textContent = names[top].split(":")[0];
    $("rows").replaceChildren(...names.map((n, k) => {
      const bar = h("b");
      bar.style.width = (probs[k] * 100) + "%";
      return h("div", { class: "row" + (k === top ? " w" : "") }, h("span", null, n.split(":")[0]), h("i", null, bar), h("span", { class: "n" }, pct(probs[k])));
    }));
    snippet = yes
      ? `m.yes_no(${JSON.stringify(body.state)},\n         ${JSON.stringify(body.question)})`
      : `m.choose(${JSON.stringify(body.state)},\n         ${JSON.stringify(body.question)},\n         ${JSON.stringify(body.options)})`;
    $("code").textContent = snippet;
    $("codebox").hidden = false;
  } catch (e) { fail(e.message); }
  $("decide").disabled = false;
}
function fail(msg) {
  $("meta").className = "meta err";
  $("meta").textContent = msg;
  $("win").textContent = "";
  $("rows").replaceChildren();
  $("codebox").hidden = true;
  $("decide").disabled = false;
}
$("decide").addEventListener("click", decide);
loadExample(0);

// ---- 2. Run on many items
let src = null;          // {input, label, preview}
let wantedRead = null;   // the loaded recipe's read, exactly as stored: null = no recipe loaded, [] = all columns (as the recipe format says)
let colsEdited = false;  // the person changed the column boxes: from then on read follows the boxes, not the loaded recipe
let savedName = "";
let savedKey = "";       // the definition (read, questions, threshold) as it was last loaded or saved
let last = null;         // {out, recipe}
let filter = "all";
let srcToken = 0;

function srcInfo(msg, bad) {
  $("srcinfo").textContent = msg;
  $("srcinfo").className = "file" + (bad ? " bad" : "");
}
function renderCols(skip) {
  const box = $("cols");
  if (!src) { box.replaceChildren(h("span", { class: "soft" }, "Add your items first.")); return; }
  const p = src.preview;
  const keep = wantedRead && !colsEdited;  // show the loaded recipe's own columns; never guess for it
  box.replaceChildren(...p.columns.map((c) => {
    const on = keep ? !wantedRead.length || wantedRead.includes(c) : p.text.includes(c) && !(skip || []).includes(c);
    const cb = h("input", { type: "checkbox", value: c });
    cb.checked = on;
    return h("label", null, cb, c);
  }));
  renderUse();
}
const checkedCols = () => [...document.querySelectorAll("#cols input:checked")].map((i) => i.value);

// Forget the current items at once, cancel any preview still in flight, and keep Run off until a new source is ready.
function dropSource() {
  ++srcToken;
  clearTimeout(pasteTimer);  // a pasted list still waiting to be previewed is an older source too
  src = null;
  renderCols();
  updateRun();
}
// Run needs items, and items that have every column the loaded recipe reads. Otherwise say why, inline, and keep Run off.
let shownProblem = false;
function readProblem() {
  if (!src || !wantedRead || colsEdited) return "";
  const have = src.preview.columns;
  return wantedRead.every((c) => have.includes(c)) ? "" : `This recipe reads ${wantedRead.join(", ")}. Your file has: ${have.join(", ") || "no columns"}.`;
}
function updateRun() {
  const bad = readProblem();
  $("runbtn").disabled = !src || !!bad;
  if (bad || shownProblem) {
    $("runmsg").className = "msg-line" + (bad ? " bad" : "");
    $("runmsg").textContent = bad;
  }
  shownProblem = !!bad;
}
async function setSource(input, name, skip) {
  dropSource();
  const token = srcToken;
  srcInfo("Reading " + name + "...");
  try {
    const preview = await postJSON("/api/preview", { input });
    if (token !== srcToken) return;
    src = { input, label: name, preview, skip };
    renderCols(skip);
    updateRun();
    srcInfo(`${name} · ${preview.count} rows · stays on this computer`);
  } catch (e) {
    if (token !== srcToken) return;
    srcInfo(e.message, true);
  }
}

document.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => {
  document.querySelectorAll(".chip").forEach((x) => { x.classList.toggle("on", x === c); x.setAttribute("aria-pressed", String(x === c)); });
  for (const k of ["sheet", "folder", "paste"]) $("pane-" + k).hidden = k !== c.dataset.src;
}));

function toBase64(buf) {
  const b = new Uint8Array(buf);
  let s = "";
  for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000));
  return btoa(s);
}
$("f-sheet").addEventListener("change", async (ev) => {
  const f = ev.target.files[0];
  if (!f) return;
  dropSource();  // before the awaits: the old items must not stay runnable while the file is read
  const gen = srcToken;  // the source generation: a read that finishes after a newer source was chosen is dropped
  const ext = f.name.toLowerCase().split(".").pop();
  if (ext === "csv") { const data = await f.text(); if (gen === srcToken) setSource({ type: "csv", data }, f.name); }
  else if (ext === "xlsx") { const buf = await f.arrayBuffer(); if (gen === srcToken) setSource({ type: "xlsx", data: toBase64(buf) }, f.name); }
  else srcInfo("Use a .csv or .xlsx file.", true);
});
$("f-folder").addEventListener("change", async (ev) => {
  dropSource();
  const gen = srcToken;
  const files = [...ev.target.files].filter((f) => /\.(txt|md)$/i.test(f.name)).sort((a, b) => a.name.localeCompare(b.name));
  if (!files.length) { srcInfo("No .txt or .md files in that folder.", true); return; }
  const rows = await Promise.all(files.map(async (f) => ({ file: f.name, text: await f.text() })));
  if (gen === srcToken) setSource({ type: "rows", data: rows }, "Folder", ["file"]);
});
let pasteTimer = 0;
$("paste").addEventListener("input", () => {
  clearTimeout(pasteTimer);
  dropSource();  // every edit invalidates the items at once, including a preview that is still on its way
  const text = $("paste").value;
  if (!text.trim()) { srcInfo("No items yet. Files stay on this computer."); return; }
  pasteTimer = setTimeout(() => setSource({ type: "lines", data: text }, "Pasted list"), 300);
});

const SAMPLE = [
  ["Order #4417", "The order arrived broken. I want my money back."],
  ["Double charge", "I was charged twice this month. Please fix it today."],
  ["App crash", "The app crashes every time I open the settings page."],
  ["Where is my box", "My package says delivered but it is not here."],
  ["Thanks!", "Thanks, the replacement arrived and works great!"],
  ["Login", "I cannot log in after the update and I have a meeting in an hour."],
  ["Invoice copy", "Can you send me a copy of last month's invoice?"],
  ["Wrong size", "I got the wrong size. Can I exchange it?"],
];
$("sample").addEventListener("click", async () => {
  await setSource({ type: "rows", data: SAMPLE.map(([subject, message]) => ({ subject, message })) }, "Sample tickets");
  try { await loadRecipe("support-triage"); } catch (e) { /* no built-in recipe: the person writes questions */ }
});

// questions
// kind: "list" or "dict" when the question was loaded (kept as it was), "auto" for a new one (colons mean key: description).
function parseChoices(text, kind) {
  const lines = text.split("\n").map((s) => s.trim()).filter(Boolean);
  if (kind === "list" || (kind !== "dict" && !lines.some((l) => l.includes(":")))) return lines;
  const o = Object.create(null);  // a plain {} would drop a "__proto__" key
  for (const l of lines) {
    const i = l.indexOf(":");
    o[i < 0 ? l : l.slice(0, i).trim()] = i < 0 ? l : l.slice(i + 1).trim();
  }
  return o;
}
const choicesText = (v) => (Array.isArray(v) ? v.join("\n") : Object.entries(v || {}).map(([k, d]) => `${k}: ${d}`).join("\n"));

function addQuestion(q) {
  q = q || { name: "", type: "choose", question: "" };  // no options yet: "auto" parsing, so "key: description" lines make a dict
  const name = h("input", { type: "text", "aria-label": "Column name", placeholder: "column_name", autocomplete: "off", spellcheck: "false" });
  const type = h("select", { "aria-label": "Answer type" }, TYPES.map(([v, t]) => h("option", { value: v }, t)));
  const text = h("input", { type: "text", class: "qt", "aria-label": "Question", placeholder: "Which team should handle this?" });
  const area = h("textarea", { "aria-label": "Options", spellcheck: "false" });
  const lab = h("span", { class: "lab" });
  const choices = h("div", null, lab, area);
  const card = h("div", { class: "q" }, h("div", { class: "qh" }, name, type, h("button", { type: "button", "aria-label": "Remove question", onclick: () => card.remove() }, "×")), text, choices);
  const sync = () => {
    choices.hidden = type.value === "yes_no";
    lab.textContent = type.value === "scale" ? "Levels, one per line: key: description" : "Options, one per line: key: description";
    area.placeholder = type.value === "scale" ? "low: Low urgency\nhigh: High urgency" : "billing: Billing, charges and refunds\nshipping: Shipping and delivery";
  };
  type.addEventListener("change", sync);
  name.value = q.name;
  type.value = q.type;
  text.value = q.question;
  area.value = choicesText(q.type === "scale" ? q.levels : q.options);
  sync();
  const given = q.type === "scale" ? q.levels : q.options;
  card.q = { name, type, text, area, kind: Array.isArray(given) ? "list" : given ? "dict" : "auto" };
  $("qs").append(card);
}
$("addq").addEventListener("click", () => addQuestion());

function questions() {
  return [...document.querySelectorAll("#qs .q")].map((c) => {
    const { name, type, text, area, kind } = c.q;
    const q = { name: name.value.trim(), type: type.value, question: text.value.trim() };
    if (q.type === "choose") q.options = parseChoices(area.value, kind);
    if (q.type === "scale") q.levels = parseChoices(area.value, kind);
    return q;
  });
}
function buildRecipe(forSave) {
  const name = $("rname").value.trim();
  const thr = Number($("thr").value);
  const keep = wantedRead && !colsEdited;  // a loaded recipe keeps its read exactly, [] included
  const read = keep ? wantedRead : src ? checkedCols() : [];
  if (!forSave && !src) throw new Error("Add your items first (step 1).");
  if (src && !keep && !read.length) throw new Error("Check at least one column to read (step 2).");
  if (!(thr >= 0 && thr <= 100)) throw new Error("The review threshold must be from 0 to 100.");
  return { name: forSave ? name : (NAME_RE.test(name) ? name : "untitled"), read, questions: questions(), review_below: thr / 100 };
}

async function loadRecipe(name) {
  const r = await getJSON("/api/recipes/" + encodeURIComponent(name));
  $("qs").replaceChildren();
  r.questions.forEach(addQuestion);
  $("thr").value = +(r.review_below * 100).toPrecision(12);  // 0.605 shows as 60.5, not 61
  $("rname").value = r.name;
  wantedRead = Array.isArray(r.read) ? [...r.read] : [];
  colsEdited = false;
  savedName = r.name;
  if (src) renderCols(src.skip);
  updateRun();
  savedKey = currentKey();  // after the form is filled: this is the definition the saved file stands for
  $("pick").value = "";
  renderUse();
}

async function run() {
  const msg = $("runmsg");
  let recipe;
  try { recipe = buildRecipe(false); } catch (e) { msg.className = "msg-line bad"; msg.textContent = e.message; return; }
  msg.className = "msg-line";
  msg.textContent = "Running on this computer...";
  $("runbtn").disabled = true;
  try {
    const out = await postJSON("/api/run", { recipe, input: src.input });
    last = { out, recipe, cols: src.preview.columns };
    msg.textContent = "";
    renderResults();
  } catch (e) { msg.className = "msg-line bad"; msg.textContent = e.message; }
  updateRun();
}
$("runbtn").addEventListener("click", run);

function renderResults() {
  const { out, recipe } = last;
  const errors = (out.failed || []).length;  // from the server: a source column may also be called "error"
  $("noresults").hidden = true;
  $("results").hidden = false;
  $("sumtxt").replaceChildren(h("b", null, `${out.count} rows · ${(out.count - errors) * recipe.questions.length} decisions · ${Math.round(out.ms)} ms`), " on this computer");
  $("f-all").textContent = "All " + out.count;
  $("f-review").textContent = "Needs review " + out.needs_review;
  const shown = shownCols();
  $("thead").replaceChildren(h("tr", null, shown.map((c) => h("th", null, c)), recipe.questions.map((q) => h("th", null, q.name)), h("th")));
  renderBody();
  renderUse();
}
const shownCols = () => (last.recipe.read.length ? last.recipe.read : last.cols);  // read [] = every input column
function renderBody() {
  const { out, recipe } = last;
  const failed = new Set(out.failed || []);
  const labels = Object.create(null);
  for (const q of recipe.questions) {
    labels[q.name] = Object.create(null);
    const c = q.type === "choose" ? q.options : q.levels;
    if (c && !Array.isArray(c)) for (const [k, d] of Object.entries(c)) labels[q.name][k] = label(k, d);
  }
  const rows = out.rows.map((r, i) => [r, failed.has(i)]).filter(([r]) => filter === "all" || r.needs_review);
  const trs = rows.slice(0, MAX_ROWS_SHOWN).map(([r, bad]) => {
    const tds = shownCols().map((c) => h("td", { class: "msg" }, r[c] == null ? "" : String(r[c])));
    for (const q of recipe.questions) {
      if (bad) { tds.push(h("td", null, "-")); continue; }
      const conf = r[q.name + "_confidence"];
      const v = q.type === "yes_no" ? (r[q.name] === "yes" ? "Yes" : "No") : (labels[q.name][r[q.name]] || String(r[q.name]));
      tds.push(h("td", null, h("span", { class: "v" }, v), h("span", { class: "c" + (conf < recipe.review_below ? " low" : "") }, Math.round(conf * 100) + "% sure")));
    }
    const tag = bad ? h("span", { class: "flagtag", title: r.error }, "Error") : r.needs_review ? h("span", { class: "flagtag" }, "Check") : null;
    return h("tr", { class: r.needs_review ? "flag" : "" }, tds, h("td", null, tag));
  });
  $("tbody").replaceChildren(...trs);
  const note = $("capnote");
  note.hidden = rows.length <= MAX_ROWS_SHOWN;
  note.textContent = `Showing the first ${MAX_ROWS_SHOWN} of ${rows.length} rows. The downloads have every row.`;
}
function setFilter(f) {
  filter = f;
  $("f-all").classList.toggle("on", f === "all");
  $("f-review").classList.toggle("on", f === "review");
  $("f-all").setAttribute("aria-pressed", String(f === "all"));
  $("f-review").setAttribute("aria-pressed", String(f === "review"));
  if (last) renderBody();
}
$("f-all").addEventListener("click", () => setFilter("all"));
$("f-review").addEventListener("click", () => setFilter("review"));

async function download(format) {
  if (!last) return;
  try {
    const r = await api("/api/export?run=" + encodeURIComponent(last.out.run_id) + "&format=" + format);  // the server keeps the result
    const url = URL.createObjectURL(await r.blob());
    const a = h("a", { href: url, download: "decisions." + format });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  } catch (e) { $("runmsg").className = "msg-line bad"; $("runmsg").textContent = e.message; }
}
$("dl-csv").addEventListener("click", () => download("csv"));
$("dl-xlsx").addEventListener("click", () => download("xlsx"));

const defKey = (r) => JSON.stringify([r.read, r.questions, r.review_below]);
function currentKey() {
  try { return defKey(buildRecipe(true)); } catch (e) { return null; }
}
function renderUse() {
  const typed = $("rname").value.trim();
  const name = NAME_RE.test(typed) ? typed : savedName || "my-recipe";
  const cur = currentKey();
  const saved = savedName && name === savedName && cur !== null && cur === savedKey;  // the form is what the saved file holds
  const shown = !last || defKey(last.recipe) === savedKey;  // and so is the table
  $("usenote").textContent = !saved ? `Press Save as recipe to keep "${name}". Then it works the same way from every tool.`
    : !shown ? `The saved recipe "${name}" is not the one that made this table. Run again to see what it gives.`
    : `The saved recipe "${name}" works the same way from every tool.`;
  $("uselines").replaceChildren(
    codeBlock(`decisiontune run ${name} tickets.csv -o sorted.csv`),
    codeBlock(`Recipe.load("${name}").run(rows)            # Python`),
    codeBlock(`POST http://localhost:${PORT}/recipes/${name}/run     # HTTP`),
    codeBlock(`"Run ${name} on this file."                  # Claude Desktop, Cursor (MCP)`));
}
$("rname").addEventListener("input", renderUse);
$("cols").addEventListener("change", () => { colsEdited = true; updateRun(); });
for (const [el, evs] of [[$("qs"), ["input", "change", "click"]], [$("thr"), ["input"]], [$("cols"), ["change"]], [$("addq"), ["click"]]]) {
  evs.forEach((ev) => el.addEventListener(ev, renderUse));  // any edit to the definition can make the saved copy differ
}

// save
$("savebtn").addEventListener("click", () => { $("saverow").hidden = !$("saverow").hidden; if (!$("saverow").hidden) $("rname").focus(); });
async function save() {
  const msg = $("runmsg");
  try {
    const recipe = buildRecipe(true);
    if (!NAME_RE.test(recipe.name)) throw new Error("Give the recipe a name: lowercase letters, numbers, - and _.");
    const out = await postJSON("/api/recipes", recipe);
    savedName = out.saved;
    savedKey = defKey(recipe);
    msg.className = "msg-line";
    msg.textContent = `Saved as "${out.saved}".`;
    $("saverow").hidden = true;
    renderUse();
    refreshRecipes();
  } catch (e) { msg.className = "msg-line bad"; msg.textContent = e.message; }
}
$("savego").addEventListener("click", save);
$("rname").addEventListener("keydown", (e) => { if (e.key === "Enter") save(); });

// ---- 3. Recipes
async function refreshRecipes() {
  let list = [];
  try { list = await getJSON("/api/recipes"); } catch (e) { /* the status chip already shows the problem */ }
  $("pick").replaceChildren(h("option", { value: "" }, "Choose a recipe..."), ...list.map((r) => h("option", { value: r.name }, r.name)));
  $("pick").value = "";
  $("reclist").replaceChildren(list.length ? h("div", { class: "reclist" }, list.map((r) => h("div", { class: "box rec" },
    h("h3", null, r.name, h("span", { class: "src" }, r.source)),
    h("p", null, "Questions: " + r.questions.join(", ")),
    h("button", { type: "button", class: "btn ghost", onclick: async () => { showTab("run"); history.replaceState(null, "", "#tab-run"); try { await loadRecipe(r.name); } catch (e) { $("runmsg").className = "msg-line bad"; $("runmsg").textContent = e.message; } } }, "Load into Run tab"))))
    : h("p", { class: "soft" }, "No recipes yet. Build one on the Run on many items tab and press Save as recipe."));
}
$("pick").addEventListener("change", async () => {
  const name = $("pick").value;
  if (!name) return;
  try { await loadRecipe(name); } catch (e) { $("runmsg").className = "msg-line bad"; $("runmsg").textContent = e.message; }
});

// ---- 4. Connect your tools
const base = "http://localhost:" + PORT;
$("conn").replaceChildren(
  ...[
    ["Claude Desktop or Cursor", 'Add this to the MCP settings. Then ask: "Use DecisionTune to sort these tickets."',
      JSON.stringify({ mcpServers: { decisiontune: { command: "decisiontune", args: ["mcp"] } } }, null, 2)],
    ["n8n, Shortcuts, Raycast", "Send a POST request with a JSON body to this address. The app must be open.",
      `${base}/decide\n{"state": "...", "question": "...",\n "options": ["billing", "shipping"]}`],
    ["Python", "For scripts and AI agents.",
      'from decision_tune import DecisionModel\nm = DecisionModel.from_pretrained(\n    "decision-tune/decisiontune-1.0")\nm.choose(text, question, options)'],
    ["Any app", "Use the same request from JavaScript, Go, Swift or any other language.",
      `curl -s ${base}/decide \\\n  -H 'Content-Type: application/json' \\\n  -d '{"state": "...", "question": "...", "options": ["a", "b"]}'`],
  ].map(([t, p, code]) => h("div", { class: "box" }, h("h3", null, t), h("p", null, p), codeBlock(code))));

addQuestion();
renderUse();
$("runbtn").disabled = true;  // until items are loaded
refreshRecipes();
{ const t = location.hash.startsWith("#tab-") ? location.hash.slice(5) : ""; if (["try", "run", "recipes", "connect"].includes(t)) showTab(t); }
