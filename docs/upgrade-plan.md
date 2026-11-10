# Fatties upgrade planning

Status: implementation completed locally; private Cloudflare bucket provisioned and REST storage access verified, 9 November 2026. Bucket-scoped S3 credentials and real-iPhone verification remain; see `widget-setup.md`.

## Implementation Results

- Implemented self-only writes/shared reads, unified Track, portion/macro adjustments, private local/R2 photo storage, meal deep links, widget credentials/setup UI, and the Scriptable widget.
- Python: **63 passed, 1 skipped**. The skipped test is the opt-in browser flow, which was run separately and passed.
- JavaScript: **8 passed**, including adjustment math and the Scriptable script running against simulated native APIs.
- Browser: the mobile flow passed in Chromium and WebKit. Final WebKit verification includes plan ownership UI, all Track periods, continuous dragging, corrected macros/reset, saved photo/deep link, account switching, and widget connection/disconnection. Screenshots were visually inspected.
- Lint and whitespace checks passed. A wheel was built and checked to include the photo adapter, UI assets, adjustment module, and downloadable widget script.
- Concurrent save/retry and database/upload failure tests pass. Shared SQLite reads and writes are serialized; the separate photo-operation lock avoids holding the database lock across storage network calls. Run one server worker, as documented.
- This workspace has no bucket-scoped R2 S3 credential or canonical public URL configured, so local private photos remain active. The Cloudflare account ID and bucket name are prepared in the uncommitted `.env`. No production app deployment or actual iPhone widget installation has been performed. No real photo-analysis API calls were needed for verification.
- Cloudflare sign-in was explicitly deferred by the user. Added `fatties migrate-photos-to-r2 --dry-run` to check existing local photos without credentials, and `fatties migrate-photos-to-r2` to copy and verify full images and thumbnails later. The command preserves local originals, meal records, and configuration; migration, retry, failure, and verification tests pass. Cloudflare access is not a prerequisite for using the local implementation.
- Subsequent Cloudflare setup: the user completed OAuth and restarted Codex. Created `fatties-meals` with APAC location hint and Standard storage. Verified `r2.dev` public access is disabled and no custom domains exist. A live REST upload/read/delete check passed and its disposable object was removed. The existing unrelated bucket was left unchanged.
- Account-token permission discovery now works after restart. Cloudflare also requires the creator's OAuth session to include the permissions delegated to the new token. An attempted bucket-scoped creation returned HTTP 400 explicitly naming missing `Workers R2 Storage Bucket Item Read` and `Write`; no token was created. The user then approved `workers-r2-bucket-item.read` and `workers-r2-bucket-item.write` alongside `account-api-tokens.write`. The complete scope list is saved in global Codex config, preserving unrelated settings. The MCP session still reported both R2 groups as `is_selectable: false`.
- 10 November: the MCP delegation path was replaced. An R2 Object Read & Write credential was created in the Cloudflare dashboard and saved in `.env`. It also reaches the `manga-backend` bucket, which the owner accepted. The app's R2 adapter and a local end-to-end run against the real bucket passed, and `FATTIES_PHOTO_STORAGE=r2` is set. See `docs/widget-setup.md` for details.
- Next connection: confirm both R2 groups are selectable, then create an account-owned token limited to `com.cloudflare.edge.r2.bucket.<account_id>_default_fatties-meals` with read group `6a018a9f2fc74eb6b293b0c548f38b39` and write group `2efd5506f9c8494dacb1fa10a3e7d5b6`. Generate S3 Access Key ID from token ID and Secret Access Key from SHA-256(token value), as documented by Cloudflare. Keep secrets out of tool output/chat: encrypt the creation response to a temporary local public key before returning it, decrypt and save directly into private `.env`, and discard temporary keys. Verify actual S3 upload/read/delete before switching `FATTIES_PHOTO_STORAGE=r2`. Do not create an account-wide app credential.

## Ticket Summary

