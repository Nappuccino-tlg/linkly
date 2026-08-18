const $ = (id) => document.getElementById(id);
const TOKEN_KEY = "linkly.token";

let token = localStorage.getItem(TOKEN_KEY);

function say(text, kind) {
  const box = $("message");
  box.textContent = text;
  box.className = "msg " + kind;
}

function clearMessage() {
  $("message").className = "msg hidden";
}

// The API answers every failure as {"error": {message, details}}, so one reader
// handles validation errors and HTTP errors alike.
function describe(payload, status) {
  const error = payload && payload.error;
  if (!error) return "Request failed (" + status + ")";
  if (error.details && error.details.length) {
    return error.details.map((d) => d.field + ": " + d.message).join(", ");
  }
  return error.message;
}

async function api(path, options = {}) {
  const headers = Object.assign({}, options.headers);
  if (token) headers["Authorization"] = "Bearer " + token;
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";

  const response = await fetch("/api" + path, Object.assign({}, options, { headers }));

  if (response.status === 401) {
    signOut();
    throw new Error("Your session has expired. Sign in again.");
  }
  if (response.status === 204) return null;

  const isJson = (response.headers.get("content-type") || "").includes("json");
  const payload = isJson ? await response.json() : null;
  if (!response.ok) throw new Error(describe(payload, response.status));
  return payload;
}

async function authenticate(path, body, headers) {
  const response = await fetch(path, { method: "POST", headers, body });
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(describe(payload, response.status));
  return payload;
}

function show(view) {
  $("auth").classList.toggle("hidden", view !== "auth");
  $("dashboard").classList.toggle("hidden", view !== "dashboard");
}

function signOut() {
  token = null;
  localStorage.removeItem(TOKEN_KEY);
  $("detail").classList.add("hidden");
  show("auth");
}

async function signIn() {
  const body = new URLSearchParams({ username: $("email").value, password: $("password").value });
  const data = await authenticate("/auth/token", body, {
    "Content-Type": "application/x-www-form-urlencoded",
  });
  token = data.access_token;
  localStorage.setItem(TOKEN_KEY, token);
  $("password").value = "";
  clearMessage();
  show("dashboard");
  await loadLinks();
}

async function signUp() {
  await authenticate(
    "/auth/register",
    JSON.stringify({ email: $("email").value, password: $("password").value }),
    { "Content-Type": "application/json" }
  );
  await signIn();
}

async function createLink() {
  const body = { target_url: $("target").value.trim() };
  const code = $("code").value.trim();
  if (code) body.custom_code = code;

  await api("/links", { method: "POST", body: JSON.stringify(body) });
  $("target").value = "";
  $("code").value = "";
  say("Link created", "ok");
  await loadLinks();
}

function button(text, className, onClick) {
  const element = document.createElement("button");
  element.className = className;
  element.textContent = text;
  element.addEventListener("click", () => run(onClick));
  return element;
}

function linkRow(link) {
  const row = document.createElement("div");
  row.className = "item";

  const grow = document.createElement("div");
  grow.className = "grow";

  const code = document.createElement("code");
  code.textContent = link.short_url;
  grow.appendChild(code);

  const target = document.createElement("span");
  target.className = "target";
  target.textContent = link.target_url;
  grow.appendChild(target);
  row.appendChild(grow);

  const pill = document.createElement("span");
  pill.className = link.is_active ? "pill" : "pill off";
  pill.textContent = link.is_active ? "active" : "disabled";
  row.appendChild(pill);

  row.appendChild(button("Copy", "link", () => copy(link.short_url)));
  row.appendChild(button("Stats", "link", () => showStats(link.code)));
  row.appendChild(button("QR", "link", () => showQr(link.code)));
  row.appendChild(
    button(link.is_active ? "Disable" : "Enable", "ghost", () => toggle(link.code, !link.is_active))
  );
  row.appendChild(button("Delete", "danger", () => remove(link.code)));
  return row;
}

async function copy(text) {
  await navigator.clipboard.writeText(text);
  say("Copied " + text, "ok");
}

