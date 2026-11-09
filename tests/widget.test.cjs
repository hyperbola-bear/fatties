const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const source = fs.readFileSync(require.resolve("../fatties/static/Fatties.js"), "utf8");

function host() {
  const files = new Map(), secrets = new Map(), h = { files, secrets, calls: [], text: [] };
  const connection = { origin: "https://fatties.test", account_id: 1, account_name: "Polar", partner_name: "Yeobo",
    token: "a".repeat(43), expires_at: Date.now() / 1000 + 3600, cacheID: "profile-1" };
  secrets.set("fatties.connection.Fatties", JSON.stringify(connection));
  h.connection = connection;
  h.data = { partner: { id: 2, name: "Yeobo" }, updated_at: new Date().toISOString(), meal: {
    id: 7, title: "Chicken rice", kcal: 597, logged_at: new Date().toISOString(),
    url: "https://fatties.test/?meal=7", thumbnail_url: "https://fatties.test/api/widget/partner/latest/thumbnail?meal_id=7" } };
  class Stack {
    addText(text) { h.text.push(text); return new Stack(); }
    addStack() { return new Stack(); } addDate() { return new Stack(); } addImage() { return new Stack(); }
    addSpacer() {} layoutHorizontally() {} layoutVertically() {} centerAlignContent() {}
    applyFillingContentMode() {} applyRelativeStyle() {} setPadding() {} async presentMedium() {}
  }
  class Request {
    constructor(url) { this.url = url; }
    async loadString() { return this.load(false); }
    async loadImage() { return this.load(true); }
    async load(image) {
      h.calls.push({ url: this.url, headers: this.headers, redirect: this.onRedirect() });
      if (h.offline) throw new Error("network unavailable");
      let status = h.status || 200;
      if (image && h.race) { status = 409; h.race = false; }
      this.response = { statusCode: status };
      if (image && status !== 200) throw new Error("not an image");
      return image ? "thumbnail bytes" : JSON.stringify(status === 200 ? h.data : { detail: "unavailable" });
    }
  }
  const fm = {
    libraryDirectory: () => "/private", joinPath: (a, b) => a + "/" + b,
    fileExists: (p) => files.has(p), createDirectory: (p) => files.set(p, null),
    readString: (p) => { if (!files.has(p)) throw new Error("missing"); return files.get(p); },
    readImage: (p) => { if (!files.has(p)) throw new Error("missing"); return files.get(p); },
    writeString: (p, data) => files.set(p, data), writeImage: (p, data) => files.set(p, data),
    remove: (p) => { for (const name of files.keys()) if (name === p || name.startsWith(p + "/")) files.delete(name); },
    listContents: (p) => [...files.keys()].filter((name) => name.startsWith(p + "/") && !name.slice(p.length + 1).includes("/")).map((name) => name.slice(p.length + 1)),
  };
  h.globals = {
    FileManager: { local: () => fm }, Keychain: { contains: (k) => secrets.has(k), get: (k) => secrets.get(k), set: (k, v) => secrets.set(k, v), remove: (k) => secrets.delete(k) },
    Script: { name: () => "Fatties", setWidget: (w) => { h.widget = w; }, complete() {} },
    Request, ListWidget: Stack, Size: class {}, Color: class { static dynamic(a) { return a; } },
    Font: { systemFont() {}, boldSystemFont() {}, semiboldSystemFont() {} }, SFSymbol: { named: () => ({ image: "symbol" }) },
    config: { runsInApp: false, runsInWidget: true },
  };
  h.run = async () => { h.calls = []; h.text = []; await new AsyncFunction(...Object.keys(h.globals), source)(...Object.values(h.globals)); };
  return h;
}

test("Scriptable fetches authorized JSON and matching thumbnail, then caches for offline", async () => {
  const h = host(); await h.run();
  assert(h.text.includes("Chicken rice")); assert.equal(h.widget.url, "https://fatties.test/?meal=7");
  assert.equal(h.calls.length, 2);
  assert(h.calls.every((call) => call.headers.Authorization === "Bearer " + h.connection.token && call.redirect === null));
  h.offline = true; await h.run();
  assert(h.text.includes("Chicken rice")); assert(h.text.includes("Offline · Updated "));
});
test("revocation clears cached photo and meal even after a previous successful refresh", async () => {
  const h = host(); await h.run(); h.status = 401; await h.run();
  assert(!h.text.includes("Chicken rice")); assert.equal(h.secrets.size, 0);
  assert(![...h.files.keys()].some((p) => p.includes("profile-1")));
  assert(h.text.some((text) => text.includes("disconnected")));
});
test("expired credentials clear local content even offline", async () => {
  const h = host(); await h.run();
  h.connection.expires_at = 1; h.secrets.set("fatties.connection.Fatties", JSON.stringify(h.connection));
  h.offline = true; await h.run();
  assert.equal(h.calls.length, 0); assert(!h.text.includes("Chicken rice")); assert.equal(h.secrets.size, 0);
});
test("successful empty result clears a previous meal and its image", async () => {
  const h = host(); await h.run(); h.data.meal = null; await h.run();
  assert(h.text.includes("No meals logged yet")); assert(!h.text.includes("Chicken rice"));
  assert(![...h.files.keys()].some((p) => p.endsWith(".jpg")));
});
test("thumbnail race refetches the JSON rather than pairing the wrong image", async () => {
  const h = host(); h.race = true; await h.run();
  assert.equal(h.calls.length, 4); assert(h.text.includes("Chicken rice"));
});