- Goal: enforce self-only plan and meal writes with shared read access; consolidate Day and Week into Track with Month and Year; add portion and individual nutrient sliders alongside existing multiplier buttons; show the partner's last logged food and photo in an iPhone Home Screen widget.
- Non-goals: changing production data, deploying without a configured destination, or enrolling in paid distribution services. Application implementation was authorized after the interview.
- Definition of done: decisions below are answered, the user confirms shared understanding, and the implementation slices have testable acceptance criteria. Implementation completion will additionally require real-device widget verification.
- Open product questions: none remaining from the interview. The user accepted Scriptable and authorized implementation. Live Cloudflare setup and physical-device checks are release dependencies.

## Context Map Before Implementation

- `fatties/app.py`: FastAPI routes. Authentication is required, but starting a plan currently accepts any existing person ID without comparing it to the session identity. Meal creation and deletion are also shared.
- `fatties/auth.py`: signed, password-bound, 90-day login cookies; changing a password invalidates old sessions.
- `fatties/static/index.html` and `app.js`: Log, Day, Week, and Plan tabs; editable plan cards for both people; a meal-recipient selector independent of login identity.
- `fatties/static/manifest.webmanifest`: installable web app metadata; no native iOS project or widget extension exists.
- `fatties/static/app.js`: item multipliers are currently ×0.5, ×0.75, ×1, ×1.25, ×1.5, ×2. Scaling applies to weight, calories, and every nutrient from an unchanged base estimate.
- `fatties/store.py`: SQLite people, plans, and meals. Photos are discarded. Meal creation time and eating time are separate stored values, but creation time is not exposed in meal responses.
- `fatties/summary.py`: calendar weeks run Monday–Sunday. Targets follow the plan in force on each day; unlogged days are distinguished from logged days. No month/year summaries exist.
- `tests/test_app.py`: API, authentication, plan history, and summary tests. Existing fixtures primarily log in as Polar; cross-account checks will need two authenticated clients.

## Design Tree

Selected architecture and verified constraints:

