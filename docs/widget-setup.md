# Fatties photos and iPhone widget setup

Fatties stays a web app. The existing Scriptable iPhone app hosts its partner-meal
widget. No custom native Fatties app, signing renewal, TestFlight, or paid Apple
Developer membership is required.

## Provisioning status — 10 November 2026

The dedicated `fatties-meals` bucket is in APAC with Standard storage. On 9
November its public `r2.dev` access was disabled and no custom domains were
attached.

An R2 S3 credential was created in the Cloudflare dashboard (R2 → Manage API
tokens) and saved in the uncommitted `.env`. The earlier attempt to create it
through the Cloudflare MCP's token delegation was abandoned. The credential
has Object Read & Write access, but it is **not limited to `fatties-meals`**:
it can also list `manga-backend`. The owner accepted this. Restricting it to
the one bucket later needs only a new token and the two `.env` values.

`FATTIES_PHOTO_STORAGE=r2` is now set. Checks run with this credential:

- The app's `R2Photos` adapter saved a full image (1568 px) and a thumbnail
  (512 px), read both back byte for byte, and deleted them.
- A local server with a temporary database and the fake analyzer passed 17
  end-to-end checks against the real bucket: owner-only writes, partner photo
  reads, retry-safe saves, Track, the widget feed and thumbnail, widget token
  isolation, and R2 object removal when a meal is deleted. The demo data and
  its objects were removed afterwards, so the bucket is empty.

Not done yet: deployment (no server or HTTPS origin is known), real Anthropic
analysis, and installing the widget on the iPhones.

## Server: private Cloudflare R2 photos

1. In your Cloudflare account, enable R2 and create a private bucket for meal
   photos. Leave public access and the `r2.dev` public URL disabled. The server
   accesses R2 directly; this implementation needs no browser CORS policy,
   Worker, public image domain, or Cloudflare Images subscription.
2. Create an R2 S3 service credential with Object Read & Write access restricted
   to that bucket. Keep the secret on the server, not in Scriptable or a chat.
3. Add these settings to the server's uncommitted `.env` or secret environment:

   ```dotenv
   FATTIES_PHOTO_STORAGE=r2
   FATTIES_PUBLIC_URL=https://your-fatties-domain.example
   R2_ACCOUNT_ID=your-cloudflare-account-id
   R2_BUCKET=your-private-bucket
   R2_ACCESS_KEY_ID=your-bucket-scoped-access-key-id
   R2_SECRET_ACCESS_KEY=your-secret-access-key
   ```

4. Stop the server, back up the SQLite database and any existing local photos,
   install the upgraded package and restart one worker. If using an existing
   virtual environment without pip, `uv pip install --python .venv/bin/python
   -e '.[dev]'` is equivalent to the README installation command.
5. Log in over HTTPS, save one photo meal, open it in Track, and verify its
   image. Delete a disposable test meal and check its objects disappear from
   the bucket. Do this with your own account; each partner owns their own logs.

Uploads are recompressed to JPEG, oriented, stripped of metadata, limited to
1568 pixels on the long side, and given a thumbnail up to 512 pixels. The server
proxies authenticated photo reads. Neither R2 secrets nor public image URLs
are returned to the browser/widget. Unsaved estimates never upload to R2.

Saved meals retain their images until deletion. Failed uploads and deleted-meal
images use a durable cleanup queue; failed cleanup retries on restart and later
meal saves/deletes. A server/storage outage can delay physical deletion, but a
deleted meal is immediately inaccessible through the API. Old meal logs have
no original photo and use a placeholder.

You can keep using Fatties without signing into Cloudflare. The default
`FATTIES_PHOTO_STORAGE=local` keeps images private beside SQLite in `photos/`.

When ready to move existing photos to R2, stop Fatties and back up the database
and photo directory. Keep `FATTIES_PHOTO_STORAGE=local` during migration:

```bash
# Checks local originals; needs no Cloudflare login or credentials.
.venv/bin/fatties migrate-photos-to-r2 --dry-run

# After configuring R2 credentials above, copies and reads back both images.
.venv/bin/fatties migrate-photos-to-r2
```

