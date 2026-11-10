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

const state = { config: null, people: [], me: null, view: "log", day: null, period: "day", estimate: null, photo: null };
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
    // A session that ran out or was signed out by a password change: back to the login screen.
    if (response.status === 401 && path !== "/api/login") showLogin(detail);
    throw Object.assign(new Error(message), { status: response.status, detail });
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

function longDate(iso) {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

function planLine(plan) {
  if (!plan) return null;
  const parts = [`Plan day ${plan.day_number}`];
  if (plan.weight_kg && plan.target_weight_kg) parts.push(`${plan.weight_kg} → ${plan.target_weight_kg} kg`);
  if (plan.finish_on) parts.push(`goal around ${longDate(plan.finish_on)}`);
  return el("p", { class: "small" }, parts.join(" · "));
}

function noPlanText(person) {
  return person.plan ? "This day is before the plan started." : person.id === state.me
    ? "No plan yet. Enter your current and target weight in Plan." : "No plan yet. Your partner can set one from their own account.";
}

function dayCard(entry) {
  const { person, consumed, target, meals } = entry;
  const color = personColor(state.people.find((p) => p.id === person.id));
  const head = el("div", { class: "person-head" }, el("span", { class: "dot", style: `background:${color}` }), el("h3", {}, person.name));
  const mealList = meals.length ? el("ul", { class: "meals" }, meals.map(mealRow)) : el("p", { class: "empty" }, "Nothing logged.");
  if (!target) {
    return el(
      "article",
      { class: "card" },
      head,
      el("div", { class: "hero" }, el("strong", {}, fmt(consumed.kcal)), el("span", {}, "kcal eaten")),
      el("p", { class: "small" }, NUTRIENTS.slice(1).map((n) => `${n.label} ${amount(consumed[n.key], n.unit)}`).join(" · ")),
      el("p", { class: "small" }, noPlanText(person)),
      mealList,
    );
  }
  const left = target.kcal - consumed.kcal;
  return el(
    "article",
    { class: "card" },
    head,
    planLine(entry.plan),
    el(
      "div",
      { class: "hero" },
      el("strong", { class: left < 0 ? "over-text" : null }, fmt(Math.abs(left))),
      el("span", {}, left < 0 ? `kcal over the ${fmt(target.kcal)} target` : `kcal left of ${fmt(target.kcal)}`),
    ),
    el("div", { class: "bars" }, NUTRIENTS.map((n) => progressRow(n, consumed[n.key], target[n.key], color))),
    mealList,
  );
}


// ---------- plan ----------

const PACES = [
  [0.25, "Gentle, 0.25 kg a week"],
  [0.5, "Steady, 0.5 kg a week"],
  [0.75, "Faster, 0.75 kg a week"],
  [1, "Fast, 1 kg a week"],
];
const EXERCISE = [
  ["sedentary", "Little exercise"],
  ["light", "1–3 times a week"],
  ["moderate", "3–5 times a week"],
  ["active", "6–7 times a week"],
];

function options(pairs, selected) {
  return pairs.map(([value, label]) => el("option", { value, selected: String(value) === String(selected) }, label));
}

function currentPlan(person) {
  const plan = person.plan;
  if (!plan) return el("p", { class: "small" }, person.id === state.me ? "No plan yet. Fill this in and start one; Track comparisons begin from that day." : "Your partner has not started a plan yet.");
  const d = plan.details;
  const t = plan.targets;
  return el(
    "div",
    { class: "plan-now" },
    el("strong", {}, `Plan since ${longDate(plan.starts_on)}`),
    el("span", {}, `${d.weight_kg} → ${d.target_weight_kg} kg at about ${d.pace_kg_per_week} kg a week, goal around ${longDate(d.finish_on)}.`),
    el("span", {}, `Daily: ${fmt(t.kcal)} kcal · carbs ${fmt(t.carbs_g)}g · protein ${fmt(t.protein_g)}g · fat ${fmt(t.fat_g)}g · sugar under ${fmt(t.sugar_g)}g${d.adjusted ? " (adjusted by hand)" : ""}.`),
  );
}

function planCard(person) {
  if (person.id !== state.me) return el("article", { class: "card" }, el("h3", {}, person.name), currentPlan(person), el("p", { class: "small" }, "Your partner's plan is read-only."));
  const color = personColor(person);
  const last = person.plan?.details || {};
  const status = el("p", { class: "status", role: "status" });
  const result = el("div", { class: "plan-result" });
  const number = (value, attrs = {}) => el("input", { type: "number", inputmode: "decimal", value: value ?? "", ...attrs });
  const form = {
    weight_kg: number(last.weight_kg, { step: 0.1, placeholder: "kg" }),
    target_weight_kg: number(last.target_weight_kg, { step: 0.1, placeholder: "kg" }),
    height_cm: number(last.height_cm, { step: 1, placeholder: "cm" }),
    age: number(last.age, { step: 1, inputmode: "numeric" }),
    sex: el("select", {}, options([["female", "Female"], ["male", "Male"]], last.sex)),
    activity: el("select", {}, options(EXERCISE, last.activity || "moderate")),
    pace_kg_per_week: el("select", {}, options(PACES, last.target_date ? 0.5 : last.pace_kg_per_week || 0.5)),
    target_date: el("input", { type: "date", value: last.target_date || "", min: shiftDate(state.config.today, 7) }),
  };
  const inputs = () => {
    const body = {};
    for (const [key, node] of Object.entries(form)) {
      if (node.value === "") continue;
      body[key] = ["sex", "activity", "target_date"].includes(key) ? node.value : Number(node.value);
    }
    return body;
  };
  const fail = (error) => {
    status.className = "status error";
    status.textContent = error.message;
  };

  const workOut = async () => {
    status.className = "status";
    status.textContent = "";
    let preview;
    try {
      preview = await api("/api/plan/preview", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(inputs()) });
    } catch (error) {
      result.replaceChildren();
      return fail(error);
    }
    const p = preview.plan;
    const fields = {};
    const field = (n) => {
      fields[n.key] = el("input", { type: "number", inputmode: "numeric", min: 0, step: 1, value: preview.targets[n.key] });
      return el("label", { class: "grow", style: "flex-basis:96px" }, `${n.label}${n.unit ? " g" : " kcal"}${n.goal ? ", at least" : ", at most"}`, fields[n.key]);
    };
    const start = async () => {
      const targets = Object.fromEntries(NUTRIENTS.map((n) => [n.key, Number(fields[n.key].value)]));
      try {
        const updated = await api(`/api/people/${person.id}/plan`, {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ inputs: inputs(), targets }),
        });
        Object.assign(person, updated);
      } catch (error) {
        return fail(error);
      }
      toast(`${person.name}'s plan starts today`);
      loadTargets();
    };
    result.replaceChildren(
      el(
        "div",
        { class: "plan-preview" },
        el("div", { class: "hero" }, el("strong", {}, fmt(preview.targets.kcal)), el("span", {}, "kcal a day")),
        el(
          "p",
          { class: "small" },
          `You burn about ${fmt(p.maintenance_kcal)} kcal a day. Eating ${fmt(p.deficit_kcal)} less loses about ${p.pace_kg_per_week} kg a week, ` +
            `so ${p.weight_kg} → ${p.target_weight_kg} kg takes about ${Math.ceil(p.weeks)} weeks: around ${longDate(p.finish_on)}.`,
        ),
        p.notes.length ? el("ul", { class: "small notes" }, p.notes.map((note) => el("li", {}, note))) : null,
        el("p", { class: "small" }, "Daily targets. You can adjust them before starting."),
        el("div", { class: "row" }, NUTRIENTS.map(field)),
        el("button", { class: "primary", onclick: start }, person.plan ? "Start new plan from today" : "Start plan today"),
        person.plan ? el("p", { class: "small" }, "Days before today keep the old plan's targets.") : null,
      ),
    );
    result.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };

  const label = (text, node, extra = {}) => el("label", { class: "grow", ...extra }, text, node);
  return el(
    "article",
    { class: "card" },
    el("div", { class: "person-head" }, el("span", { class: "dot", style: `background:${color}` }), el("h3", {}, person.name)),
    currentPlan(person),
    el("div", { class: "row" }, label("Current weight (kg)", form.weight_kg), label("Target weight (kg)", form.target_weight_kg)),
    el("div", { class: "row" }, label("Height (cm)", form.height_cm), label("Age", form.age), label("Sex", form.sex)),
    el("div", { class: "row" }, label("Exercise", form.activity), label("Pace", form.pace_kg_per_week)),
    label("Reach it by (optional, replaces pace)", form.target_date),
    el("button", { class: "ghost", onclick: workOut }, "Work out my targets"),
    status,
    result,
    el("p", { class: "small" }, "Protein 1.8 g per kg of target weight, fat about 27% of calories, sugar under 10% (WHO), carbs the rest. Pace is capped at 1% of body weight a week. A starting point, not medical advice."),
  );
}

