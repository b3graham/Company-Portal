// Confirm prompts for destructive forms.
document.addEventListener("submit", (e) => {
  const msg = e.target.dataset && e.target.dataset.confirm;
  if (msg && !window.confirm(msg)) e.preventDefault();
});

// Live clock widget on the home page.
const clockTime = document.getElementById("clock-time");
if (clockTime) {
  const clockDate = document.getElementById("clock-date");
  const tick = () => {
    const now = new Date();
    clockTime.textContent = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
    clockDate.textContent = now.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
  };
  tick();
  setInterval(tick, 1000);
}

// Weather widget: server proxies Open-Meteo and caches for 10 minutes.
const weatherCards = document.getElementById("weather-cards");
if (weatherCards && weatherCards.querySelector(".wcity")) {
  fetch("/api/weather")
    .then((r) => r.json())
    .then((data) => {
      for (const c of data.cities) {
        const el = weatherCards.querySelector(`.wcity[data-id="${c.id}"]`);
        if (!el) continue;
        el.querySelector(".w-emoji").textContent = c.emoji;
        el.querySelector(".w-temp").textContent = `${Math.round(c.temp)}°F`;
        el.querySelector(".w-desc").textContent =
          `${c.desc} · feels ${Math.round(c.feels)}° · ${Math.round(c.wind)} mph · ${c.humidity}% rh`;
      }
      if (data.errors) {
        weatherCards.querySelectorAll(".w-temp").forEach((el) => {
          if (el.textContent === "…") el.textContent = "n/a";
        });
      }
    })
    .catch(() => {
      weatherCards.querySelectorAll(".w-temp").forEach((el) => { el.textContent = "n/a"; });
    });
}

// Stock ticker: server proxies Yahoo Finance and caches for 2 minutes.
const tickerTrack = document.getElementById("ticker-track");
if (tickerTrack && tickerTrack.textContent.includes("Loading")) {
  const fmtPrice = (p) => p >= 1000
    ? p.toLocaleString([], { maximumFractionDigits: 0 })
    : p.toFixed(2);
  const item = (s) => {
    const el = document.createElement("span");
    el.className = "ticker-item";
    const up = (s.change ?? 0) >= 0;
    const sym = document.createElement("strong");
    sym.textContent = s.symbol.startsWith("^") ? s.name : s.symbol;
    sym.title = s.name;
    const price = document.createElement("span");
    price.textContent = fmtPrice(s.price);
    const delta = document.createElement("span");
    delta.className = up ? "ticker-up" : "ticker-down";
    delta.textContent = s.change == null ? "" :
      `${up ? "▲" : "▼"} ${Math.abs(s.change).toFixed(2)} (${up ? "+" : "-"}${Math.abs(s.change_pct).toFixed(2)}%)`;
    el.append(sym, price, delta);
    return el;
  };
  const render = (stocks) => {
    if (!stocks.length) return;
    tickerTrack.innerHTML = "";
    // Two copies of the list make the -50% translate loop seamless.
    for (let pass = 0; pass < 2; pass++) stocks.forEach((s) => tickerTrack.appendChild(item(s)));
    tickerTrack.style.animationDuration = `${stocks.length * 5}s`;
    tickerTrack.classList.add("ticker-scrolling");
  };
  const load = () => fetch("/api/stocks")
    .then((r) => r.json())
    .then((data) => render(data.stocks || []))
    .catch(() => { tickerTrack.innerHTML = '<span class="muted small">Quotes unavailable right now.</span>'; });
  load();
  setInterval(load, 120000);
}

// World news widget: top 5 stories, server-cached RSS.
const newsList = document.getElementById("news-list");
if (newsList) {
  const unavailable = () => { newsList.innerHTML = '<li class="muted small">News unavailable right now.</li>'; };
  fetch("/api/news")
    .then((r) => r.json())
    .then((data) => {
      if (!data.items || !data.items.length) return unavailable();
      newsList.innerHTML = "";
      for (const it of data.items) {
        const li = document.createElement("li");
        const a = document.createElement("a");
        a.href = it.link;
        a.target = "_blank";
        a.rel = "noopener";
        a.textContent = it.title;
        li.appendChild(a);
        if (it.ts) {
          const mins = Math.max(1, Math.round(Date.now() / 1000 / 60 - it.ts / 60));
          const label = mins < 60 ? `${mins}m ago` : `${Math.round(mins / 60)}h ago`;
          const when = document.createElement("span");
          when.className = "muted small";
          when.textContent = ` · ${label}`;
          li.appendChild(when);
        }
        newsList.appendChild(li);
      }
      document.getElementById("news-src").textContent = data.source + (data.stale ? " (cached)" : "");
    })
    .catch(unavailable);
}

// Markdown preview toggle in the doc editor (server-rendered for fidelity).
const toggle = document.getElementById("preview-toggle");
if (toggle) {
  const textarea = document.getElementById("editor-content");
  const preview = document.getElementById("preview");
  toggle.addEventListener("click", async () => {
    if (preview.hidden) {
      const body = new FormData();
      body.append("content", textarea.value);
      const res = await fetch("/docs/preview", { method: "POST", body });
      preview.innerHTML = (await res.json()).html;
      preview.hidden = false;
      textarea.hidden = true;
      toggle.textContent = "Edit";
    } else {
      preview.hidden = true;
      textarea.hidden = false;
      toggle.textContent = "Preview";
    }
  });
}
