"use strict";

const NUTRIENTS = [
  { key: "kcal", label: "Calories", short: "kcal", unit: "" },
  { key: "carbs_g", label: "Carbs", short: "carbs", unit: "g" },
  { key: "protein_g", label: "Protein", short: "protein", unit: "g", goal: true },
  { key: "fat_g", label: "Fat", short: "fat", unit: "g" },
  { key: "sugar_g", label: "Sugar", short: "sugar", unit: "g" },
];
const SCALES = [0.5, 0.75, 1, 1.25, 1.5, 2];
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const state = { config: null, people: [], me: null, view: "log", day: null, week: null, estimate: null, photo: null };
const $ = (id) => document.getElementById(id);

function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch (_) {
    return null;
  }
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (name === "class") node.className = value;
    else if (name === "style") node.style.cssText = value;
    else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
    else node.setAttribute(name, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function svg(tag, attrs = {}, ...children) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
    else node.setAttribute(name, value);
  }
  for (const child of children.flat()) if (child) node.append(child instanceof Node ? child : document.createTextNode(child));
  return node;
}

const fmt = (n) => Math.round(n).toLocaleString();
const amount = (n, unit) => `${fmt(n)}${unit}`;
const personColor = (person) => `var(--person-${(state.people.indexOf(person) % 2) + 1})`;

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (response.status === 204) return null;
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body.detail;
    const message = typeof detail === "string" ? detail : detail?.message || (Array.isArray(detail) ? detail.map((d) => d.msg).join("; ") : `HTTP ${response.status}`);
    throw new Error(message);
  }
  return body;
}

function toast(message) {
  const node = $("toast");
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (node.hidden = true), 2600);
}

function shiftDate(iso, days) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function dateLabel(iso, withWeekday = true) {
  const d = new Date(`${iso}T00:00:00Z`);
  const opts = { day: "numeric", month: "short", timeZone: "UTC" };
  if (withWeekday) opts.weekday = "short";
  const text = d.toLocaleDateString(undefined, opts);
  if (iso === state.config.today) return `Today, ${text}`;
  if (iso === shiftDate(state.config.today, -1)) return `Yesterday, ${text}`;
  return text;
}

// ---------- people ----------

function renderWho() {
  const who = $("who");
  who.replaceChildren(
    ...state.people.map((person) =>
      el(
        "button",
        {
          role: "radio",
          "aria-checked": String(person.id === state.me),
          onclick: () => {
            state.me = person.id;
            store("fatties.me", String(person.id));
            renderWho();
            if (state.estimate) renderEstimate();
          },
        },
        el("span", { class: "dot", style: `background:${personColor(person)}` }),
        person.name,
      ),
    ),
  );
}

// ---------- tooltip ----------

function showTip(event, lines) {
  const tip = $("tooltip");
  tip.replaceChildren(...lines.map((line, i) => el("div", i === 0 ? { style: "font-weight:600" } : {}, line)));
  tip.hidden = false;
  const x = Math.min(window.innerWidth - tip.offsetWidth - 8, event.clientX + 12);
  const y = Math.max(8, event.clientY - tip.offsetHeight - 12);
  tip.style.left = `${Math.max(8, x)}px`;
  tip.style.top = `${y}px`;
}
const hideTip = () => ($("tooltip").hidden = true);
document.addEventListener("scroll", hideTip, { passive: true });

// ---------- progress bars ----------