async function loadTargets() {
  state.people = await api("/api/people");
  if (state.view !== "targets") return;
  $("target-people").replaceChildren(...state.people.map(planCard));
}


// ---------- identity and meals ----------
function renderWho() {
  const person = state.people.find((p) => p.id === state.me);
  $("who").replaceChildren(el("span", { class: "small" }, person ? `Logged in as ${person.name}` : ""));
  $("who").hidden = false;
}

function mealRow(meal) {
  const t = meal.totals;
  return el("li", { class: "meal" },
    el("a", { class: "title", href: `/?meal=${meal.id}`, onclick: (e) => { e.preventDefault(); openMeal(meal.id); } }, meal.title),
    el("span", { class: "meta" }, [meal.eaten_at.slice(11, 16), meal.meal_type].filter(Boolean).join(" · ")),
    el("span", { class: "macros" }, `${fmt(t.kcal)} kcal · C ${fmt(t.carbs_g)}g · P ${fmt(t.protein_g)}g · F ${fmt(t.fat_g)}g · S ${fmt(t.sugar_g)}g`),
    meal.person_id === state.me ? el("button", { "aria-label": `Delete ${meal.title}`, onclick: async () => {
      if (!confirm(`Delete "${meal.title}" and its photo?`)) return;
      try { await api(`/api/meals/${meal.id}`, { method: "DELETE" }); toast("Meal deleted"); loadTrack(); }
      catch (error) { toast(error.message); }
    } }, "Delete") : null);
}

