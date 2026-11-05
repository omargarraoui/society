"use strict";

const MODELS = { groq: "Groq", gemini: "Gemini", cloudflare: "Cloudflare" };
const PENALTIES = {
  warning: "a warning", mark: "a mark", restitution: "paying back what they stole", fine: "a fine",
  no_children: "no children", lose_role: "losing their role", exile: "exile", death: "death",
};
const FILTERS = [
  ["all", "Everything"], ["words", "Words"], ["god", "God"], ["promises", "Promises"],
  ["family", "Family"], ["food", "Food"],
];
const FILTER_OF = {
  speak: "words", pray: "words", petition: "words",
  revelation: "god", appoint: "god", sentence: "god", pardon: "god", penalty: "god", child_denied: "god",
  anomaly: "god",
  promise: "promises", assess: "promises", observe: "promises",
  propose_marriage: "family", accept_marriage: "family", request_child: "family", birth: "family",
  give: "food", steal: "food", starve: "food",
};
const PAGE = 60;
const LIVE_URL = "https://omar-society.spaincentral.cloudapp.azure.com/events";
const WIRE_LENGTH = 80;

let data, people, filter = "all", shown = PAGE;

// Every string from the world goes in as text, never as HTML: the agents' words are written by an LLM.
function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (key === "style") node.style.cssText = value;
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child !== null && child !== undefined && child !== false) node.append(child);
  }
  return node;
}

const cap = (name) => name.charAt(0).toUpperCase() + name.slice(1);
const modelVar = (model) => `--model: var(--${model})`;

function who(name) {
  if (name === "god") return el("span", { class: "who god-name" }, "God");
  if (name === "all") return el("span", { class: "who" }, "everyone");
  const person = people[name];
  return el("span", { class: "who", style: person ? modelVar(person.model) : "" },
    el("span", { class: "mark", "aria-hidden": "true" }), cap(name));
}

function when(tick) {
  const c = data.calendar;
  const day = Math.floor(tick / c.ticks_per_day);
  const minutes = Math.round((tick % c.ticks_per_day) * 1440 / c.ticks_per_day);
  const weekday = (day % c.days_per_week) + 1;
  return {
    day, week: Math.floor(day / c.days_per_week) + 1, weekday, rest: weekday === c.rest_day,
    time: `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`,
  };
}

const quote = (text) => el("span", { class: "quote", lang: "en" }, `“${text.trim()}”`);
const times = (n, one, many) => `${n} ${n === 1 ? one : many}`;

function sentence(e) {
  const d = e.data;
  const A = who(e.agent);
  const B = d.target ? who(d.target) : null;
  switch (e.kind) {
    case "speak": return [A, " to ", B, quote(d.message)];
    case "pray": return [A, " prayed", quote(d.message)];
    case "petition": return [A, " asked God, for everyone", quote(d.message)];
    case "promise": return [A, " promised", quote(d.message)];
    case "give": return [A, ` gave ${d.amount} food to `, B];
    case "steal": return d.taken > 0 ? [A, ` stole ${d.taken} food from `, B]
      : [A, " tried to steal from ", B, ", who had nothing"];
    case "propose_marriage": return [A, " asked ", B, " to marry them"];
    case "accept_marriage": return [A, " and ", B, " got married"];
    case "assess": return [A, " judged that ", B, d.kept ? " kept a promise" : " broke a promise"];
    case "observe": return [A, " watched ", B];
    case "request_child": return [A, " asked God for a child with ", B];
    case "revelation": return [A, " to ", B, quote(d.message)];
    case "appoint": return [A, " made ", B, " the representative"];
    case "sentence": {
      const extra = d.days ? ` for ${d.days} days` : d.amount ? ` (${d.amount} food)` : "";
      return [A, " sentenced ", B, ` to ${PENALTIES[d.penalty]}${extra}`, quote(d.reason)];
    }
    case "pardon": return ["The sentence on ", B, " was cancelled"];
    case "penalty": return ["The sentence on ", B, ` was carried out: ${PENALTIES[d.penalty]}`];
    case "child_denied": return [A, " said no to a child for ", B, quote(d.reason)];
    case "birth": return [A, " was born to ", who(d.parents[0]), " and ", who(d.parents[1])];
    case "starve": return [A, " died of hunger"];
    case "work": return [A, " worked"];
    case "rest": return [A, " rested"];
    case "skip": return [A, ` missed a turn (${d.reason})`];
    case "invalid": return [A, ` tried something the rules don't allow: ${d.action} (${d.reason})`];
    case "anomaly": return [A, "'s decision was blocked and the world paused: ", d.reason];
    default: return [A, ` ${e.kind}`];
  }
}