function progressRow(n, consumed, target, color) {
  const scale = Math.max(consumed, target) || 1;
  const within = Math.min(consumed, target);
  const over = consumed > target && !n.goal;
  const track = el("div", { class: "track" });
  track.append(el("div", { class: "fill", style: `width:${(within / scale) * 100}%;background:${color}` }));
  if (consumed > target) {
    const start = (target / scale) * 100;
    if (over) track.append(el("div", { class: "fill-over", style: `left:calc(${start}% + 2px);width:calc(${100 - start}% - 2px)` }));
    else track.append(el("div", { class: "fill", style: `left:calc(${start}% + 2px);width:calc(${100 - start}% - 2px);background:${color};opacity:.55` }));
    track.append(el("div", { class: "target-tick", style: `left:calc(${start}% - 1px)` }));
  }
  const left = target - consumed;
  let note;
  if (left >= 0) note = el("span", {}, n.goal ? `${amount(left, n.unit)} to go` : `${amount(left, n.unit)} left`);
  else note = el("span", { class: n.goal ? "good-text" : "over-text" }, n.goal ? `goal met` : `${amount(-left, n.unit)} over`);
  return el(
    "div",
    { class: "bar-row" },
    el("span", { class: "name" }, n.label),
    track,
    el("span", { class: "value" }, `${fmt(consumed)} / ${amount(target, n.unit)} · `, note),
  );
}

// ---------- day ----------

async function loadDay() {
  const data = await api(`/api/day?date=${state.day}`);
  $("day-label").textContent = dateLabel(state.day);
  $("day-people").replaceChildren(...data.people.map(dayCard));
}

function dayCard(entry) {
  const { person, consumed, target, meals } = entry;
  const color = personColor(state.people.find((p) => p.id === person.id));
  const left = target.kcal - consumed.kcal;
  return el(
    "article",
    { class: "card" },
    el("div", { class: "person-head" }, el("span", { class: "dot", style: `background:${color}` }), el("h3", {}, person.name)),
    el(
      "div",
      { class: "hero" },
      el("strong", { class: left < 0 ? "over-text" : null }, fmt(Math.abs(left))),
      el("span", {}, left < 0 ? `kcal over the ${fmt(target.kcal)} target` : `kcal left of ${fmt(target.kcal)}`),
    ),
    el("div", { class: "bars" }, NUTRIENTS.map((n) => progressRow(n, consumed[n.key], target[n.key], color))),
    person.targets_set ? null : el("p", { class: "small" }, "Using default targets. Set real ones in Targets."),
    meals.length
      ? el("ul", { class: "meals" }, meals.map(mealRow))
      : el("p", { class: "empty" }, "Nothing logged."),
  );
}

function mealRow(meal) {
  const t = meal.totals;
  return el(
    "li",
    { class: "meal" },
    el("span", { class: "title" }, meal.title),
    el("span", { class: "meta" }, [meal.eaten_at.slice(11, 16), meal.meal_type].filter(Boolean).join(" · ")),
    el("span", { class: "macros" }, `${fmt(t.kcal)} kcal · C ${fmt(t.carbs_g)}g · P ${fmt(t.protein_g)}g · F ${fmt(t.fat_g)}g · S ${fmt(t.sugar_g)}g`),
    el(
      "button",
      {
        "aria-label": `Delete ${meal.title}`,
        onclick: async () => {
          if (!confirm(`Delete "${meal.title}"?`)) return;
          await api(`/api/meals/${meal.id}`, { method: "DELETE" });
          toast("Meal deleted");
          loadDay();
        },
      },
      "Delete",
    ),
  );
}

// ---------- week ----------

async function loadWeek() {
  const data = await api(`/api/week?date=${state.week}`);
  $("week-label").textContent = `${dateLabel(data.start, false)} – ${dateLabel(data.end, false)}`.replace(/Today, |Yesterday, /g, "");
  $("week-people").replaceChildren(...data.people.map(weekCard));
  renderCompare(data);
}