async function openMeal(id) {
  show("meal");
  history.replaceState(null, "", `/?meal=${id}`);
  $("meal-detail").replaceChildren(el("p", { class: "status" }, "Loading meal…"));
  try {
    const meal = await api(`/api/meals/${id}`);
    if (state.view !== "meal") return;
    const node = el("article", { class: "card" }, el("h2", {}, meal.title),
      el("p", { class: "small" }, `${meal.person.name} · Eaten ${longDate(meal.eaten_on)} at ${meal.eaten_at.slice(11, 16)}`),
      meal.has_photo ? el("img", { class: "meal-photo", src: `/api/meals/${id}/photo`, alt: meal.title,
        onerror: (e) => { e.target.replaceWith(el("p", { class: "small" }, "Photo temporarily unavailable.")); } })
        : el("p", { class: "photo-placeholder" }, "No photo saved for this meal"),
      el("p", {}, NUTRIENTS.map((n) => `${n.label} ${amount(meal.totals[n.key], n.unit)}`).join(" · ")),
      meal.note ? el("p", { class: "small" }, meal.note) : null,
      el("ul", { class: "notes" }, meal.items.map((item) => el("li", {}, `${item.name} · ${item.portion} · ${fmt(item.kcal)} kcal`))));
    if (meal.person_id === state.me) node.append(el("button", { class: "ghost", onclick: async () => {
      if (!confirm(`Delete "${meal.title}" and its photo?`)) return;
      try { await api(`/api/meals/${id}`, { method: "DELETE" }); show("track"); }
      catch (error) { toast(error.message); }
    } }, "Delete my meal"));
    $("meal-detail").replaceChildren(node);
  } catch (error) {
    if (error.status !== 401) $("meal-detail").replaceChildren(el("p", { class: "card" }, error.message));
  }
}

// ---------- Track ----------
let trackRequest = 0;
function periodDate(iso, period, direction) {
  const d = new Date(`${iso}T00:00:00Z`);
  if (period === "day" || period === "week") return shiftDate(iso, direction * (period === "week" ? 7 : 1));
  d.setUTCDate(1);
  if (period === "month") d.setUTCMonth(d.getUTCMonth() + direction);
  else { d.setUTCMonth(0); d.setUTCFullYear(d.getUTCFullYear() + direction); }
  return d.toISOString().slice(0, 10);
}

async function loadTrack() {
  const request = ++trackRequest;
  const date = state.day, period = state.period;
  $("track-date").value = date;
  $("track-period").value = period;
  $("track-status").textContent = "Loading…";
  $("track-people").replaceChildren();
  $("track-compare").hidden = true;
  try {
    const data = await api(`/api/track?period=${period}&date=${date}`);
    if (request !== trackRequest || state.view !== "track") return;
    $("track-label").textContent = period === "day" ? dateLabel(date) : period === "year" ? date.slice(0, 4) :
      period === "month" ? new Date(`${date}T00:00:00Z`).toLocaleDateString(undefined, { month: "long", year: "numeric", timeZone: "UTC" }) :
      `${longDate(data.start)} – ${longDate(data.end)}`;
    $("track-status").textContent = "";
    $("track-people").replaceChildren(...data.people.map((entry) => period === "day" ? dayCard(entry) : periodCard(entry, data)));
    if (period !== "day") renderCompare(data);
  } catch (error) {
    if (request === trackRequest) $("track-status").textContent = error.message;
  }
}

function barPath(x, yTop, width, height, radius, fill) {
  if (height <= 0) return null;
  const r = Math.min(radius, height, width / 2);
  return svg("path", { d: `M${x},${yTop + height} V${yTop + r} Q${x},${yTop} ${x + r},${yTop} H${x + width - r} Q${x + width},${yTop} ${x + width},${yTop + r} V${yTop + height} Z`, fill, "pointer-events": "none" });
}