function renderNow() {
  const t = when(data.tick);
  const minutesAgo = Math.max(0, Math.round((Date.now() - Date.parse(data.updated)) / 60000));
  const ago = minutesAgo < 60 ? `${minutesAgo} min` : `${Math.round(minutesAgo / 60)} h`;
  const parts = [
    `World week ${t.week}, day ${t.weekday}, ${t.time}`,
    t.rest && el("span", { class: "flag" }, "Day of rest"),
    data.paused && el("span", { class: "flag paused" }, "Paused"),
    `Updated ${ago} ago`,
  ].filter(Boolean);
  document.getElementById("now").replaceChildren(
    ...parts.flatMap((part, i) => (i ? [" · ", part] : [part])));
  for (const span of document.querySelectorAll("[data-rule]")) span.textContent = data.rules[span.dataset.rule];
  const realMinutesPerWorldHour = Math.round(60 / data.speed);
  document.getElementById("speed").textContent = `: right now about ${data.speed.toFixed(1)} world days pass in `
    + `one real day, so a world hour lasts about ${realMinutesPerWorldHour} real minutes`;
  document.getElementById("updated").textContent =
    `Data from ${new Date(data.updated).toLocaleString("en-GB")} (world: ${data.world}, turn ${data.tick}).`;
}

function renderStats() {
  const alive = data.people.filter((p) => p.alive);
  const promises = data.people.flatMap((p) => p.promises);
  const tiles = [
    [alive.length, `alive${alive.some((p) => p.child) ? `, ${alive.filter((p) => p.child).length} children` : ""}`],
    [promises.length, promises.length === 1 ? "promise made" : "promises made"],
    [data.totals.steal || 0, "thefts"],
    [data.totals.accept_marriage || 0, "marriages"],
    [data.totals.birth || 0, "births"],
    [data.people.length - alive.length, "deaths"],
  ];
  document.getElementById("stats").replaceChildren(...tiles.flatMap(([n, label], i) => [
    // The spaces around the separator are where a narrow screen can wrap the line.
    ...(i ? [" ", el("span", { class: "sep", "aria-hidden": "true" }, "|"), " "] : []),
    el("span", { class: "stat" }, el("b", {}, String(n)), label)]));
}

function renderPeople() {
  document.getElementById("people").replaceChildren(...data.people.map((p) => {
    const facts = [
      `${p.sex === "M" ? "Man" : "Woman"}, ${p.food} food`,
      p.spouse ? ["Married to ", who(p.spouse)] : null,
      p.parents.length ? ["Child of ", who(p.parents[0]), " and ", who(p.parents[1])] : null,
      p.child ? "Still a child" : null,
      `${times(p.promises.length, "promise", "promises")}`,
      `Worked ${times(p.did.work || 0, "hour", "hours")}, rested ${p.did.rest || 0}, `
        + `spoke ${times((p.did.speak || 0) + (p.did.pray || 0) + (p.did.petition || 0), "time", "times")}`,
    ].filter(Boolean);
    const tags = [
      p.role ? el("span", { class: "tag god" }, `The ${p.role}`) : null,
      p.exiled ? el("span", { class: "tag bad" }, "In exile") : null,
      !p.alive ? el("span", { class: "tag bad" }, "Dead") : null,
      ...p.warnings.map((w) => el("span", { class: "tag bad", title: w }, "Warned")),
      ...p.marks.map((m) => el("span", { class: "tag bad", title: m }, "Marked")),
    ];
    return el("article", { class: `person${p.alive ? "" : " dead"}` },
      el("h3", {}, who(p.name), el("span", { class: "model" }, MODELS[p.model]), ...tags),
      el("ul", { class: "facts" }, ...facts.map((f) => el("li", {}, f))));
  }));
}

function renderFilters() {
  document.getElementById("filters").replaceChildren(...FILTERS.map(([key, label]) => {
    const button = el("button", { type: "button", "aria-pressed": String(filter === key) }, label);
    button.addEventListener("click", () => { filter = key; shown = PAGE; renderFilters(); renderFeed(); });
    return button;
  }));
}