function weekChart(entry, color) {
  const W = 340, H = 170, top = 14, bottom = 22, left = 4, right = 40;
  const target = entry.target.kcal;
  const maxValue = Math.max(target * 1.15, ...entry.days.map((d) => d.consumed.kcal)) || 1;
  const y = (v) => top + (H - top - bottom) * (1 - v / maxValue);
  const slot = (W - left - right) / 7;
  const barW = Math.min(26, slot - 10);
  const base = y(0);
  const nodes = [];
  for (const frac of [0.5, 1]) {
    const v = maxValue * frac;
    nodes.push(svg("line", { class: "grid", x1: left, x2: W - right, y1: y(v), y2: y(v) }));
  }
  entry.days.forEach((day, i) => {
    const cx = left + slot * i + slot / 2;
    const kcal = day.consumed.kcal;
    const x = cx - barW / 2;
    if (kcal > 0) {
      const within = Math.min(kcal, target);
      // Rounded top only on the data end; the baseline stays square.
      nodes.push(barPath(x, y(within), barW, base - y(within), kcal <= target ? 4 : 0, color));
      if (kcal > target) nodes.push(barPath(x, y(kcal), barW, y(target) - y(kcal) - 2, 4, "var(--over)"));
    }
    const isToday = day.date === state.config.today;
    nodes.push(
      svg("text", { x: cx, y: H - 6, "text-anchor": "middle", style: isToday ? "font-weight:700;fill:var(--ink)" : "" }, WEEKDAYS[i]),
    );
    const lines = [dateLabel(day.date)];
    if (day.logged) {
      const c = day.consumed;
      lines.push(`${fmt(c.kcal)} kcal (${day.percent.kcal}% of target)`);
      lines.push(`C ${fmt(c.carbs_g)}g · P ${fmt(c.protein_g)}g · F ${fmt(c.fat_g)}g · S ${fmt(c.sugar_g)}g`);
      lines.push(`${day.meals} meal${day.meals === 1 ? "" : "s"} logged`);
    } else lines.push("Nothing logged");
    nodes.push(
      svg("rect", {
        class: "hit", x: left + slot * i, y: top, width: slot, height: base - top,
        onpointerenter: (e) => showTip(e, lines), onpointermove: (e) => showTip(e, lines),
        onpointerleave: hideTip, onclick: (e) => showTip(e, lines),
      }),
    );
  });
  nodes.push(svg("line", { class: "base", x1: left, x2: W - right, y1: base, y2: base }));
  nodes.push(svg("line", { class: "target", x1: left, x2: W - right, y1: y(target), y2: y(target) }));
  nodes.push(svg("text", { class: "target-label", x: W - right + 4, y: y(target) + 4 }, fmt(target)));
  return svg("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `Calories per day for ${entry.person.name}` }, nodes);
}

function barPath(x, yTop, w, h, r, fill) {
  if (h <= 0) return null;
  r = Math.min(r, h, w / 2);
  const d = `M${x},${yTop + h} V${yTop + r} Q${x},${yTop} ${x + r},${yTop} H${x + w - r} Q${x + w},${yTop} ${x + w},${yTop + r} V${yTop + h} Z`;
  return svg("path", { d, fill, "pointer-events": "none" });
}

function delta(value, n) {
  if (value === null || value === undefined) return el("span", { style: "color:var(--muted)" }, "–");
  if (Math.abs(value) < 0.5) return el("span", {}, "same");
  const up = value > 0;
  // Eating more protein is the good direction; for everything else less is better.
  const good = n.goal ? up : !up;
  return el("span", { class: good ? "delta-down" : "delta-up" }, `${up ? "▲" : "▼"} ${amount(Math.abs(value), n.unit)}`);
}

function weekCard(entry) {
  const person = state.people.find((p) => p.id === entry.person.id);
  const color = personColor(person);
  const avg = entry.daily_average;
  const change = entry.previous_week.change;
  return el(
    "article",
    { class: "card" },
    el("div", { class: "person-head" }, el("span", { class: "dot", style: `background:${color}` }), el("h3", {}, entry.person.name)),
    el(
      "div",
      { class: "legend" },
      el("span", {}, el("span", { class: "dot", style: `background:${color};border-radius:3px` }), "kcal eaten"),
      el("span", {}, el("span", { class: "dot", style: "background:var(--over);border-radius:3px" }), "over target"),
      el("span", {}, el("span", { class: "swatch-line" }), "daily target"),
    ),
    weekChart(entry, color),
    el(
      "div",
      { class: "stats" },
      el("div", {}, el("strong", {}, entry.logged_days ? fmt(avg.kcal) : "–"), el("span", {}, `avg kcal/day (target ${fmt(entry.target.kcal)})`)),
      el("div", {}, el("strong", { class: entry.days_over_kcal ? "over-text" : null }, `${entry.days_over_kcal}`), el("span", {}, `of ${entry.logged_days} logged days over`)),
      el("div", {}, el("strong", {}, `${entry.days_protein_hit}`), el("span", {}, "days protein goal hit")),
    ),
    el(
      "div",
      { class: "table-wrap" },
      el(
        "table",
        {},
        el("thead", {}, el("tr", {}, el("th", {}, ""), el("th", {}, "Avg/day"), el("th", {}, "Target"), el("th", {}, "%"), el("th", {}, "vs last wk"))),
        el(
          "tbody",
          {},
          NUTRIENTS.map((n) =>
            el(
              "tr",
              {},
              el("td", {}, n.label),
              el("td", {}, entry.logged_days ? amount(avg[n.key], n.unit) : "–"),
              el("td", {}, amount(entry.target[n.key], n.unit)),
              el("td", {}, entry.logged_days ? `${entry.average_percent[n.key]}%` : "–"),
              el("td", {}, delta(change ? change[n.key] : null, n)),
            ),
          ),
        ),
      ),
    ),
    el(
      "p",
      { class: "small" },
      entry.elapsed_days
        ? `Week so far: ${fmt(entry.total.kcal)} of ${fmt(entry.target_to_date.kcal)} kcal budgeted through ${entry.elapsed_days === 7 ? "Sunday" : "today"}. ` +
            (entry.remaining_to_date.kcal >= 0
              ? `${fmt(entry.remaining_to_date.kcal)} kcal under.`
              : `${fmt(-entry.remaining_to_date.kcal)} kcal over.`) +
            (entry.unlogged_past_days ? ` ${entry.unlogged_past_days} past day${entry.unlogged_past_days === 1 ? "" : "s"} with nothing logged, so the total looks lower than it is.` : "")
        : "This week has not started yet.",
      entry.previous_week.logged_days ? ` Last week averaged ${fmt(entry.previous_week.daily_average.kcal)} kcal/day.` : "",
    ),
  );
}

function renderCompare(data) {
  if (data.people.length < 2) return $("week-compare").replaceChildren();
  const head = el(
    "tr",
    {},
    el("th", {}, "Avg per day"),
    data.people.map((entry) =>
      el("th", {}, el("span", { class: "dot", style: `background:${personColor(state.people.find((p) => p.id === entry.person.id))};margin-right:6px` }), entry.person.name),
    ),
  );
  const rows = NUTRIENTS.map((n) =>
    el(
      "tr",
      {},
      el("td", {}, n.label),
      data.people.map((entry) =>
        el("td", {}, entry.logged_days ? `${amount(entry.daily_average[n.key], n.unit)} · ${entry.average_percent[n.key]}%` : "–"),
      ),
    ),
  );
  $("week-compare").replaceChildren(
    el("h3", {}, "Side by side"),
    el("p", { class: "small" }, "Average per logged day, and the share of each person's own target."),
    el("div", { class: "table-wrap" }, el("table", {}, el("thead", {}, head), el("tbody", {}, rows))),
  );
}

// ---------- log ----------

function guessMealType() {
  const h = new Date().getHours();
  if (h < 5) return "supper";
  if (h < 11) return "breakfast";
  if (h < 15) return "lunch";
  if (h < 17) return "snack";
  if (h < 21) return "dinner";
  return "supper";
}

async function shrinkPhoto(file) {
  // Re-encode as JPEG at most 1568px on the long side: small upload, and iPhone HEIC becomes JPEG.
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => reject(new Error("This photo format cannot be read here. Try a JPEG."));
      image.src = url;
    });
    const ratio = Math.min(1, 1568 / Math.max(img.naturalWidth, img.naturalHeight));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(img.naturalWidth * ratio);
    canvas.height = Math.round(img.naturalHeight * ratio);
    canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
    return await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
  } finally {
    URL.revokeObjectURL(url);
  }
}