function periodChart(entry, period) {
  const color = personColor(state.people.find((p) => p.id === entry.person.id));
  const buckets = period === "year" ? entry.buckets : entry.days;
  const W = 480, H = 180, left = 5, right = 5, top = 12, bottom = 26;
  const max = Math.max(1, ...buckets.map((b) => Math.max(b.consumed.kcal, b.target?.kcal || 0))) * 1.12;
  const y = (v) => top + (H - top - bottom) * (1 - v / max);
  const slot = (W - left - right) / buckets.length, width = Math.min(25, slot * 0.65);
  const base = y(0), nodes = [];
  for (const fraction of [0.5, 1]) nodes.push(svg("line", { class: "grid", x1: left, x2: W - right, y1: y(max * fraction), y2: y(max * fraction) }));
  buckets.forEach((bucket, i) => {
    const x = left + slot * i, cx = x + slot / 2, kcal = bucket.consumed.kcal;
    const target = bucket.target?.kcal, future = bucket.date > state.config.today;
    if (bucket.logged && !future) {
      const within = target == null ? kcal : Math.min(kcal, target);
      nodes.push(barPath(cx - width / 2, y(within), width, base - y(within), 3, color));
      if (target != null && kcal > target) nodes.push(barPath(cx - width / 2, y(kcal), width, y(target) - y(kcal), 3, "var(--over)"));
      if (kcal === 0) nodes.push(svg("circle", { cx, cy: base, r: 2, fill: color }));
    } else if (!future) nodes.push(svg("text", { x: cx, y: base - 5, "text-anchor": "middle" }, "–"));
    if (target != null) nodes.push(svg("line", { class: "target", x1: x + 1, x2: x + slot - 1, y1: y(target), y2: y(target) }));
    const label = period === "year" ? bucket.label : period === "week" ? WEEKDAYS[i] : String(Number(bucket.date.slice(8)));
    if (period !== "month" || i % 3 === 0 || i === buckets.length - 1) nodes.push(svg("text", { x: cx, y: H - 7, "text-anchor": "middle" }, label));
    const lines = [period === "year" ? `${bucket.label} ${bucket.date.slice(0, 4)}` : longDate(bucket.date)];
    if (future) lines.push("Future period");
    else if (!bucket.logged) lines.push("Nothing logged");
    else {
      lines.push(`${fmt(kcal)} kcal${period === "year" ? "/logged day" : ""}${target == null ? " · no target" : ` / target ${fmt(target)}`}`);
      if (period === "year") lines.push(`${bucket.logged_days} logged days · ${fmt(bucket.total.kcal)} kcal total`);
      lines.push(...NUTRIENTS.slice(1).map((n) => `${n.label} ${amount(bucket.consumed[n.key], n.unit)}`));
    }
    nodes.push(svg("rect", { class: "hit", x, y: top, width: slot, height: base - top,
      onpointerenter: (e) => showTip(e, lines), onpointermove: (e) => showTip(e, lines), onpointerleave: hideTip,
      onclick: (e) => showTip(e, lines), tabindex: 0, role: "button", "aria-label": lines.join(". "),
      onfocus: (e) => { const r = e.target.getBoundingClientRect(); showTip({ clientX: r.x, clientY: r.y }, lines); }, onblur: hideTip,
      onkeydown: (e) => { if (e.key === "Escape") hideTip(); } }));
  });
  return svg("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `${entry.person.name}: ${period === "year" ? "monthly average calories per logged day" : "daily calories"}; dashes mean no log` }, nodes);
}

function delta(value, n) {
  if (value == null) return el("span", {}, "–");
  if (Math.abs(value) < 0.5) return el("span", {}, "same");
  const up = value > 0, good = n.goal ? up : !up;
  return el("span", { class: good ? "delta-down" : "delta-up" }, `${up ? "▲" : "▼"} ${amount(Math.abs(value), n.unit)}`);
}

function periodCard(entry, data) {
  const color = personColor(state.people.find((p) => p.id === entry.person.id));
  const average = entry.all_daily_average, target = entry.logged_days ? entry.average_target : null;
  const coverage = `${entry.all_logged_days}/${entry.elapsed_days} days logged`;
  return el("article", { class: "card" },
    el("div", { class: "person-head" }, el("span", { class: "dot", style: `background:${color}` }), el("h3", {}, entry.person.name)),
    planLine(entry.plan),
    el("div", { class: "legend" }, el("span", {}, "Calories"), el("span", { class: "over-text" }, "Red = over target"), el("span", {}, "Dashed = target · – = no log")),
    periodChart(entry, data.period),
    data.period === "year" ? el("p", { class: "small" }, "Each bar is a monthly average per logged day; target uses that month's logged plan days.") : null,
    el("div", { class: "stats" },
      el("div", {}, el("strong", {}, entry.all_logged_days ? fmt(average.kcal) : "–"), el("span", {}, "avg kcal / logged day")),
      el("div", {}, el("strong", {}, `${entry.all_logged_days}/${entry.elapsed_days}`), el("span", {}, "days logged")),
      el("div", {}, el("strong", {}, fmt(entry.all_total.kcal)), el("span", {}, "kcal logged to date"))),
    el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["", "Avg/day", "Plan avg*", "vs previous"].map((text) => el("th", {}, text)))),
      el("tbody", {}, NUTRIENTS.map((n) => el("tr", {}, el("td", {}, n.label),
        el("td", {}, entry.all_logged_days ? amount(average[n.key], n.unit) : "–"),
        el("td", {}, target ? amount(target[n.key], n.unit) : "–"),
        el("td", {}, delta(entry.previous_period.change?.[n.key], n))))))),
    el("p", { class: "small" }, `${coverage}. Missing days are not zero intake. *Plan averages use logged days with a plan; the intake average also includes logs before a plan started.`),
    entry.planned_days_begun ? el("p", { class: "small" },
      `${fmt(entry.total.kcal)} kcal logged against ${fmt(entry.target_to_date.kcal)} budgeted across ${entry.planned_days_begun} elapsed plan days. ` +
      `${entry.days_over_kcal} logged days over calories; ${entry.days_protein_hit} protein goals hit. ` +
      (entry.unlogged_past_days ? `${entry.unlogged_past_days} past plan days have no logs, so the total is incomplete.` : "")) : el("p", { class: "small" }, "No elapsed plan days in this period."),
    el("p", { class: "small" }, `Previous ${data.period}: ${longDate(entry.previous_period.start)} – ${longDate(entry.previous_period.end)} (${entry.previous_period.logged_days} logged days).`));
}

