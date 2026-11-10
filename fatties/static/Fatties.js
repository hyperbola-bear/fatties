// Fatties partner-meal widget for Scriptable. Contains no account secrets.
// Run inside Scriptable once and paste the connection code from Fatties > Widget.
// Then add a medium Scriptable Home Screen widget and select this script.
// Documentation: https://docs.scriptable.app/listwidget/

const fm = FileManager.local();
const key = "fatties.connection." + Script.name();
const root = fm.joinPath(fm.libraryDirectory(), "Fatties");
if (!fm.fileExists(root)) fm.createDirectory(root, true);

function readConnection() {
  if (!Keychain.contains(key)) return null;
  try { return JSON.parse(Keychain.get(key)); } catch (_) { return null; }
}
function cacheDirectory(connection) { return fm.joinPath(root, connection.cacheID); }
function clearCache(connection) {
  if (!connection) return;
  const path = cacheDirectory(connection);
  if (fm.fileExists(path)) fm.remove(path);
}
function clearConnection(connection) {
  clearCache(connection);
  if (Keychain.contains(key)) Keychain.remove(key);
}
function readCache(connection) {
  try {
    const directory = cacheDirectory(connection);
    const data = JSON.parse(fm.readString(fm.joinPath(directory, "meal.json")));
    const image = data.imageFile ? fm.readImage(fm.joinPath(directory, data.imageFile)) : null;
    return { data: data.payload, image, refreshed: new Date(data.refreshed) };
  } catch (_) { return null; }
}

async function request(connection, path, image) {
  if (path !== connection.origin && !path.startsWith(connection.origin + "/")) throw new Error("Unexpected widget destination");
  const r = new Request(path);
  r.headers = { Authorization: "Bearer " + connection.token, Accept: image ? "image/jpeg" : "application/json" };
  r.timeoutInterval = 6;
  r.onRedirect = () => null; // Never forward a credential to a redirected destination.
  let body;
  try { body = image ? await r.loadImage() : await r.loadString(); }
  catch (error) {
    const status = r.response ? r.response.statusCode : 0;
    const failure = new Error("Widget request failed"); failure.status = status; throw failure;
  }
  const status = r.response ? r.response.statusCode : 0;
  if (status !== 200) { const error = new Error("Widget request failed"); error.status = status; throw error; }
  return image ? body : JSON.parse(body);
}
async function fetchMeal(connection) {
  for (let attempt = 0; attempt < 2; attempt++) {
    const data = await request(connection, connection.origin + "/api/widget/partner/latest", false);
    let image = null;
    if (data.meal && data.meal.thumbnail_url) {
      try { image = await request(connection, data.meal.thumbnail_url, true); }
      catch (error) { if (error.status === 409 && attempt === 0) continue; throw error; }
    }
    const directory = cacheDirectory(connection);
    if (!fm.fileExists(directory)) fm.createDirectory(directory, true);
    const imageFile = image ? "meal-" + data.meal.id + ".jpg" : null;
    if (imageFile) fm.writeImage(fm.joinPath(directory, imageFile), image);
    const refreshed = new Date();
    fm.writeString(fm.joinPath(directory, "meal.json"), JSON.stringify({ payload: data, refreshed: refreshed.toISOString(), imageFile }));
    for (const name of fm.listContents(directory)) {
      if (name !== "meal.json" && name !== imageFile) fm.remove(fm.joinPath(directory, name));
    }
    return { data, image, refreshed };
  }
  throw new Error("Meal changed while loading; retry later");
}