function setupLog() {
  $("meal-type").replaceChildren(...state.config.meal_types.map((t) => el("option", { value: t }, t[0].toUpperCase() + t.slice(1))));
  $("meal-type").value = guessMealType();
  $("photo").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    const status = $("analyze-status");
    status.className = "status";
    try {
      state.photo = await shrinkPhoto(file);
      $("preview").src = URL.createObjectURL(state.photo);
      $("preview").hidden = false;
      $("photo-hint").hidden = true;
      $("analyze").disabled = false;
      status.textContent = "";
    } catch (error) {
      status.className = "status error";
      status.textContent = error.message;
    }
  });
  $("photo-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.photo) return;
    const status = $("analyze-status");
    const button = $("analyze");
    const form = new FormData();
    form.append("photo", state.photo, "meal.jpg");
    form.append("meal_type", $("meal-type").value);
    form.append("note", $("note").value);
    button.disabled = true;
    status.className = "status";
    status.replaceChildren(el("span", { class: "spinner" }), "Estimating, this takes about 20 seconds…");
    try {
      const result = await api("/api/analyze", { method: "POST", body: form });
      state.estimate = { ...result, items: result.items.map((item) => ({ base: item, scale: 1, removed: false })) };
      status.textContent = "";
      renderEstimate();
      $("estimate").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
      status.className = "status error";
      status.textContent = `Could not estimate: ${error.message}`;
    } finally {
      button.disabled = false;
    }
  });
}