function renderCompare(data) {
  const box = $("track-compare");
  box.hidden = data.people.length < 2;
  box.replaceChildren(el("h3", {}, "Side by side"), el("p", { class: "small" }, "Average intake per logged day. Each person is compared against their own plan above."),
    el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, el("th", {}, ""), data.people.map((p) => el("th", {}, p.person.name)))),
      el("tbody", {}, NUTRIENTS.map((n) => el("tr", {}, el("td", {}, n.label), data.people.map((p) => el("td", {}, p.all_logged_days ? amount(p.all_daily_average[n.key], n.unit) : "–"))))))));
}

// ---------- photo analysis and adjustments ----------
function guessMealType() {
  const h = new Date().getHours();
  return h < 5 ? "supper" : h < 11 ? "breakfast" : h < 15 ? "lunch" : h < 17 ? "snack" : h < 21 ? "dinner" : "supper";
}
async function shrinkPhoto(file) {
  const url = URL.createObjectURL(file);
  try {
    const image = await new Promise((resolve, reject) => { const img = new Image(); img.onload = () => resolve(img); img.onerror = () => reject(new Error("Try a JPEG or another readable photo.")); img.src = url; });
    const ratio = Math.min(1, 1568 / Math.max(image.naturalWidth, image.naturalHeight));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(image.naturalWidth * ratio); canvas.height = Math.round(image.naturalHeight * ratio);
    canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
    if (!blob) throw new Error("Could not prepare the photo.");
    return blob;
  } finally { URL.revokeObjectURL(url); }
}
function localNow() {
  const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 16);
}
function scaled(entry) { return FattiesAdjustments.scaled(entry, state.estimate.mealScale); }
function currentItems() { return state.estimate.items.filter((entry) => !entry.removed).map(scaled); }
let photoVersion = 0;
function setupLog() {
  $("meal-type").replaceChildren(...state.config.meal_types.map((type) => el("option", { value: type }, type)));
  $("meal-type").value = guessMealType();
  $("photo").addEventListener("change", async (e) => {
    const file = e.target.files[0]; if (!file) return;
    const version = ++photoVersion;
    state.estimate = null; state.photo = null; $("estimate").replaceChildren(); $("analyze").disabled = true;
    $("preview").hidden = true;
    try {
      const photo = await shrinkPhoto(file);
      if (version !== photoVersion) return;
      state.photo = photo;
      if (state.previewURL) URL.revokeObjectURL(state.previewURL);
      state.previewURL = URL.createObjectURL(photo); $("preview").src = state.previewURL;
      $("preview").hidden = false; $("photo-hint").hidden = true; $("analyze").disabled = false;
      $("analyze-status").textContent = "";
    } catch (error) { $("analyze-status").textContent = error.message; }
  });
  $("photo-form").addEventListener("submit", async (e) => {
    e.preventDefault(); if (!state.photo) return;
    const version = photoVersion, photo = state.photo, form = new FormData();
    form.append("photo", photo, "meal.jpg"); form.append("meal_type", $("meal-type").value); form.append("note", $("note").value);
    $("analyze").disabled = true; $("analyze-status").textContent = "Estimating…";
    try {
      const result = await api("/api/analyze", { method: "POST", body: form });
      if (version !== photoVersion || state.me == null) return;
      state.estimate = { ...result, photo, mealScale: 1, requestId: crypto.randomUUID(), items: result.items.map(FattiesAdjustments.create) };
      $("analyze-status").textContent = ""; renderEstimate();
      $("estimate").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) { if (version === photoVersion) $("analyze-status").textContent = error.message; }
    finally { if (version === photoVersion && state.photo) $("analyze").disabled = false; }
  });
}