Only after the command succeeds, set `FATTIES_PHOTO_STORAGE=r2` and restart.
The command preserves every local original and database reference, verifies
both full images and thumbnails, and can be retried after interruption. It
does not switch configuration automatically. Keep originals as a backup until
live reads have been checked. Changing the setting alone does not move images.
R2 storage/operation charges depend on usage.

## Each iPhone: connect Scriptable

1. Install [Scriptable](https://apps.apple.com/sg/app/scriptable/id1405459188).
2. Open Fatties over HTTPS and log in as yourself. Open the **Widget** tab.
3. Download **Fatties.js**, share the file to Scriptable, and add the script.
   If the share flow is unavailable, open the downloaded file, copy its contents
   into a new Scriptable script, and name it **Fatties**. The downloadable source
   contains no credentials; do not paste a connection code into source code.
4. In Fatties, select **Create widget connection**, then **Copy connection code**.
   This one-time private code is hidden and cleared when leaving the Widget tab.
5. Run Fatties inside Scriptable, paste the code into its secure prompt, and
   choose **Connect**. This fetches and previews the partner's latest saved log.
   The credential is stored in Scriptable Keychain. If no logs exist yet, the
   preview says **No meals logged yet**.
6. Touch and hold the iPhone Home Screen, choose **Edit → Add Widget** (wording
   can vary by iOS version), select **Scriptable**, and add a **small** or **medium** widget.
   Edit it and choose the **Fatties** script. No widget parameter is needed.
7. Repeat on the other phone, logging into the other person's own account.
   Polar's connection shows Yeobo, and Yeobo's shows Polar. The default example
   names are Polar/Partner; use the actual configured account names.

The photo is prominent, with partner name, meal name, calories, logging time and
last successful refresh. Tapping opens that exact meal's HTTPS web page, asking
for a browser login if needed. Test whether your phone opens Safari or the
installed web app; this implementation does not force installed-PWA launch.

## Refresh, disconnection, and offline behavior

- Newest means **newest saved**, even for a backdated meal. Deleted latest logs
  fall back to the previous saved log at the next successful refresh.
- Scriptable requests a refresh after 15 minutes. iOS decides actual timing;
  food logging does not send push updates to Scriptable. You can run the script
  and choose **Preview / refresh** to fetch now.
- Network/server failures retain the last successful meal/photo and show its
  refresh age. Empty results clear old content. A thumbnail/log race retries
  the data fetch so the photo and meal stay matched.
- Connections expire after 90 days. Logout, password change, or **Disconnect my
  widgets** invalidates all widget connections for your account, not your
  partner's. Generate a new code and use **Reconnect** in Scriptable.
- Revoked/expired credentials clear cached meal/photo data on the next widget
  run that can detect the condition. Already downloaded data cannot be erased
  remotely from an offline phone. The script can detect fixed expiry offline.
- **Remove connection from this phone** in Scriptable clears its local credential
  and cache; use Fatties **Disconnect my widgets** to revoke server-side access.
- Rename a Scriptable script only if you intend to reconnect: Keychain entries
  are separated by script name, and each connection gets an isolated cache.

## Verification checklist for the live installation

- Both accounts can save their own plan/meals, and cannot change the partner's.
- Saved images display in Track and the widget; deleted/legacy logs behave as above.
- Each phone shows its opposite partner; reconnecting accounts clears the old cache.
- A newly logged backdated meal becomes the widget's next result.
- Airplane mode preserves the cached meal with its refresh age.
- Disconnect or password change produces a reconnect message after refresh.
- Tapping opens the specific meal; a deleted meal has a clear web fallback.
- Check actual refresh behavior with Scriptable closed and normal iOS power settings.

## Primary references

- [Cloudflare R2 with boto3](https://developers.cloudflare.com/r2/examples/aws/boto3/)
- [R2 API credentials](https://developers.cloudflare.com/r2/api/tokens/)
- [R2 pricing](https://developers.cloudflare.com/r2/pricing/)
- [Scriptable widgets and refresh](https://docs.scriptable.app/listwidget/)
- [Scriptable requests](https://docs.scriptable.app/request/)
- [Scriptable Keychain](https://docs.scriptable.app/keychain/)