function scaled(entry) {
  const out = { ...entry.base };
  for (const key of ["estimated_weight_g", "kcal", "carbs_g", "protein_g", "fat_g", "sugar_g"]) {
    out[key] = Math.round(entry.base[key] * entry.scale * 10) / 10;
  }
  if (entry.scale !== 1) out.portion = `${entry.base.portion} (×${entry.scale})`;
  return out;
}

function currentItems() {
  return state.estimate.items.filter((entry) => !entry.removed).map(scaled);
}

function localNow() {
  const d = new Date();
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}

function renderEstimate() {
  const est = state.estimate;
  const box = $("estimate");
  if (!est) return box.replaceChildren();
  if (!est.is_food) {
    return box.replaceChildren(el("div", { class: "card" }, el("h3", {}, "No food found"), el("p", { class: "small" }, est.description)));
  }
  const items = currentItems();
  const sum = (key) => items.reduce((total, item) => total + item[key], 0);
  const me = state.people.find((p) => p.id === state.me);
  const title = el("input", { value: est.meal_title, maxlength: 200, "aria-label": "Meal name" });
  const when = el("input", { type: "datetime-local", value: est.eaten_at || localNow(), onchange: (e) => (est.eaten_at = e.target.value) });
  title.addEventListener("input", (e) => (est.meal_title = e.target.value));
  box.replaceChildren(
    el(
      "div",
      { class: "card" },
      el("label", {}, "Meal", title),
      el("div", { class: "totals" }, NUTRIENTS.map((n) => el("div", {}, el("strong", {}, amount(sum(n.key), n.unit)), el("span", {}, n.short)))),
      el("p", { class: "small" }, est.description),
      est.items.map((entry, index) => itemRow(entry, index)),
      est.assumptions.length || est.warnings.length
        ? el("div", { class: "small" }, `Confidence: ${est.overall_confidence}. Assumed:`, el("ul", {}, [...est.assumptions, ...est.warnings].map((a) => el("li", {}, a))))
        : el("p", { class: "small" }, `Confidence: ${est.overall_confidence}.`),
      el("label", {}, "Eaten at", when),
      el(
        "button",
        { class: "primary", disabled: !items.length || !me, onclick: saveMeal },
        me ? `Save for ${me.name}` : "Pick who ate this at the top",
      ),
    ),
  );
}