function portionControl(label, value, apply) {
  const output = el("output", {}, `×${value.toFixed(2)}`);
  const range = el("input", { type: "range", min: 0.5, max: 2, step: 0.05, value, "aria-label": label,
    oninput: (e) => { apply(Number(e.target.value)); updateEstimate(); } });
  const shortcuts = SCALES.map((scale) => el("button", { type: "button", "data-scale": scale,
    onclick: () => { apply(scale); updateEstimate(); } }, `×${scale}`));
  return { range, output, shortcuts, node: el("div", { class: "portion-control" },
    el("label", {}, label, range), output, el("div", { class: "scale", role: "group", "aria-label": `${label} shortcuts` }, shortcuts)) };
}
function syncPortion(control, value, removed = false) {
  control.range.value = value; control.range.disabled = removed;
  control.output.textContent = `×${value.toFixed(2)}`;
  control.range.setAttribute("aria-valuetext", `${Math.round(value * 100)} percent of estimated portion`);
  for (const button of control.shortcuts) { button.setAttribute("aria-pressed", String(!removed && Math.abs(Number(button.dataset.scale) - value) < 0.001)); button.disabled = removed; }
}
function renderEstimate() {
  const est = state.estimate;
  if (!est) return $("estimate").replaceChildren();
  if (!est.is_food) return $("estimate").replaceChildren(el("div", { class: "card" }, el("h3", {}, "No food found"), el("p", {}, est.description)));
  const title = el("input", { value: est.meal_title, maxlength: 200, "aria-label": "Meal name", oninput: (e) => { est.meal_title = e.target.value; } });
  est.eaten_at = est.eaten_at || localNow();
  const when = el("input", { type: "datetime-local", value: est.eaten_at, "aria-label": "Eaten at", onchange: (e) => { est.eaten_at = e.target.value; } });
  est.totalNodes = NUTRIENTS.map((n) => el("strong"));
  est.portionUI = portionControl("Whole meal portion", est.mealScale, (value) => { est.mealScale = value; });
  est.saveButton = el("button", { class: "primary", onclick: saveMeal }, `Save for ${state.people.find((p) => p.id === state.me)?.name || "me"}`);
  est.panel = el("fieldset", { class: "card" }, el("label", {}, "Meal", title),
    el("div", { class: "totals", "aria-live": "polite" }, NUTRIENTS.map((n, i) => el("div", {}, est.totalNodes[i], el("span", {}, n.short)))),
    est.portionUI.node, el("p", { class: "small" }, est.description), est.items.map(itemRow),
    el("button", { class: "ghost", onclick: () => { est.mealScale = 1; est.items.forEach(FattiesAdjustments.reset); updateEstimate(); } }, "Reset to estimate"),
    el("p", { class: "small" }, `Confidence: ${est.overall_confidence}. ${[...(est.assumptions || []), ...(est.warnings || [])].join(" ")}`),
    el("label", {}, "Eaten at", when), est.saveButton);
  $("estimate").replaceChildren(est.panel);
  updateEstimate();
}
function itemRow(entry) {
  const ui = { macros: {} }; entry.ui = ui;
  ui.energy = el("span", { class: "macros" }); ui.description = el("p", { class: "small" });
  ui.summary = el("p", { class: "macros" });
  ui.portion = portionControl(`${entry.base.name} portion`, entry.scale, (value) => { entry.scale = value; });
  const controls = NUTRIENTS.slice(1).map((n) => {
    const id = `macro-${state.estimate.items.indexOf(entry)}-${n.key}`;
    const update = (e) => {
      if (e.target.value === "") return;
      FattiesAdjustments.setMacro(entry, n.key, Number(e.target.value), state.estimate.mealScale);
      e.target.value = scaled(entry)[n.key]; updateEstimate();
    };
    const range = el("input", { id, type: "range", min: 0, max: Math.max(100, entry.base[n.key] * 4), step: 0.1,
      "aria-label": `${entry.base.name} ${n.label} grams`, oninput: update });
    const number = el("input", { type: "number", min: 0, max: 10000, step: 0.1, inputmode: "decimal",
      "aria-label": `${entry.base.name} ${n.label} exact grams`, onchange: update });
    ui.macros[n.key] = { range, number };
    return el("div", { class: "macro-control" }, el("label", { for: id }, `${n.label} (g)`), range, number);
  });
  ui.remove = el("button", { class: "ghost", onclick: () => { entry.removed = !entry.removed; updateEstimate(); } }, "Remove");
  ui.node = el("details", { class: "item" }, el("summary", {}, el("span", { class: "name" }, entry.base.name), ui.energy),
    ui.description, ui.summary, ui.portion.node, ...controls,
    el("p", { class: "small" }, "Grams reflect the current portion. Calories update automatically. Sugar is included in carbs."),
    el("div", { class: "row" }, ui.remove, el("button", { class: "ghost", onclick: () => { FattiesAdjustments.reset(entry); updateEstimate(); } }, "Reset this food")));
  return ui.node;
}
function updateEstimate() {
  const est = state.estimate; if (!est?.is_food) return;
  est.panel.disabled = !!est.saving;
  syncPortion(est.portionUI, est.mealScale);
  for (const entry of est.items) {
    const item = scaled(entry), ui = entry.ui;
    ui.energy.textContent = `${fmt(item.kcal)} kcal`; ui.node.classList.toggle("removed", entry.removed);
    ui.description.textContent = `${item.portion} · about ${fmt(item.estimated_weight_g)}g`;
    ui.summary.textContent = NUTRIENTS.slice(1).map((n) => `${n.label} ${amount(item[n.key], n.unit)}`).join(" · ");
    syncPortion(ui.portion, entry.scale, entry.removed); ui.remove.textContent = entry.removed ? "Undo removal" : "Remove";
    for (const n of NUTRIENTS.slice(1)) {
      const { range, number } = ui.macros[n.key];
      range.max = Math.max(Number(range.max), item[n.key]); range.value = item[n.key]; range.disabled = entry.removed;
      range.setAttribute("aria-valuetext", `${item[n.key]} grams`);
      if (document.activeElement !== number) number.value = item[n.key];
      number.disabled = entry.removed;
    }
  }
  const items = currentItems();
  NUTRIENTS.forEach((n, i) => { est.totalNodes[i].textContent = amount(items.reduce((sum, item) => sum + item[n.key], 0), n.unit); });
  est.saveButton.disabled = !items.length || !!est.saving;
}
async function saveMeal() {
  const est = state.estimate; if (!est || est.saving) return;
  const person = state.me;
  const payload = { person_id: person, request_id: est.requestId, eaten_at: est.eaten_at || localNow(),
    meal_type: $("meal-type").value, title: est.meal_title || "Meal", note: $("note").value || null,
    items: est.items.filter((entry) => !entry.removed).map((entry) => FattiesAdjustments.scaled(entry, est.mealScale)), model: est.model };
  est.saving = true; updateEstimate();
  const formControls = [...$("photo-form").querySelectorAll("input, select, button")].map((control) => ({ control, disabled: control.disabled }));
  formControls.forEach(({ control }) => { control.disabled = true; });
  try {
    const encoded = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result.split(",")[1]); reader.onerror = () => reject(new Error("Could not read the photo.")); reader.readAsDataURL(est.photo); });
    await api("/api/meals", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ ...payload, photo_base64: encoded }) });
    if (state.estimate !== est) return;
    state.estimate = null; state.photo = null; ++photoVersion;
    $("photo").value = ""; $("note").value = ""; $("preview").hidden = true; $("photo-hint").hidden = false; $("analyze").disabled = true;
    if (state.previewURL) URL.revokeObjectURL(state.previewURL); state.previewURL = null;
    renderEstimate(); toast("Saved"); state.day = state.config.today; state.period = "day"; show("track");
  } catch (error) { toast(`Not saved: ${error.message}`); }
  finally {
    formControls.forEach(({ control, disabled }) => { control.disabled = disabled; });
    if (!state.photo) $("analyze").disabled = true;
    if (state.estimate === est) { est.saving = false; updateEstimate(); }
  }
}