- A first-party Home Screen widget needs an installed iOS app containing a SwiftUI WidgetKit extension. Safari's Add to Home Screen creates a web-app icon. Installing the app and placing its widget are separate user actions. Sources: [Apple widget extensions](https://developer.apple.com/documentation/widgetkit/creating-a-widget-extension), [Safari web apps](https://support.apple.com/guide/iphone/open-as-web-app-iphea86e5236/ios), [adding widgets](https://support.apple.com/en-gb/guide/iphone/iphb8f1bf206/ios).
- Accepted: keep Fatties entirely as a web app and use the existing Scriptable app as the native widget host. The Singapore App Store lists Scriptable as free with tip purchases. Its widgets support text/images, authenticated HTTP requests, Keychain credentials, local file caching, and opening a web URL on tap. This avoids building/signing/distributing our own iOS app or paying Apple membership. Each phone still needs Scriptable installed, a one-time script/credential setup, and adding its medium widget. Sources: [App Store](https://apps.apple.com/sg/app/scriptable/id1405459188), [ListWidget](https://docs.scriptable.app/listwidget/), [Request](https://docs.scriptable.app/request/), [Keychain](https://docs.scriptable.app/keychain/), [FileManager](https://docs.scriptable.app/filemanager/).
- Scriptable's `refreshAfterDate` sets an earliest requested refresh, not an exact schedule. Do not promise backend-triggered push refresh through Scriptable or that tapping a web URL opens the installed PWA instead of Safari; test actual tap behavior. Sources: [refresh behavior](https://docs.scriptable.app/listwidget/#refreshafterdate), [tap URL](https://docs.scriptable.app/listwidget/#url).
- Dedicated native wrappers, custom WidgetKit extensions, APNs integration, Ad Hoc signing, and TestFlight distribution are outside the selected approach. The installed Scriptable app supplies the native widget extension.
- Cloudflare R2 supports private objects and expiring presigned URLs. Proposed access options are authorized backend streaming or short-lived URLs after authentication, with locally cached widget thumbnails; do not depend on an old signed URL remaining valid. Private R2 storage and lifecycle were accepted in round two. Sources: [R2 signed URLs](https://developers.cloudflare.com/r2/api/s3/presigned-urls/), [R2 pricing](https://developers.cloudflare.com/r2/pricing/).

Confirmed requirements after the final widget-host decision:

1. Polar cannot set Yeobo's plan; Yeobo cannot set Polar's plan. Each person also creates/deletes only their own meals. Both partners can view each other's plans and history read-only. Enforce ownership on the server as well as in the interface.
2. One Track tab replaces separate Day and Week tabs, with Day, Week, Month, Year and previous/next navigation. Calendar weeks remain Monday–Sunday. Both partners remain visible; targets follow plan history and unlogged days remain explicit.
3. Photo-estimate adjustment includes a whole-meal portion slider and expandable per-food controls: portion, carbs, protein, fat, and sugar sliders, plus editable numbers and the existing portion shortcut buttons. Portion changes preserve macro corrections proportionally. Calories update automatically; there is no separate calorie slider initially. Sugar remains included within carbs and cannot exceed them. Include Reset to estimate.
4. The medium widget shows the partner's photo, name, meal name, calories, logging time, and refresh status. Tapping opens that meal. Retain compressed saved-meal photos and thumbnails in private Cloudflare R2 storage for as long as the meal exists; deleting the meal deletes the images, and unsaved uploads are discarded. Old meal logs show a photo placeholder.
5. “Last logged” means newest saved log, even if its eating time is backdated. The database needs a stable tie-breaker because creation timestamps can tie; propose creation timestamp then meal ID descending.
6. Occasional widget update delays are acceptable if refresh age is visible. Use periodic Scriptable refresh; there is no promised event-driven delivery or guaranteed refresh interval.
7. Fatties remains a web app. The user accepted installing Scriptable on both phones and using it for the widget; no paid Apple membership or custom native distribution is planned.
8. Month charts show daily bars; Year shows monthly summaries. Both include calories/macros versus targets, averages per logged day, logging coverage, and prior-period comparisons. Unlogged days are not zero-intake days.
9. Offline widgets retain the cached meal and show refresh age. Deleting the latest log reveals the preceding log on the next successful refresh; no logs produces “No meals logged yet.” Locally cached information cannot be remotely erased immediately while a device is offline.

Decision frontier: complete. The user confirmed the selected approach and explicitly requested implementation.

Implementation notes under the accepted behavior:

- Macro sliders use gram values and numeric input so an initially zero estimate can be corrected; bound controls appropriately while allowing precise input. Portion changes scale corrected grams proportionally, without accumulating rounding errors.
- Calorie calculation implementing the accepted automatic-update behavior: preserve the scaled baseline calorie estimate and add energy deltas for changed carbs/protein/fat (4/4/9 kcal per gram), bounded at zero. Sugar contributes no extra energy because it is already within carbs. This preserves the original estimate's fibre/alcohol differences instead of replacing every estimate with 4/4/9. Test unchanged estimates, proportional corrections, zero values, reset, and the sugar/carbs constraint.
- Photos: private Cloudflare R2 storage and lifecycle are accepted. Existing meal logs have no recoverable photo. No Cloudflare resources have been created or account access requested.
- Widget refresh: periodic fetching through Scriptable, with cached data on network/server failure. Clear cached content on an explicit invalid/revoked-credential response when the next refresh runs; display a reconnect message. Prefer an authenticated backend thumbnail endpoint so R2 credentials remain server-side.
- Implemented widget credential: a revocable read-only token generated from authenticated web settings, stored hashed server-side and in Scriptable Keychain on-device, sent in an authorization header, and valid for 90 days like the existing web login. It permits only that user's partner-last-log data and its thumbnail; server derives the partner and rejects arbitrary target IDs. Password change, web logout, or Disconnect widget revokes the user's widget credentials; the other partner's credentials are unaffected. Expired/revoked credentials require reconnecting through settings. No general session credentials, hardcoded account passwords, public R2 objects, or credential-bearing URLs are used.

## Target Flow and Integration Contract

1. A person logs into the existing HTTPS web app. Log and Plan writes derive ownership from that authenticated person; viewing the partner never changes write identity.
2. Photo analysis remains temporary. Saving creates that person's meal plus a retained compressed image/thumbnail in R2; failed or abandoned saves must not leak orphan images. Make upload/save idempotent to avoid duplicate logs on retries.
3. Track uses one selected date and calendar period. Month handles variable lengths/leap days; Year groups months; incomplete current periods exclude future targets from to-date summaries. Historical targets are resolved per day before aggregation.
4. Widget settings provide a downloadable generic Scriptable script plus a per-person connect/disconnect flow. Secrets must not be embedded in downloadable script source or URLs. Support separate local credential/cache names per account to prevent old partner content after switching accounts.
5. Cookie-authenticated routes: `GET /api/widget/credentials` lists active connection expiries, `POST /api/widget/credentials` issues a connection, and `DELETE /api/widget/credentials` disconnects it. Newly generated tokens are shown only during the explicit connection flow. Bearer-authenticated read routes: `GET /api/widget/partner/latest` and `GET /api/widget/partner/latest/thumbnail`. These dedicated routes authenticate explicitly; other API routes still require browser login.
6. The latest response includes only partner display name, meal ID/title, calories, created/logged timestamp, eaten timestamp, thumbnail availability, and canonical meal web URL, plus server response time. No meals is an explicit empty result. Sort by normalized creation instant then ID descending. The thumbnail endpoint exposes only the currently authorized partner's current latest photo; legacy/no-photo cases use a local placeholder.
7. Add a web meal detail/deep-link route so widget taps open the specific meal, including a friendly deleted-meal state and login return destination. Test browser behavior on both phones rather than promising installed-PWA launch.
8. A successful fetch replaces the widget cache. Successful empty results clear stale meal content; 401/403 clears credential-associated cache and prompts reconnection; network/5xx failures retain cached content and its last successful refresh time. Fetch JSON and image as a consistent meal pair so a newly logged meal cannot acquire an older meal's thumbnail.

## Slices

Implementation slices, sequenced for independent review and verification. Slice 5 depends on the data/photo contract from slice 4; all slices preserve existing SQLite history.

| # | Slice | Scope | Acceptance check | Suggested agent prompt |
|---|---|---|---|---|
| 1 | Enforce self-only writes | `app.py`, `static/app.js`, `tests/test_app.py` | Both accounts can save their own plan and meals; cross-account plan saves, meal creation, and deletion return 403 without data changes; both accounts retain read access | Implement confirmed ownership rules with two-account API tests and matching UI; separate session identity from any view selection. |
| 2 | Unify Track and add calendar summaries | `summary.py`, `app.py`, `static/index.html`, `app.js`, `style.css`, tests | One Track tab; agreed dropdown periods; correct boundaries and historical targets; unlogged days remain explicit | Implement agreed Track periods and navigation, preserving existing daily/weekly semantics. |
| 3 | Add portion and macro sliders | `static/app.js`, `style.css`, `app.py`/`analysis.py` if correction semantics need changes, tests | Agreed overall/item controls and shortcut buttons stay synchronized; corrections/calories obey agreed rules; zero estimates can be corrected; removal/undo/reset works; continuous touch drag retains focus | Add agreed portion and nutrient controls without accumulating rounding errors or rebuilding the dragged control on every input. |
| 4 | Retain photos and serve partner's last logged meal | `store.py`, `app.py`, config/dependencies, frontend save flow, new storage module, tests | Private saved-meal images and thumbnails work; deleted/failed saves clean up; existing logs show a placeholder; partner comes from session identity; newest-created ordering is deterministic | Implement the agreed Cloudflare photo lifecycle and authenticated partner-latest-log data contract. |
| 5 | Prove web-backed widget end to end | `static/Fatties.js`, setup guide, backend scoped credentials and web settings, meal detail/deep-link flow, tests; no iOS project | Both phones show the opposite partner's meal; one-time setup and adding the widget work; wrong/revoked tokens fail; tap opens the correct meal web URL; cache states work | Build the agreed Scriptable integration with authenticated partner-last-log data and private thumbnail access; document physical-device results. |
| 6 | Complete refresh and setup | Selected widget host and backend lifecycle | Periodic refresh works within host/iOS limits; stale data is recognizable; revocation/password change invalidates access and clears cached data on the next permitted refresh; setup instructions tested for both people | Complete selected host setup, periodic refresh and credential/cache lifecycle; document system limits and actual tap behavior. |

## Verification Plan

- Focused checks: two-account ownership for every write route and unchanged-data assertions; calendar boundary and historical-plan summary tests; nutrient adjustment/calorie/zero-value behavior; deterministic latest-log ordering and deletion; private image access and failed-save cleanup.
- Broader checks: `.venv/bin/pytest -q` and `.venv/bin/ruff check .` after implementation.
- Manual checks: iPhone touch slider continuity and keyboard accessibility; each Track period and navigation boundary; actual widget installation, login, offline cache, update behavior, and tap navigation on both phones.
- Implementation checks were run; exact results and remaining external checks are listed above.

## Risks

- Product/behavior: newest saved and newest eaten are intentionally different; shared logging is removed by agreement; a missing log must not look like zero intake; independent macro sliders introduce correction interactions that must be explicit.
- Technical: Scriptable credentials differ from browser cookies; background scheduling constrains freshness; slider input must not destroy the active drag target; photo upload and SQLite writes need explicit compensation/idempotency.
- Security/privacy: do not trust the meal-recipient selector as login identity; scope widget access to the authenticated person's partner; private photos introduce new retained data and access/cache lifecycle; partner plan visibility is accepted but only owners may write.
- Rollback/migration: current non-image history must remain readable; image/credential schema changes need additive migration, revocation, and cleanup behavior. Take a database backup before migration. Deploy ownership and web changes independently; keep widget/photo enablement configurable until integration checks pass.

## Recommended Execution Order

1. Shared understanding was confirmed and implementation authorized. Architecture decisions are recorded in `docs/adr/`.
2. Enforce ownership, then complete Track and slider improvements as independently verifiable changes.
3. Add the agreed photo/latest-meal data contract and a minimal physical-device widget prototype using the selected host before investing in setup automation.
4. Complete periodic refresh, credential/privacy lifecycle, setup guidance, and device validation. For Scriptable, omit native build/signing/push work.

## Implementation Dependencies and Completion Evidence

- Cloudflare setup: R2 bucket, a service credential restricted to the required bucket operations, HTTPS server configuration, and private object access. Gather credentials through a safe setup flow only when integration work begins; none have been requested or created during planning. Cloudflare storage/operation costs are separate from Scriptable and depend on usage.
- Device setup: install Scriptable on both phones, import the supplied script, connect each own Fatties account, then add a medium Scriptable widget. Widget placement is a user action.
- Automated evidence: authorization for both users and all write routes; cookie/bearer separation; credential expiry/revocation; summary boundaries; slider math/reset; photo access/cleanup; latest-log ordering and cache response contracts.
- Device evidence: opposite-partner identity, retained image, offline state, deleted/empty states, expired token reconnect, actual refresh behavior, exact-meal tap destination, and continuous touch sliders.
- Completion claims distinguish automated and browser verification from actual iPhone checks. Code and local verification are complete; live infrastructure and phone setup have not been performed.