function renderFeed() {
  const events = data.feed.filter((e) => filter === "all" || FILTER_OF[e.kind] === filter);
  const items = [];
  let lastDay = null;
  for (const e of events.slice(0, shown)) {
    const t = when(e.tick);
    if (t.day !== lastDay) {
      items.push(el("li", { class: "day" },
        `World week ${t.week}, day ${t.weekday}${t.rest ? " (day of rest)" : ""}`));
      lastDay = t.day;
    }
    items.push(el("li", { class: `event${e.agent === "god" ? " god" : ""}` },
      el("time", {}, t.time), el("p", {}, ...sentence(e))));
  }
  document.getElementById("feed").replaceChildren(
    ...(items.length ? items : [el("li", { class: "empty" }, "Nothing here yet.")]));
  const more = document.getElementById("more");
  more.hidden = events.length <= shown;
}

function renderPromises() {
  const all = data.people.flatMap((p) => p.promises.map((promise) => ({ ...promise, by: p.name })))
    .sort((a, b) => b.tick - a.tick);
  document.getElementById("promises").replaceChildren(...(all.length ? all.map((p) => {
    const record = p.refrains
      ? (p.state === "broken"
        ? el("span", { class: "ko" }, `Record: broken ${p.violations} ${p.violations === 1 ? "time" : "times"}`)
        : el("span", { class: "ok" }, "Record: kept so far"))
      : el("span", {}, "The code can't check this one");
    const t = when(p.tick);
    return el("article", { class: "promise" },
      el("div", {}, who(p.by), el("span", { class: "model" }, `  week ${t.week}, day ${t.weekday}, ${t.time}`)),
      el("p", { class: "body", lang: "en" }, `“${p.body.trim()}”`),
      el("div", { class: "verdicts" }, record,
        el("span", {}, p.judged_kept + p.judged_broken
          ? `Others judged it kept ${p.judged_kept}×, broken ${p.judged_broken}×` : "Nobody has judged it yet")));
  }) : [el("p", { class: "empty" }, "No promises yet.")]));
}

function renderGod() {
  const words = data.feed.filter((e) => e.kind === "revelation").slice(0, 5);
  const decisions = data.decisions.slice(-8).reverse();
  const representative = data.people.find((p) => p.role === "representative");
  const status = { applied: "done", pending: "waiting 24 h", vetoed: "cancelled", moot: "no longer applies" };
  const day = (tick) => { const t = when(tick); return `week ${t.week}, day ${t.weekday}`; };
  document.getElementById("god").replaceChildren(el("div", { class: "god-grid" },
    el("div", { class: "god-words" },
      words.length ? words.map((e) => el("blockquote", { lang: "en" }, `“${e.data.message.trim()}”`,
        el("footer", {}, "To ", e.data.target === "all" ? "everyone" : cap(e.data.target), `, ${day(e.tick)}`)))
        : el("p", { class: "empty" }, "God hasn't spoken yet.")),
    el("div", { class: "god-side" },
      el("div", {}, el("h3", {}, "Representative"),
        el("p", {}, representative ? [who(representative.name), " speaks to God for everyone."]
          : "Nobody yet. God picks one at dawn.")),
      el("div", {}, el("h3", {}, "Penalties"),
        decisions.length ? el("ul", {}, ...decisions.map((d) => el("li", {}, who(d.target),
          `: ${PENALTIES[d.kind]} (${status[d.status]})`, quote(d.reason))))
          : el("p", { class: "empty" }, "None so far.")))));
}