// ---------- widget setup ----------
async function loadWidget() {
  try {
    const data = await api("/api/widget/credentials");
    $("widget-status").textContent = data.connections.length ? `${data.connections.length} active connection(s). Next expiry: ${new Date(data.connections[0].expires_at * 1000).toLocaleDateString()}.` : "No widgets connected yet.";
  } catch (error) { $("widget-status").textContent = error.message; }
}
function setupWidget() {
  $("widget-connect").addEventListener("click", async () => {
    $("widget-connect").disabled = true;
    try {
      const code = await api("/api/widget/credentials", { method: "POST" });
      $("widget-code").value = JSON.stringify(code); $("widget-code-box").hidden = false;
      $("widget-status").textContent = `Connection for ${code.account_name}, showing ${code.partner_name}. Copy it now, then run the Fatties script in Scriptable.`;
    } catch (error) { $("widget-status").textContent = error.message; }
    finally { $("widget-connect").disabled = false; }
  });
  $("widget-copy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("widget-code").value); toast("Connection code copied"); }
    catch (_) { $("widget-code").focus(); $("widget-code").select(); toast("Select and copy the connection code"); }
  });
  $("widget-disconnect").addEventListener("click", async () => {
    if (!confirm("Disconnect all widgets for your account?")) return;
    try {
      await api("/api/widget/credentials", { method: "DELETE" }); $("widget-code").value = ""; $("widget-code-box").hidden = true;
      $("widget-status").textContent = "Disconnected. Widgets clear saved content on their next successful connection to Fatties.";
    } catch (error) { $("widget-status").textContent = error.message; }
  });
}

