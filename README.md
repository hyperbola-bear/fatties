# fatties

A small meal tracker for two people who are losing weight together. Each meal
or snack gets a photo. Claude estimates calories, carbs, protein, fat and
sugar for Singapore food, you adjust the portions, and the app compares what
each person ate with their own daily and weekly targets.

## What it does

- **Log**: take a photo, add an optional note ("ate half", "kopi siew dai"),
  and get an estimate per item. Scale any item (x0.5 to x2) or remove it, then
  save it to your own account. The whole meal and individual foods have portion
  sliders and shortcut buttons. Expand a food to correct carbs, protein, fat,
  and sugar with sliders or exact gram inputs. Calories update automatically;
  portion changes preserve corrections proportionally. Reset restores the estimate.
  Photos are retained only when you save a meal, in private storage.
- **Track**: one dropdown for Day, Week (Monday–Sunday), Month, and Year,
  previous/next navigation and a date picker. Both partners' history remains
  visible. Daily/monthly charts, averages per logged day, logging coverage,
  previous-period comparisons and plan-day budgets distinguish missing logs
  from zero intake. Targets follow the plan active on each date. Year charts
  show monthly averages per logged day.
- **Plan**: enter current weight, target weight, height, age, sex and how
  often you exercise, then pick a pace (0.25 to 1 kg a week) or a date to reach
  the target by. The app works out daily calories, carbs, protein, fat and
  sugar, and when you will reach the target. You can adjust the numbers, then
  start the plan. It starts that day: Day and Week compare against it from
  then on, and days before it have no target. Starting a new plan later (say,
  with your new weight) keeps the old targets for the days before.
- **Widget**: connect the free Scriptable iPhone app to show your partner's
  newest saved meal, photo, calories and logging time on the Home Screen.
  Tap to open the meal's web page. Fatties remains a web app; there is no
  Fatties native build or Apple Developer subscription. Setup is in the
  Widget tab and [the setup guide](docs/widget-setup.md).

Each account can create/delete only its own meals and change only its own
plan. Your partner's plan and history are read-only, enforced by the API.

How the plan is worked out: maintenance calories from Mifflin-St Jeor at your
exercise level, minus about 7,700 kcal per kg you want to lose each week.
Protein is 1.8 g per kg of target weight, fat about 27 percent of calories,
sugar under 10 percent (WHO), and carbs the rest. Pace is capped at 1 percent
of body weight a week, and calories never go below 1,500 (men) or 1,200
(women). If a target date needs more than that, the plan says so and gives a
realistic date. These are starting points, not medical advice.

Protein is treated as a goal (more is fine). Calories, carbs, fat and sugar
are limits.

## Run it

Needs Python 3.10 or newer.

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp .env.example .env    # then put your Anthropic API key in it
.venv/bin/fatties set-password Polar     # asks for the password twice
.venv/bin/fatties set-password Partner
.venv/bin/fatties --port 8000
```

Open http://localhost:8000 and log in with a name from `FATTIES_PEOPLE` and
its password. Set `FATTIES_ANALYZER=fake` to try the app without spending API
credits.

## Login

Each person has their own password, set on the server with
`fatties set-password NAME`. Run it again to change a password; that signs
out every phone that logged in with the old one. Passwords are stored as
salted scrypt hashes, never in plain text.

- A login lasts 90 days in an HttpOnly, SameSite=Lax cookie.
- Browser `/api` routes need the cookie, apart from login/logout/session.
  The two dedicated widget read routes instead require a scoped bearer token;
  widget tokens cannot read the rest of the API or modify anything.
- Widget connections expire after 90 days. Logging out, changing your password,
  or disconnecting widgets revokes your connections without affecting your
  partner's. Reconnect from the Widget tab. Tokens are hashed in SQLite and
  stored in Scriptable's Keychain, never embedded in the downloadable script.
- After 10 wrong passwords for a name within 15 minutes, logins for that name
  are refused until the 15 minutes pass.
- The API docs pages (`/docs`, `/openapi.json`) are turned off.

## Put it on the VPS

Phones only allow the camera on HTTPS, and the login cookie is only marked
Secure over HTTPS, so run it behind a reverse proxy with a certificate. With
Caddy and a domain pointing at the VPS:

```
fatties.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

