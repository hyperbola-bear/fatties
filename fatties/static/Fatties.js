// Fatties partner-meal widget for Scriptable. Contains no account secrets.
// Run inside Scriptable once and paste the connection code from Fatties > Widget.
// Then add a small or medium Scriptable Home Screen widget and select this script.
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

const muted = Color.dynamic(new Color("666666"), new Color("bbbbbb"));
const faint = Color.dynamic(new Color("8a8a86"), new Color("8a8a86"));
const ink = Color.dynamic(new Color("1a1a19"), new Color("ffffff"));

function line(stack, text, font, color, lines) {
  const node = stack.addText(text); node.font = font; node.textColor = color;
  node.lineLimit = lines || 1; node.minimumScaleFactor = 0.75;
  return node;
}
function relative(stack, prefix, date, font, color) {
  const row = stack.addStack(); row.layoutHorizontally(); row.centerAlignContent();
  line(row, prefix, font, color);
  const node = row.addDate(date); node.applyRelativeStyle(); node.font = font; node.textColor = color;
  node.lineLimit = 1; node.minimumScaleFactor = 0.75;
  return row;
}
function footer(stack, cached, message) {
  relative(stack, message ? "Offline · Updated " : "Updated ", cached.refreshed, Font.systemFont(10), faint);
}

// Small widget with a photo: the photo fills the widget, details sit on a dark band.
function photoTile(widget, cached, meal, message) {
  widget.setPadding(0, 0, 0, 0);
  widget.backgroundImage = cached.image;
  widget.addSpacer();
  const band = widget.addStack(); band.layoutHorizontally(); band.setPadding(18, 12, 10, 12);
  const gradient = new LinearGradient();
  gradient.colors = [new Color("000000", 0), new Color("000000", 0.72)]; gradient.locations = [0, 0.45];
  band.backgroundGradient = gradient;
  const details = band.addStack(); details.layoutVertically(); details.spacing = 1;
  const white = new Color("ffffff"), soft = new Color("ffffff", 0.82);
  relative(details, cached.data.partner.name + " · ", new Date(meal.logged_at), Font.systemFont(11), soft);
  line(details, meal.title, Font.boldSystemFont(14), white, 2);
  line(details, Math.round(meal.kcal).toLocaleString() + " kcal", Font.semiboldSystemFont(12), white);
  if (message) line(details, "Offline", Font.systemFont(10), soft);
  band.addSpacer();
}

// No meal, or a meal without a photo: a text card with a small icon, no empty photo box.
function textCard(widget, cached, meal, message, roomy) {
  widget.setPadding(14, 14, 12, 14);
  const icon = widget.addImage(SFSymbol.named("fork.knife").image);
  icon.imageSize = new Size(roomy ? 22 : 20, roomy ? 22 : 20); icon.tintColor = faint;
  widget.addSpacer();
  line(widget, cached.data.partner.name, Font.systemFont(11), muted);
  line(widget, meal ? meal.title : "No meals logged yet", Font.boldSystemFont(roomy ? 16 : 14), ink, 2);
  if (meal) {
    line(widget, Math.round(meal.kcal).toLocaleString() + " kcal", Font.semiboldSystemFont(roomy ? 14 : 12), ink);
    relative(widget, "Logged ", new Date(meal.logged_at), Font.systemFont(11), muted);
  }
  widget.addSpacer(6);
  footer(widget, cached, message);
}

// Medium widget with a photo: photo on the left, details on the right.
function photoRow(widget, cached, meal, message) {
  widget.setPadding(12, 12, 12, 12);
  const row = widget.addStack(); row.layoutHorizontally(); row.spacing = 12;
  const photo = row.addStack(); photo.size = new Size(122, 122); photo.cornerRadius = 12;
  const image = photo.addImage(cached.image); image.imageSize = new Size(122, 122); image.applyFillingContentMode();
  const text = row.addStack(); text.layoutVertically(); text.spacing = 2;
  line(text, cached.data.partner.name, Font.systemFont(11), muted);
  line(text, meal.title, Font.boldSystemFont(15), ink, 2);
  line(text, Math.round(meal.kcal).toLocaleString() + " kcal", Font.semiboldSystemFont(14), ink);
  relative(text, "Logged ", new Date(meal.logged_at), Font.systemFont(11), muted);
  text.addSpacer();
  footer(text, cached, message);
  row.addSpacer();
}

function render(connection, cached, message, family) {
  const widget = new ListWidget();
  widget.backgroundColor = Color.dynamic(new Color("fcfcfb"), new Color("1a1a19"));
  widget.refreshAfterDate = new Date(Date.now() + 15 * 60 * 1000);
  if (connection) widget.url = connection.origin + "/";
  if (!connection || !cached) {
    widget.setPadding(14, 14, 14, 14);
    line(widget, "Fatties", Font.boldSystemFont(17), ink);
    widget.addSpacer(8);
    line(widget, message || "Run the Fatties script in Scriptable to connect your account.", Font.systemFont(11), muted, 4);
    return widget;
  }
  const meal = cached.data.meal;
  if (meal) widget.url = meal.url;
  const small = family === "small";
  if (meal && cached.image) small ? photoTile(widget, cached, meal, message) : photoRow(widget, cached, meal, message);
  else textCard(widget, cached, meal, message, !small);
  return widget;
}

let connection = readConnection();
let preview = "medium";
if (config.runsInApp) {
  if (connection) {
    const menu = new Alert(); menu.title = "Fatties";
    menu.message = "Connected as " + connection.account_name + ", showing " + connection.partner_name + ".";
    menu.addAction("Preview medium"); menu.addAction("Preview small"); menu.addAction("Reconnect");
    menu.addDestructiveAction("Remove connection from this phone"); menu.addCancelAction("Cancel");
    const choice = await menu.presentSheet();
    if (choice === 1) preview = "small";
    else if (choice === 2) connection = await connect(connection);
    else if (choice === 3) { clearConnection(connection); connection = null; }
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
const family = config.runsInWidget ? config.widgetFamily : preview;
const widget = render(connection, cached, message, family);
if (config.runsInWidget) Script.setWidget(widget);
else if (family === "small") await widget.presentSmall();
else await widget.presentMedium();
Script.complete();