// ---------- navigation and login ----------
function show(view) {
  state.view = view;
  if (view !== "meal") { store("fatties.view", view); history.replaceState(null, "", location.pathname); }
  if (view !== "widget") { $("widget-code").value = ""; $("widget-code-box").hidden = true; }
  hideTip();
  for (const tab of document.querySelectorAll(".tabs button")) tab.setAttribute("aria-selected", String(tab.dataset.view === view));
  for (const section of document.querySelectorAll(".view")) section.hidden = section.id !== `view-${view}`;
  const load = { track: loadTrack, targets: loadTargets, widget: loadWidget }[view];
  if (load) Promise.resolve().then(load).catch((error) => toast(error.message));
}
function showLogin(detail) {
  ++trackRequest; ++photoVersion;
  state.me = null; state.people = []; state.estimate = null; state.photo = null;
  if (state.previewURL) URL.revokeObjectURL(state.previewURL); state.previewURL = null;
  for (const id of ["track-people", "track-compare", "target-people", "estimate", "meal-detail"]) $(id).replaceChildren();
  $("preview").removeAttribute("src"); $("preview").hidden = true;
  $("widget-code").value = ""; $("widget-code-box").hidden = true;
  hideTip(); $("who").hidden = true; document.querySelector(".tabs").hidden = true;
  for (const section of document.querySelectorAll(".view")) section.hidden = section.id !== "view-login";
  $("login-status").textContent = detail?.passwords_set === false ? "No passwords are set. Set your account passwords on the server first." : "";
  $("login-name").focus();
}
function setupLogin() {
  $("login-form").addEventListener("submit", async (e) => {
    e.preventDefault(); const button = e.target.querySelector("button"); button.disabled = true;
    try { await api("/api/login", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ name: $("login-name").value, password: $("login-password").value }) }); location.reload(); }
    catch (error) { $("login-status").textContent = error.message; }
    finally { button.disabled = false; }
  });
  $("logout").addEventListener("click", async () => {
    try { await api("/api/logout", { method: "POST" }); location.reload(); }
    catch (error) { toast(`Could not log out: ${error.message}`); }
  });
}
async function init() {
  setupLogin();
  let session;
  try { session = await api("/api/session"); } catch (error) { if (error.status === 401) return; throw error; }
  state.config = await api("/api/config"); state.people = await api("/api/people"); state.me = session.person.id;
  state.day = state.config.today; state.period = ["day", "week", "month", "year"].includes(store("fatties.period")) ? store("fatties.period") : "day";
  $("signed-in-as").textContent = `Logged in as ${session.person.name}.`; document.querySelector(".tabs").hidden = false;
  renderWho(); setupLog(); setupWidget();
  for (const tab of document.querySelectorAll(".tabs button")) tab.addEventListener("click", () => show(tab.dataset.view));
  $("track-period").addEventListener("change", (e) => { state.period = e.target.value; store("fatties.period", state.period); loadTrack(); });
  $("track-date").addEventListener("change", (e) => { if (e.target.value && e.target.validity.valid) { state.day = e.target.value; loadTrack(); } });
  for (const [id, step] of [["track-prev", -1], ["track-next", 1]]) $(id).addEventListener("click", () => {
    const next = periodDate(state.day, state.period, step);
    if (next < "1900-01-01" || next > "2100-12-31") return;
    state.day = next; loadTrack();
  });
  $("track-today").addEventListener("click", () => { state.day = state.config.today; loadTrack(); });
  $("meal-back").addEventListener("click", () => show("track"));
  const meal = new URLSearchParams(location.search).get("meal");
  if (meal && /^\d+$/.test(meal)) await openMeal(Number(meal));
  else {
    const view = store("fatties.view"); show(["log", "track", "targets", "widget"].includes(view) ? view : ["day", "week"].includes(view) ? "track" : "log");
  }
  document.addEventListener("visibilitychange", async () => {
    if (document.hidden || state.me == null) return;
    try { const config = await api("/api/config"); if (state.day === state.config.today) state.day = config.today; state.config = config; if (state.view === "track") loadTrack(); }
    catch (_) { /* The existing page remains useful while offline. */ }
  });
}
init().catch((error) => toast(`Could not start: ${error.message}`));