Keep `fatties` on `127.0.0.1` (the default) so only Caddy can reach it.
Uvicorn trusts the proxy headers from 127.0.0.1, so the app sees the request
as HTTPS and sets the cookie as Secure. Then open the address on each phone,
log in, and use "Add to Home Screen".

Run one server worker (the default). Before upgrading an existing installation,
stop the server and back up its SQLite database and existing photo directory.
Startup applies additive schema changes without deleting old meals or plans.
Configure private R2 photo storage using [the setup guide](docs/widget-setup.md).
The default local backend works without Cloudflare credentials for development;
its files are served only through authenticated endpoints, never as public assets.
Cloudflare sign-in is not required for local storage. When ready, use
`fatties migrate-photos-to-r2 --dry-run` and then `fatties migrate-photos-to-r2`
with R2 credentials configured to copy and verify existing photos before switching.

## Settings (`.env`)

| Name | Default | Meaning |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | none | Claude Console API key |
| `FATTIES_PEOPLE` | `Polar,Partner` | the two names, comma separated |
| `FATTIES_ANALYZER` | `claude` | `claude` or `fake` |
| `FATTIES_MODEL` | `claude-opus-5-5` | any Claude model with vision |
| `FATTIES_EFFORT` | `medium` | `low`, `medium` or `high` |
| `FATTIES_DB` | `data/fatties.db` | SQLite file |
| `FATTIES_TZ` | `Asia/Singapore` | decides which day a meal belongs to |
| `FATTIES_PHOTO_STORAGE` | `local` | private `local` storage or Cloudflare `r2` |
| `FATTIES_PHOTO_DIR` | beside the DB, `photos/` | local image/thumbnail directory |
| `FATTIES_PUBLIC_URL` | request origin | canonical HTTPS origin for widget links |
| `R2_ACCOUNT_ID`, `R2_BUCKET` | none | private Cloudflare R2 destination |
| `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | none | bucket-scoped R2 S3 credentials, server only |

`.env` is in `.gitignore`. Never commit a key; this repository is public.

## How the estimate works

`fatties/analysis.py` sends one Messages API request with the photo as an
image block and a strict JSON schema under `output_config.format`. The prompt
and schema come from the `meal_photo_analysis` workflow in my-harness, with
calories added per item. The app then cleans the answer: negative numbers
become 0, sugar above carbs is capped at carbs, and an item with macros but no
calories gets 4/4/9 calories. Totals are always summed by the app, never taken
from the model. If Claude declines a photo for policy reasons, the request
falls back to another model on the server (`fallbacks: "default"`).

One chicken rice photo on Claude Opus 5.5 at medium effort took about 17
seconds and used about 3,300 input and 1,000 to 1,400 output tokens, roughly
US$0.03 per photo.

## Tests

```bash
.venv/bin/pytest -q
.venv/bin/ruff check .
node --test tests/*.test.cjs
```

For the optional isolated browser test (fake analysis, temporary data, no API spend):

```bash
.venv/bin/python -m pip install -e '.[dev,ui]'
.venv/bin/playwright install chromium
FATTIES_UI_TESTS=1 .venv/bin/pytest -q tests/test_ui.py
```

The Scriptable tests simulate its documented native APIs. They verify cache,
authentication failure, expiry and race behavior; they do not replace installing
and checking the widget on an actual iPhone. iOS controls background refresh;
the script requests a refresh after 15 minutes without promising that interval.

## Not done yet

- Someone who knows a name can lock that person out for 15 minutes by
  guessing wrong 10 times. Fine for two people; revisit if it ever happens.
- Accuracy is unmeasured. There is no set of reference meals yet.
- No editing of a saved meal (delete and log it again), no manual entry
  without a photo, and no weight log.
- Old meal logs have no recoverable photo and display a placeholder.
