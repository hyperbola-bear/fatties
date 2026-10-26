# fatties

A small meal tracker for two people who are losing weight together. Each meal
or snack gets a photo. Claude estimates calories, carbs, protein, fat and
sugar for Singapore food, you adjust the portions, and the app compares what
each person ate with their own daily and weekly targets.

## What it does

- **Log**: take a photo, add an optional note ("ate half", "kopi siew dai"),
  and get an estimate per item. Scale any item (x0.5 to x2) or remove it, then
  save it for whoever ate it. Photos are never stored, only the numbers.
- **Day**: calories and each macro against the target for both people, with
  what is left, and the meals logged that day.
- **Week** (Monday to Sunday): calories per day against the target line,
  average per logged day, days over target, days the protein goal was hit,
  each macro against the target and against last week, and a side-by-side
  table of both people.
- **Targets**: type targets in, or work them out from weight, height, age,
  sex, exercise and pace (Mifflin-St Jeor minus a deficit; protein about
  1.8 g/kg, fat about 27 percent of calories, sugar under 10 percent, carbs the
  rest). These are starting points, not medical advice.

Protein is treated as a goal (more is fine). Calories, carbs, fat and sugar
are limits.

## Run it

Needs Python 3.10 or newer.

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp .env.example .env    # then put your Anthropic API key in it
.venv/bin/fatties --port 8000
```

Open http://localhost:8000. On a phone, use "Add to Home Screen". Phones only
allow the camera on HTTPS, so for real use put it behind a reverse proxy with
a certificate (Caddy does this in two lines).

Set `FATTIES_ANALYZER=fake` to try the app without spending API credits.

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
```

## Not done yet

- **No login.** Anyone who can reach the server can read the log and spend
  API credits. Add a password before putting it on a public address.
- Accuracy is unmeasured. There is no set of reference meals yet.
- No editing of a saved meal (delete and log it again), no manual entry
  without a photo, and no weight log.