function renderSegregation() {
  const target = document.getElementById("segregation");
  const s = data.segregation;
  const groups = Object.entries(data.groups).filter(([, n]) => n >= 2).map(([g]) => g);
  if (!groups.length || s.in_group_ratio === null) {
    const sizes = Object.entries(data.groups).map(([g, n]) => `${n} on ${MODELS[g]}`).join(", ");
    target.replaceChildren(el("p", { class: "note" },
      `Not measurable yet. It needs at least two living people on the same model, and right now there are ${sizes}. `
      + "Children inherit a parent's model, so this changes as families grow."));
    return;
  }
  const values = groups.map((g) => [g, s.ratio[`${g}->${g}`] ?? 0]);
  const max = Math.max(2, ...values.map(([, v]) => v));
  const tip = el("div", { class: "tip", hidden: "" });
  const rows = values.map(([g, v]) => {
    const row = el("div", { class: "bar-row", style: modelVar(g), tabindex: "0" },
      el("span", { class: "label" }, MODELS[g]),
      el("div", { class: "track" },
        el("div", { class: "bar", style: `width: ${(v / max) * 100}%` }),
        el("div", { class: "base", style: `left: ${(1 / max) * 100}%` })),
      el("span", { class: "value" }, `${v.toFixed(2)}×`));
    const text = `${MODELS[g]} people chose other ${MODELS[g]} people ${v.toFixed(2)} times as often as `
      + "picking at random would.";
    const show = (x, y) => { tip.textContent = text; tip.hidden = false; tip.style.left = `${x + 12}px`;
      tip.style.top = `${y + 12}px`; };
    row.addEventListener("mousemove", (ev) => show(ev.clientX, ev.clientY));
    row.addEventListener("mouseleave", () => { tip.hidden = true; });
    row.addEventListener("focus", () => { const r = row.getBoundingClientRect(); show(r.left, r.bottom); });
    row.addEventListener("blur", () => { tip.hidden = true; });
    return row;
  });
  const marriages = Object.entries(s.marriages);
  const facts = [
    `Overall, people pick someone on their own model ${s.in_group_ratio.toFixed(2)}× as often as chance.`,
    s.trust_gap === null ? "Not enough judgments yet to compare trust."
      : `When people judge a promise by someone on their own model, they call it kept `
        + `${Math.abs(s.trust_gap * 100).toFixed(0)}% ${s.trust_gap >= 0 ? "more" : "less"} often than when they `
        + "judge someone on another model.",
    marriages.length ? `Marriages: ${s.in_group_marriages} within a model, ${s.all_marriages - s.in_group_marriages} across.`
      : "No marriages yet.",
  ];
  const table = el("table", {}, el("tr", {}, el("th", {}, "From → to"), el("th", {}, "× chance")),
    ...Object.entries(s.ratio).map(([pair, v]) => el("tr", {},
      el("td", {}, pair.split("->").map((g) => MODELS[g]).join(" → ")), el("td", {}, v.toFixed(2)))));
  target.replaceChildren(
    el("div", {},
      el("h3", { class: "viz-title" }, "Choosing their own model (talking, giving, proposing, judging, watching)"),
      el("div", { class: "viz" }, ...rows),
      el("p", { class: "axis-note" }, "Dashed line = 1×, what random choice would give. Right of it = own kind preferred.")),
    el("ul", { class: "how" }, ...facts.map((f) => el("li", {}, f))),
    el("details", {}, el("summary", {}, "All the numbers"), table),
    tip);
}

function render() {
  people = Object.fromEntries(data.people.map((p) => [p.name, p]));
  renderNow(); renderStats(); renderPeople(); renderFilters(); renderFeed(); renderPromises(); renderGod();
  renderSegregation();
}

document.getElementById("more").addEventListener("click", () => { shown += PAGE; renderFeed(); });

// The live mirror needs the people (for names and colors), so it starts after data.json has loaded.
function startLive() {
  const wire = document.getElementById("wire");
  const status = document.getElementById("live-status");
  const add = (item) => {
    wire.prepend(item);
    while (wire.children.length > WIRE_LENGTH) wire.lastChild.remove();
  };
  let now = "";
  const source = new EventSource(LIVE_URL);
  source.onopen = () => { status.textContent = `connected${now}`; status.className = "live-status on"; };
  source.onerror = () => { status.textContent = "reconnecting…"; status.className = "live-status"; };
  // The server announces a tick once the previous one is done, so the time sits in the status, and each line
  // carries the time of its own event.
  source.addEventListener("tick", (message) => {
    const t = when(JSON.parse(message.data).tick);
    now = ` · world day ${t.day + 1}, ${t.time}`;
    status.textContent = `connected${now}`;
  });
  source.addEventListener("world", (message) => {
    const e = JSON.parse(message.data);
    const t = when(e.tick);
    add(el("li", { class: ["work", "rest"].includes(e.kind) ? "routine" : "" },
      el("time", {}, `day ${t.day + 1} ${t.time}`), ...sentence(e)));
  });
}

fetch(`data.json?t=${Date.now()}`)
  .then((response) => {
    if (!response.ok) throw new Error(`data.json: HTTP ${response.status}`);
    return response.json();
  })
  .then((json) => { data = json; render(); startLive(); })
  .catch((error) => {
    document.getElementById("now").textContent = "Couldn't load the data. Try again in a minute.";
    console.error(error);
  });