async function toggle(code, isActive) {
  await api("/links/" + code, {
    method: "PATCH",
    body: JSON.stringify({ is_active: isActive }),
  });
  await loadLinks();
}

async function remove(code) {
  if (!confirm("Delete " + code + "? Anyone holding the short link will get a 404.")) return;
  await api("/links/" + code, { method: "DELETE" });
  $("detail").classList.add("hidden");
  await loadLinks();
}

async function loadLinks() {
  const page = await api("/links?limit=100");
  const container = $("links");
  container.textContent = "";

  if (!page.items.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No links yet. Shorten one above.";
    container.appendChild(empty);
    return;
  }
  page.items.forEach((link) => container.appendChild(linkRow(link)));
}

function number(value, label) {
  const cell = document.createElement("div");
  const strong = document.createElement("b");
  strong.textContent = value;
  const caption = document.createElement("span");
  caption.textContent = label;
  cell.append(strong, caption);
  return cell;
}

async function showStats(code) {
  const stats = await api("/links/" + code + "/stats");
  const panel = $("detail");
  panel.textContent = "";
  panel.classList.remove("hidden");

  const heading = document.createElement("h2");
  heading.textContent = "Stats for /" + code;
  panel.appendChild(heading);

  const numbers = document.createElement("div");
  numbers.className = "numbers";
  numbers.append(
    number(stats.total_clicks, "clicks"),
    number(stats.unique_visitors, "unique visitors"),
    number(stats.daily.length, "active days")
  );
  panel.appendChild(numbers);

  if (stats.daily.length) {
    const peak = Math.max.apply(
      null,
      stats.daily.map((d) => d.count)
    );
    const bars = document.createElement("div");
    bars.className = "bars";
    stats.daily.forEach((day) => {
      const bar = document.createElement("div");
      bar.style.height = Math.round((day.count / peak) * 100) + "%";
      bar.title = day.day + ": " + day.count;
      bars.appendChild(bar);
    });
    panel.appendChild(bars);

    const caption = document.createElement("p");
    caption.className = "muted";
    caption.textContent = stats.daily[0].day + " to " + stats.daily[stats.daily.length - 1].day;
    panel.appendChild(caption);
  } else {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No clicks yet. Open the short link and come back.";
    panel.appendChild(empty);
  }

  if (stats.top_referrers.length) {
    const table = document.createElement("table");
    stats.top_referrers.forEach((referrer) => {
      const tr = document.createElement("tr");
      const name = document.createElement("td");
      name.textContent = referrer.referrer;
      const count = document.createElement("td");
      count.textContent = referrer.count;
      tr.append(name, count);
      table.appendChild(tr);
    });
    panel.appendChild(table);
  }
}

async function showQr(code) {
  // The QR endpoint is owner-only, so an <img src> cannot fetch it -- there is no way to
  // attach the bearer token to an image request. Fetch it as a blob instead.
  const response = await fetch("/api/links/" + code + "/qr?box_size=6", {
    headers: { Authorization: "Bearer " + token },
  });
  if (!response.ok) throw new Error("Could not load the QR code");

  const panel = $("detail");
  panel.textContent = "";
  panel.classList.remove("hidden");

  const heading = document.createElement("h2");
  heading.textContent = "QR code for /" + code;
  panel.appendChild(heading);

  const image = document.createElement("img");
  image.className = "qr";
  image.alt = "QR code for the short link " + code;
  image.src = URL.createObjectURL(await response.blob());
  image.addEventListener("load", () => URL.revokeObjectURL(image.src));
  panel.appendChild(image);
}

async function run(action) {
  try {
    clearMessage();
    await action();
  } catch (error) {
    say(error.message, "error");
  }
}

$("sign-in").addEventListener("click", () => run(signIn));
$("sign-up").addEventListener("click", () => run(signUp));
$("sign-out").addEventListener("click", signOut);
$("create").addEventListener("click", () => run(createLink));
$("password").addEventListener("keydown", (e) => {
  if (e.key === "Enter") run(signIn);
});
$("target").addEventListener("keydown", (e) => {
  if (e.key === "Enter") run(createLink);
});

if (token) {
  show("dashboard");
  run(loadLinks);
} else {
  show("auth");
}