function itemRow(entry, index) {
  const item = scaled(entry);
  return el(
    "div",
    { class: `item${entry.removed ? " removed" : ""}` },
    el(
      "div",
      { class: "item-head" },
      el("span", {}, el("span", { class: "name" }, item.name), item.local_name ? el("span", { class: "local" }, ` · ${item.local_name}`) : null),
      el("span", { class: "macros" }, `${fmt(item.kcal)} kcal`),
    ),
    el("span", { class: "portion" }, `${item.portion} · about ${fmt(item.estimated_weight_g)}g · ${item.confidence} confidence`),
    el("span", { class: "macros" }, `C ${fmt(item.carbs_g)}g · P ${fmt(item.protein_g)}g · F ${fmt(item.fat_g)}g · S ${fmt(item.sugar_g)}g`),
    el(
      "div",
      { class: "scale", role: "group", "aria-label": `Portion of ${item.name}` },
      SCALES.map((s) =>
        el(
          "button",
          {
            "aria-pressed": String(!entry.removed && entry.scale === s),
            onclick: () => {
              entry.scale = s;
              entry.removed = false;
              renderEstimate();
            },
          },
          s === 1 ? "as is" : `×${s}`,
        ),
      ),
      el(
        "button",
        { class: "remove", onclick: () => ((entry.removed = !entry.removed), renderEstimate()) },
        entry.removed ? "Undo" : "Remove",
      ),
    ),
  );
}

async function saveMeal() {
  const est = state.estimate;
  try {
    await api("/api/meals", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        person_id: state.me,
        eaten_at: est.eaten_at || localNow(),
        meal_type: $("meal-type").value,
        title: est.meal_title || "Meal",
        note: $("note").value || null,
        items: currentItems(),
        model: est.model,
      }),
    });
  } catch (error) {
    toast(`Not saved: ${error.message}`);
    return;
  }
  toast("Saved");
  state.estimate = null;
  state.photo = null;
  $("photo").value = "";
  $("note").value = "";
  $("preview").hidden = true;
  $("photo-hint").hidden = false;
  $("analyze").disabled = true;
  renderEstimate();
  state.day = state.config.today;
  show("day");
}

// ---------- targets ----------

