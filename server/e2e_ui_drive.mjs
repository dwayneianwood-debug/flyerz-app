// Drive the built job page the way a salesperson does: open it, pick a style, reload.
const jobId = process.argv[2];
const mode = process.argv[3] || "expect-done";
const base = process.argv[4] || "http://127.0.0.1:5000";
const debugPort = process.argv[5] || "9222";

if (!jobId) {
  console.error("usage: e2e_ui_drive.mjs <jobId> [expect-done|expect-fix] [base] [debugPort]");
  process.exit(2);
}

async function openTab(url) {
  const endpoint = `http://127.0.0.1:${debugPort}/json/new?${encodeURIComponent(url)}`;
  let response = await fetch(endpoint, { method: "PUT" });
  if (!response.ok) response = await fetch(endpoint);
  if (!response.ok) throw new Error(`chrome tab ${response.status}`);
  return response.json();
}

const list = await openTab(`${base}/job/${jobId}`);
const socket = new WebSocket(list.webSocketDebuggerUrl);
const pending = new Map();
let nextId = 0;

function send(method, params = {}) {
  const id = ++nextId;
  socket.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
  });
}

await new Promise((resolve, reject) => {
  socket.addEventListener("open", resolve);
  socket.addEventListener("error", reject);
});
socket.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (!message.id || !pending.has(message.id)) return;
  const waiter = pending.get(message.id);
  pending.delete(message.id);
  if (message.error) waiter.reject(new Error(JSON.stringify(message.error)));
  else waiter.resolve(message.result);
});

await send("Runtime.enable");
await send("Page.enable");
await send("Page.navigate", { url: `${base}/job/${jobId}` });

async function evalPage(expression) {
  const result = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  return result.result?.value;
}

async function readState() {
  return evalPage(`(() => {
    const fix = document.querySelector('[data-testid="button-auto-fix"]');
    const styles = document.querySelector('[data-testid="select-bleed-method"]');
    const text = document.body.innerText || "";
    return JSON.stringify({
      fix: !!fix,
      styles: !!styles,
      coverError: text.includes("Could not read artwork"),
      ready: !!styles,
    });
  })()`);
}

let state = "";
for (let attempt = 0; attempt < 40; attempt += 1) {
  await new Promise((resolve) => setTimeout(resolve, 500));
  state = await readState();
  if (state && state.includes('"ready":true')) break;
}

let proof = "";
for (let attempt = 0; attempt < 20; attempt += 1) {
  await new Promise((resolve) => setTimeout(resolve, 300));
  proof = await evalPage(`(() => {
    const page2 = document.querySelector('[data-testid="button-bleed-page-2"]');
    const before = document.querySelector('[data-testid="img-press-before"]');
    return JSON.stringify({ page2: !!page2, before: !!before });
  })()`);
  if (proof && proof.includes('"page2":true') && proof.includes('"before":true')) break;
}

let style = "";
if (mode === "expect-done") {
  await evalPage(`(() => {
    const sel = document.querySelector('[data-testid="select-bleed-method"]');
    if (!sel) return "no-select";
    const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value").set;
    setter.call(sel, "stretch");
    sel.dispatchEvent(new Event("change", { bubbles: true }));
    return "set";
  })()`);
  for (let attempt = 0; attempt < 20; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 300));
    style = await evalPage(`(() => {
      const pages = document.querySelector('[data-testid="style-page-selector"]');
      const page2 = document.querySelector('[data-testid="button-style-page-2"]');
      return JSON.stringify({ selector: !!pages, page2: !!page2 });
    })()`);
    if (style && style.includes('"page2":true')) break;
  }
  await send("Page.reload", { ignoreCache: true });
  for (let attempt = 0; attempt < 30; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    state = await readState();
    if (state && state.includes('"ready":true')) break;
  }
}

console.log(JSON.stringify({ mode, state, style, proof }));
socket.close();
