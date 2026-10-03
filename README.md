
# Disney Cruise Data Scraper

## What it does

This Playwright scraper:

1. Opens the Disney Cruise Line India page.
2. Clicks `View Dates`.
3. Applies optional filters programmatically.
4. Waits for dynamically loaded cruise results.
5. Extracts cruise-card content from the rendered DOM.
6. Handles Disney's lazy-loaded results by scrolling.
7. Processes at least 35 result batches by default.
8. Deduplicates records.
9. Ensures `location` is not empty.
10. Saves temporary JSON and final CSV output.

## Installation

```powershell
pip install -r requirements.txt
playwright install chromium
```

## Run

Visible browser:

```powershell
python scraper.py --headed
```

The default run processes at least 35 batches:

```powershell
python scraper.py
```

## Optional filters

The challenge screenshots do not specify exact filter values, so the default
run preserves Disney's `Any Date`, `Any Destination`, and `Any Port` settings.

When the challenge requires a concrete value, set the corresponding environment
variable before running.

PowerShell examples:

```powershell
$env:DCL_DESTINATION="Bahamas"
$env:DCL_DEPARTURE_PORT="Port Canaveral"
python scraper.py --headed
```

For a date filter:

```powershell
$env:DCL_DATE_FILTER="2027"
python scraper.py --headed
```

Use the exact visible option text from the Disney filter menu.

## Output

The scraper creates:

```text
data/
├── raw_cruises.json
└── disney_cruises.csv
```

CSV columns:

- cruise_name
- ship
- departure_port
- destination
- location
- duration
- guests
- price_usd
- price_inr
- available_dates
- rate_details
- special_offer
- url
- source_page
- raw_content

`raw_content` preserves the raw text collected from the cruise card, while the
other columns expose the corresponding sections as separate CSV fields.

## Important implementation detail

Disney's current results page is dynamically rendered and contains many nested
responsive DOM elements. The scraper therefore performs the card discovery in
one browser-side JavaScript pass instead of making tens of thousands of
individual Playwright locator calls.

If the site exposes a conventional `Next` control, the scraper uses it.
Otherwise it treats each lazy-load/scroll batch as a result page and continues
until at least 35 batches have been processed and the result set stabilizes.

## Validation

The script will fail instead of silently producing an empty/invalid CSV when:

- zero records are extracted;
- a required `cruise_name` is missing;
- `location` is missing;
- `raw_content` is missing;
- an empty cell remains in the final CSV.