function targetsCard(person) {
  const color = personColor(person);
  const inputs = {};
  const status = el("p", { class: "status", role: "status" });
  const field = (n) => {
    inputs[n.key] = el("input", { type: "number", inputmode: "decimal", min: 0, step: 1, value: Math.round(person.targets[n.key]) });
    return el("label", {}, `${n.label}${n.unit ? " (g)" : " (kcal)"}${n.key === "sugar_g" ? ", limit" : n.goal ? ", goal" : ""}`, inputs[n.key]);
  };
  const calc = {
    weight_kg: el("input", { type: "number", inputmode: "decimal", step: 0.1, placeholder: "kg" }),
    height_cm: el("input", { type: "number", inputmode: "decimal", step: 1, placeholder: "cm" }),
    age: el("input", { type: "number", inputmode: "numeric", step: 1 }),
    sex: el("select", {}, el("option", { value: "female" }, "Female"), el("option", { value: "male" }, "Male")),
    activity: el(
      "select",
      {},
      el("option", { value: "sedentary" }, "Little exercise"),
      el("option", { value: "light" }, "1–3 times a week"),
      el("option", { value: "moderate", selected: true }, "3–5 times a week"),
      el("option", { value: "active" }, "6–7 times a week"),
    ),
    deficit_kcal: el(
      "select",
      {},
      el("option", { value: 250 }, "Gentle (−250 kcal)"),
      el("option", { value: 500, selected: true }, "Steady (−500 kcal)"),
      el("option", { value: 750 }, "Fast (−750 kcal)"),
    ),
  };
  const suggest = async () => {
    status.className = "status";
    try {
      const body = Object.fromEntries(Object.entries(calc).map(([k, node]) => [k, node.value]));
      const result = await api("/api/targets/suggest", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      for (const n of NUTRIENTS) inputs[n.key].value = result[n.key];
      status.textContent = `Maintenance is about ${fmt(result.maintenance_kcal)} kcal. Filled in below; press Save to keep.`;
    } catch (error) {
      status.className = "status error";
      status.textContent = error.message;
    }
  };
  const save = async () => {
    status.className = "status";
    try {
      const body = Object.fromEntries(NUTRIENTS.map((n) => [n.key, Number(inputs[n.key].value)]));
      const updated = await api(`/api/people/${person.id}/targets`, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      Object.assign(person, updated);
      status.textContent = "Saved.";
    } catch (error) {
      status.className = "status error";
      status.textContent = error.message;
    }
  };
  return el(
    "article",
    { class: "card" },
    el("div", { class: "person-head" }, el("span", { class: "dot", style: `background:${color}` }), el("h3", {}, person.name)),
    el("p", { class: "small" }, "Work out a starting point from body stats, or type targets directly."),
    el("div", { class: "row" }, ...["weight_kg", "height_cm", "age"].map((k) => el("label", { class: "grow" }, { weight_kg: "Weight (kg)", height_cm: "Height (cm)", age: "Age" }[k], calc[k]))),
    el("div", { class: "row" }, el("label", { class: "grow" }, "Sex", calc.sex), el("label", { class: "grow" }, "Exercise", calc.activity), el("label", { class: "grow" }, "Pace", calc.deficit_kcal)),
    el("button", { class: "ghost", onclick: suggest }, "Suggest targets"),
    el("div", { class: "row" }, NUTRIENTS.map((n) => el("div", { class: "grow", style: "flex-basis:120px" }, field(n)))),
    el("button", { class: "primary", onclick: save }, "Save targets"),
    status,
    el("p", { class: "small" }, "Protein about 1.8 g per kg, fat about 27% of calories, sugar under 10% (WHO), carbs the rest. A starting point, not medical advice."),
  );
}

function loadTargets() {
  $("target-people").replaceChildren(...state.people.map(targetsCard));
}

// ---------- navigation ----------

function show(view) {
  state.view = view;
  store("fatties.view", view);
  hideTip();
  for (const tab of document.querySelectorAll(".tabs button")) tab.setAttribute("aria-selected", String(tab.dataset.view === view));
  for (const section of document.querySelectorAll(".view")) section.hidden = section.id !== `view-${view}`;
  const load = { day: loadDay, week: loadWeek, targets: loadTargets }[view];
  if (load) Promise.resolve().then(load).catch((error) => toast(error.message));
}

async function init() {
  state.config = await api("/api/config");
  state.people = await api("/api/people");
  state.day = state.week = state.config.today;
  const saved = Number(store("fatties.me"));
  state.me = state.people.some((p) => p.id === saved) ? saved : state.people[0]?.id;
  renderWho();
  setupLog();
  for (const tab of document.querySelectorAll(".tabs button")) tab.addEventListener("click", () => show(tab.dataset.view));
  for (const button of document.querySelectorAll("#view-day .pager button")) {
    button.addEventListener("click", () => ((state.day = shiftDate(state.day, Number(button.dataset.step))), loadDay()));
  }
  for (const button of document.querySelectorAll("#view-week .pager button")) {
    button.addEventListener("click", () => ((state.week = shiftDate(state.week, Number(button.dataset.step))), loadWeek()));
  }
  const savedView = store("fatties.view");
  show(["log", "day", "week", "targets"].includes(savedView) ? savedView : "log");
}

init().catch((error) => toast(`Could not start: ${error.message}`));