async function connect(previous) {
  const prompt = new Alert();
  prompt.title = "Connect Fatties";
  prompt.message = "In the Fatties web app, open Widget, create a connection and copy its private connection code. Paste it here.";
  prompt.addSecureTextField("Connection code", ""); prompt.addAction("Connect"); prompt.addCancelAction("Cancel");
  if (await prompt.presentAlert() < 0) return previous;
  let connection;
  try {
    connection = JSON.parse(prompt.textFieldValue(0));
    if (typeof connection.origin !== "string" || !/^https:\/\/[^\s/?#@]+$/.test(connection.origin) ||
        typeof connection.token !== "string" || !/^[A-Za-z0-9_-]{40,100}$/.test(connection.token) ||
        !Number.isSafeInteger(connection.account_id) || connection.account_id < 1 || !Number.isFinite(connection.expires_at)) {
      throw new Error("Invalid connection");
    }
    connection.cacheID = String(connection.account_id) + "-" + Date.now().toString(36);
    await fetchMeal(connection); // Validate before replacing an existing working connection.
    clearCache(previous); Keychain.set(key, JSON.stringify(connection));
    return connection;
  } catch (_) {
    if (connection && connection.cacheID) clearCache(connection);
    const alert = new Alert(); alert.title = "Could not connect";
    alert.message = "Check the code and internet connection. If it expired or was disconnected, create a new connection in Fatties.";
    alert.addAction("OK"); await alert.presentAlert(); return previous;
  }
}

function smallText(stack, text) {
  const node = stack.addText(text); node.font = Font.systemFont(11);
  node.textColor = Color.dynamic(new Color("666666"), new Color("bbbbbb")); node.lineLimit = 2;
  return node;
}
function render(connection, cached, message) {
  const widget = new ListWidget(); widget.setPadding(12, 12, 12, 12);
  widget.backgroundColor = Color.dynamic(new Color("fcfcfb"), new Color("1a1a19"));
  widget.refreshAfterDate = new Date(Date.now() + 15 * 60 * 1000);
  if (connection) widget.url = connection.origin + "/";
  if (!connection || !cached) {
    const title = widget.addText("Fatties"); title.font = Font.boldSystemFont(17);
    widget.addSpacer(8);
    smallText(widget, message || "Run the Fatties script in Scriptable to connect your account.");
    return widget;
  }
  const meal = cached.data.meal;
  const row = widget.addStack(); row.layoutHorizontally(); row.centerAlignContent(); row.spacing = 12;
  const photo = row.addStack(); photo.size = new Size(106, 110); photo.cornerRadius = 10;
  if (cached.image && meal) {
    const image = photo.addImage(cached.image); image.imageSize = new Size(106, 110); image.applyFillingContentMode();
  } else {
    photo.backgroundColor = Color.dynamic(new Color("efefec"), new Color("30302e")); photo.centerAlignContent();
    photo.addSpacer(); const symbol = photo.addImage(SFSymbol.named("fork.knife").image); symbol.imageSize = new Size(30, 30); photo.addSpacer();
  }
  const text = row.addStack(); text.layoutVertically(); text.spacing = 4;
  smallText(text, cached.data.partner.name);
  const title = text.addText(meal ? meal.title : "No meals logged yet"); title.font = Font.boldSystemFont(15); title.lineLimit = 2;
  if (meal) {
    widget.url = meal.url;
    const kcal = text.addText(Math.round(meal.kcal).toLocaleString() + " kcal"); kcal.font = Font.semiboldSystemFont(14);
    const logged = text.addStack(); smallText(logged, "Logged ");
    const date = logged.addDate(new Date(meal.logged_at)); date.applyRelativeStyle(); date.font = Font.systemFont(11);
  }
  text.addSpacer();
  const updated = text.addStack(); smallText(updated, message ? "Offline · Updated " : "Updated ");
  const date = updated.addDate(cached.refreshed); date.applyRelativeStyle(); date.font = Font.systemFont(10);
  return widget;
}

let connection = readConnection();
if (config.runsInApp) {
  if (connection) {
    const menu = new Alert(); menu.title = "Fatties";
    menu.message = "Connected as " + connection.account_name + ", showing " + connection.partner_name + ".";
    menu.addAction("Preview / refresh"); menu.addAction("Reconnect"); menu.addDestructiveAction("Remove connection from this phone"); menu.addCancelAction("Cancel");
    const choice = await menu.presentSheet();
    if (choice === 1) connection = await connect(connection);
    else if (choice === 2) { clearConnection(connection); connection = null; }
  } else connection = await connect(null);
}
let cached = connection ? readCache(connection) : null;
let message = null;
if (connection) {
  if (connection.expires_at * 1000 <= Date.now()) {
    clearConnection(connection); connection = null; cached = null; message = "Connection expired. Reconnect from Fatties > Widget.";
  } else {
    try { cached = await fetchMeal(connection); }
    catch (error) {
      if (error.status === 401 || error.status === 403) {
        clearConnection(connection); connection = null; cached = null; message = "Widget disconnected. Reconnect from Fatties > Widget.";
      } else message = "Offline. Open Fatties or try again later.";
    }
  }
}
const widget = render(connection, cached, message);
if (config.runsInWidget) Script.setWidget(widget);
else await widget.presentMedium();
Script.complete();
