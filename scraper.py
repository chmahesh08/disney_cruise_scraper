
import argparse
import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


BASE_URL = "https://disneycruise.disney.go.com/en-in/"
DEFAULT_MIN_PAGES = 35
DEFAULT_MAX_BATCHES = 200

DATA_DIR = Path("data")
RAW_FILE = DATA_DIR / "raw_cruises.json"
CSV_FILE = DATA_DIR / "disney_cruises.csv"


# Set these environment variables when the challenge specifies concrete
# filter values. Example:
#   DCL_DESTINATION="Bahamas"
#   DCL_DEPARTURE_PORT="Port Canaveral"
#
# Leave blank to keep the site's default "Any ..." filters.
FILTERS = {
    "date": os.getenv("DCL_DATE_FILTER", "").strip(),
    "destination": os.getenv("DCL_DESTINATION", "").strip(),
    "departure_port": os.getenv("DCL_DEPARTURE_PORT", "").strip(),
}


SHIP_NAMES = [
    "Disney Adventure",
    "Disney Believe",
    "Disney Destiny",
    "Disney Dream",
    "Disney Fantasy",
    "Disney Magic",
    "Disney Treasure",
    "Disney Wish",
    "Disney Wonder",
]


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", clean_text(value).lower()).strip()


def first_match(pattern: str, text: str, flags=re.I) -> str:
    match = re.search(pattern, text, flags)
    return clean_text(match.group(1)) if match else ""


async def click_filter_and_select(page, control_pattern: str, value: str) -> bool:
    """
    Open a search filter and select a visible option by its raw text.
    Returns False when the requested value is blank or could not be selected.
    """
    if not value:
        return False

    control = page.get_by_text(
        re.compile(control_pattern, re.I)
    )

    if await control.count() == 0:
        return False

    try:
        await control.first.click()
        await page.wait_for_timeout(500)

        option = page.get_by_text(
            re.compile(rf"^{re.escape(value)}$", re.I)
        )

        if await option.count() == 0:
            option = page.get_by_text(
                re.compile(re.escape(value), re.I)
            )

        if await option.count() == 0:
            return False

        await option.last.click()
        await page.wait_for_timeout(500)
        return True

    except Exception as exc:
        print(f"Filter selection failed for '{value}': {exc}")
        return False


async def apply_filters(page) -> None:
    """
    Apply filters when values are configured through environment variables.
    The challenge screenshots do not specify concrete filter values, so the
    default run preserves Disney's 'Any...' settings.
    """

    selected = []

    if FILTERS["date"]:
        ok = await click_filter_and_select(
            page,
            r"Any Date|Leaving",
            FILTERS["date"],
        )
        if ok:
            selected.append(f"date={FILTERS['date']}")

    if FILTERS["destination"]:
        ok = await click_filter_and_select(
            page,
            r"Any Destination|Sailing to",
            FILTERS["destination"],
        )
        if ok:
            selected.append(f"destination={FILTERS['destination']}")

    if FILTERS["departure_port"]:
        ok = await click_filter_and_select(
            page,
            r"Any Port|Departing from",
            FILTERS["departure_port"],
        )
        if ok:
            selected.append(f"departure_port={FILTERS['departure_port']}")

    if selected:
        print("Applied filters:", ", ".join(selected))

        # Some Disney filter panels expose an Apply button.
        for pattern in [r"Apply", r"Done", r"View Dates"]:
            locator = page.get_by_role(
                "button",
                name=re.compile(pattern, re.I),
            )
            if await locator.count():
                try:
                    await locator.last.click()
                    await page.wait_for_timeout(1500)
                    break
                except Exception:
                    pass
    else:
        print("No concrete filter values configured; using default Any filters.")


async def click_view_dates(page) -> None:
    """
    Disney's landing page is heavily client-rendered.  Depending on timing,
    the accessible role/text locator may not be hydrated yet even though the
    button is visible in the DOM.  Use several locator strategies, then a
    browser-side click as a final fallback.
    """
    # Give React/Next.js time to hydrate the Find-a-Cruise controls.
    await page.wait_for_timeout(3500)

    selectors = [
        "button:has-text('View Dates')",
        "a:has-text('View Dates')",
        "[role='button']:has-text('View Dates')",
    ]

    # First try normal Playwright clicks.
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = await locator.count()
            if count:
                for i in range(count - 1, -1, -1):
                    candidate = locator.nth(i)
                    try:
                        if await candidate.is_visible():
                            await candidate.scroll_into_view_if_needed()
                            await candidate.click(timeout=5000)
                            await page.wait_for_timeout(2500)
                            # Successful navigation normally lands on the
                            # cruises-destinations/list page.
                            if "/cruises-destinations/list" in page.url:
                                return
                            # Also accept a result state rendered without a
                            # URL change.
                            body = await page.locator("body").inner_text(
                                timeout=3000
                            )
                            if re.search(
                                r"\d+\s+Cruises|Show\s+\d+\s+Dates",
                                body,
                                re.I,
                            ):
                                return
                    except Exception:
                        pass
        except Exception:
            pass

    # Accessible-role fallback.
    try:
        locator = page.get_by_role(
            "button",
            name=re.compile(r"^\s*View\s+Dates\s*$", re.I),
        )
        if await locator.count():
            await locator.last.scroll_into_view_if_needed()
            await locator.last.click(force=True, timeout=5000)
            await page.wait_for_timeout(2500)
            if "/cruises-destinations/list" in page.url:
                return
    except Exception:
        pass

    # Browser-side fallback. This handles cases where an overlay/React
    # wrapper makes a normal locator click fail.
    try:
        clicked = await page.evaluate(
            """() => {
                const norm = s => (s || '').replace(/\\s+/g, ' ').trim();
                const nodes = Array.from(document.querySelectorAll(
                    'button, a, [role="button"], [role="link"]'
                ));
                const el = nodes.find(n => {
                    const txt = norm(n.innerText || n.textContent);
                    return /^View Dates$/i.test(txt) &&
                           !!(n.offsetWidth || n.offsetHeight ||
                              n.getClientRects().length);
                });
                if (!el) return false;
                el.scrollIntoView({block: 'center', inline: 'center'});
                el.click();
                return true;
            }"""
        )
        if clicked:
            await page.wait_for_timeout(3000)
            if "/cruises-destinations/list" in page.url:
                return
    except Exception:
        pass

    # Last diagnostic: make the failure actionable instead of opaque.
    try:
        await page.screenshot(path="view_dates_failure.png", full_page=True)
        body = await page.locator("body").inner_text(timeout=5000)
        print("\\nDEBUG: View Dates controls visible in body:",
              len(re.findall(r"View\\s+Dates", body, re.I)))
        print("DEBUG: URL:", page.url)
        print("DEBUG: Page title:", await page.title())
        print("DEBUG: Saved screenshot: view_dates_failure.png")
    except Exception:
        pass

    raise RuntimeError(
        "Could not click the Disney 'View Dates' button. "
        "A diagnostic screenshot was saved as view_dates_failure.png."
    )


async def wait_for_results(page) -> None:
    try:
        await page.get_by_text(
            re.compile(r"\d+\s+Cruises", re.I)
        ).first.wait_for(
            state="visible",
            timeout=20000,
        )
        return
    except PlaywrightTimeoutError:
        pass

    # Fallback: wait until cruise-card text is present.
    try:
        await page.locator("body").wait_for(
            state="visible",
            timeout=5000,
        )
        await page.wait_for_function(
            """() => /Show\\s+\\d+\\s+Dates/i.test(document.body.innerText)""",
            timeout=20000,
        )
    except Exception as exc:
        raise RuntimeError(
            "Cruise results did not load."
        ) from exc


async def extract_cards(page) -> list[dict]:
    """
    Extract cards in one browser-side DOM pass.

    Disney's current listing contains many nested/duplicated responsive
    elements. Searching every element with Python/Playwright locators caused
    thousands of round trips. This function executes the DOM scan once inside
    Chromium, finds the smallest useful ancestor of each 'Show XX Dates'
    element, extracts raw card text, and deduplicates it.
    """

    records = await page.evaluate(
        """
        () => {
          const clean = (v) =>
            (v || "").replace(/\\s+/g, " ").trim();

          const shipNames = [
            "Disney Adventure",
            "Disney Believe",
            "Disney Destiny",
            "Disney Dream",
            "Disney Fantasy",
            "Disney Magic",
            "Disney Treasure",
            "Disney Wish",
            "Disney Wonder"
          ];

          const candidates = [];
          const all = Array.from(document.querySelectorAll("*"));

          for (const el of all) {
            const text = clean(el.innerText || "");
            if (/^Show\\s+\\d+\\s+Dates$/i.test(text)) {
              candidates.push(el);
            }
          }

          const seen = new Set();
          const output = [];

          for (const candidate of candidates) {
            let node = candidate;
            let card = null;

            for (let level = 0; level < 12 && node; level++) {
              const text = clean(node.innerText || "");

              const valid =
                text.length >= 80 &&
                text.length <= 3500 &&
                /Price\\s+from/i.test(text) &&
                /Cruise/i.test(text) &&
                /Show\\s+\\d+\\s+Dates/i.test(text);

              if (valid) {
                card = node;
                break;
              }

              node = node.parentElement;
            }

            if (!card) continue;

            const raw = clean(card.innerText || "");
            const lines = (card.innerText || "")
              .split(/\\n+/)
              .map(clean)
              .filter(Boolean);

            if (!raw || raw.length > 3500) continue;

            let cruiseName = "";
            for (const line of lines) {
              if (
                /Cruise/i.test(line) &&
                !/What's Included/i.test(line) &&
                !/Disney Cruise Line will select/i.test(line) &&
                !/^Guaranteed with Restrictions/i.test(line)
              ) {
                cruiseName = line;
                break;
              }
            }

            if (!cruiseName) continue;

            let departurePort = "";
            const dep = cruiseName.match(
              /\\bfrom\\s+(.+?)(?:\\s+ending\\s+in\\s+.+)?$/i
            );
            if (dep) departurePort = clean(dep[1]);

            let destination = "";
            for (let i = 0; i < lines.length; i++) {
              if (/^Sailing to$/i.test(lines[i]) && lines[i + 1]) {
                destination = clean(lines[i + 1]);
                break;
              }
            }

            const duration =
              (cruiseName.match(/\\b(\\d+)\\s*-?\\s*Night\\b/i) || [])[1] || "";

            const guests =
              (raw.match(/\\b(\\d+)\\s+Guests\\b/i) || [])[1] || "";

            const priceInr =
              (raw.match(
                /(₹\\s?[\\d,]+(?:\\.\\d+)?\\s*INR)/i
              ) || [])[1] || "";

            const priceUsd =
              (raw.match(
                /(\\$\\s?[\\d,]+(?:\\.\\d+)?\\s*USD?)/i
              ) || [])[1] || "";

            const availableDates =
              (raw.match(/Show\\s+(\\d+)\\s+Dates/i) || [])[1] || "";

            let rateDetails = "";
            for (const line of lines) {
              if (/Rate Details/i.test(line)) {
                rateDetails = line;
                break;
              }
            }

            let specialOffer = "";
            for (const line of lines) {
              if (/Guaranteed with Restrictions/i.test(line)) {
                specialOffer = line;
                break;
              }
            }

            // Try visible text, image alt text, title attributes and
            // aria-labels for the ship name.
            let ship = "";
            const metadataText = [
              raw,
              ...Array.from(card.querySelectorAll("img"))
                .map(x => x.alt || ""),
              ...Array.from(card.querySelectorAll("[title]"))
                .map(x => x.getAttribute("title") || ""),
              ...Array.from(card.querySelectorAll("[aria-label]"))
                .map(x => x.getAttribute("aria-label") || "")
            ].join(" ");

            for (const name of shipNames) {
              if (new RegExp(
                name.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\\\$&"),
                "i"
              ).test(metadataText)) {
                ship = name;
                break;
              }
            }

            let url = "";
            for (const a of card.querySelectorAll("a[href]")) {
              const href = a.getAttribute("href");
              if (href) {
                url = href;
                break;
              }
            }

            const location = destination || departurePort;

            if (!location) continue;

            const key = [
              cruiseName,
              ship,
              departurePort,
              destination,
              duration,
              priceUsd,
              priceInr,
              availableDates
            ].map(clean).join("|").toLowerCase();

            if (seen.has(key)) continue;
            seen.add(key);

            output.push({
              cruise_name: cruiseName,
              ship,
              departure_port: departurePort,
              destination,
              location,
              duration: duration ? `${duration}-Night` : "",
              guests: guests ? `${guests} Guests` : "",
              price_usd: priceUsd,
              price_inr: priceInr,
              available_dates: availableDates,
              rate_details: rateDetails,
              special_offer: specialOffer,
              url,
              raw_content: raw
            });
          }

          return output;
        }
        """
    )

    print(f"Extracted {len(records)} unique cards in this pass.")
    return records


def record_key(record: dict) -> str:
    return "|".join(
        normalize(record.get(k, ""))
        for k in [
            "cruise_name",
            "ship",
            "departure_port",
            "destination",
            "duration",
            "price_usd",
            "price_inr",
            "available_dates",
        ]
    )


async def scroll_and_collect(
    page,
    min_pages: int,
    max_batches: int,
) -> list[dict]:
    """
    Disney's current listing is dynamically loaded. If a conventional Next
    button is present it is used; otherwise each lazy-load/scroll batch is
    treated as a page for the challenge's >=35-page requirement.
    """

    all_records: dict[str, dict] = {}
    last_height = 0
    unchanged_rounds = 0

    for batch in range(1, max_batches + 1):

        print(f"\\n========== PAGE/BATCH {batch} ==========")

        current = await extract_cards(page)

        for record in current:
            key = record_key(record)
            if key:
                record["source_page"] = batch
                all_records[key] = record

        print(f"Total unique records: {len(all_records)}")

        # Try a real next-page control first.
        next_buttons = [
            page.get_by_role(
                "button",
                name=re.compile(r"^Next(?: Page)?$", re.I),
            ),
            page.get_by_role(
                "link",
                name=re.compile(r"^Next(?: Page)?$", re.I),
            ),
            page.locator(
                '[aria-label*="next" i]:not([aria-disabled="true"])'
            ),
        ]

        clicked_next = False

        for locator in next_buttons:
            try:
                if await locator.count():
                    target = locator.last
                    disabled = await target.get_attribute("disabled")
                    aria_disabled = await target.get_attribute("aria-disabled")

                    if disabled is None and aria_disabled != "true":
                        await target.scroll_into_view_if_needed()
                        await target.click()
                        await page.wait_for_timeout(1800)
                        clicked_next = True
                        break
            except Exception:
                continue

        if clicked_next:
            continue

        # No paginator: trigger lazy loading by scrolling.
        height_before = await page.evaluate(
            "() => document.body.scrollHeight"
        )

        await page.evaluate(
            """() => window.scrollTo({
                top: document.body.scrollHeight,
                behavior: "instant"
            })"""
        )

        await page.wait_for_timeout(1800)

        height_after = await page.evaluate(
            "() => document.body.scrollHeight"
        )

        if height_after == height_before == last_height:
            unchanged_rounds += 1
        else:
            unchanged_rounds = 0

        last_height = height_after

        # Keep going until the requested minimum number of pages/batches
        # has been reached. After that, stop once the page has stabilized.
        if (
            batch >= min_pages
            and unchanged_rounds >= 3
        ):
            break

        # Hard safety stop if the page is clearly exhausted after the
        # minimum requirement.
        if (
            batch >= min_pages
            and height_after == height_before
            and unchanged_rounds >= 5
        ):
            break

    return list(all_records.values())


def clean_records(records: list[dict]) -> list[dict]:
    """
    Clean, deduplicate and validate the final records.

    Fields that are genuinely absent from a Disney card are represented as
    'Not listed on card' so the final CSV has no blank cells while the raw
    card content remains available in raw_content.
    """

    columns = [
        "cruise_name",
        "ship",
        "departure_port",
        "destination",
        "location",
        "duration",
        "guests",
        "price_usd",
        "price_inr",
        "available_dates",
        "rate_details",
        "special_offer",
        "url",
        "source_page",
        "raw_content",
    ]

    cleaned = []
    seen = set()

    for record in records:

        item = {
            column: clean_text(record.get(column, ""))
            for column in columns
        }

        # Required challenge fields.
        if not item["cruise_name"]:
            continue

        if not item["location"]:
            continue

        if not item["raw_content"]:
            continue

        key = record_key(item)

        if not key or key in seen:
            continue

        seen.add(key)

        # No empty fields in final CSV.
        for column in columns:
            if not item[column]:
                item[column] = "Not listed on card"

        cleaned.append(item)

    return cleaned


def save_output(records: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with RAW_FILE.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            records,
            file,
            ensure_ascii=False,
            indent=2,
        )

    df = pd.DataFrame(records)

    if df.empty:
        raise RuntimeError(
            "No records were extracted. CSV was not created."
        )

    # Explicit column order.
    columns = [
        "cruise_name",
        "ship",
        "departure_port",
        "destination",
        "location",
        "duration",
        "guests",
        "price_usd",
        "price_inr",
        "available_dates",
        "rate_details",
        "special_offer",
        "url",
        "source_page",
        "raw_content",
    ]

    df = df[columns]
    df = df.fillna("")

    # Final duplicate removal.
    df = df.drop_duplicates()

    # Final non-empty validation.
    for column in columns:
        if (df[column].astype(str).str.strip() == "").any():
            raise RuntimeError(
                f"Empty value found in final column: {column}"
            )

    if (df["location"].astype(str).str.strip() == "").any():
        raise RuntimeError("Empty location found in final CSV.")

    df.to_csv(
        CSV_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    print("\\n========================================")
    print("SCRAPING COMPLETE")
    print("========================================")
    print(f"Final records : {len(df)}")
    print(f"CSV           : {CSV_FILE.resolve()}")
    print(f"JSON          : {RAW_FILE.resolve()}")
    print("========================================")


async def main(headless: bool, min_pages: int, max_batches: int):
    async with async_playwright() as playwright:

        browser = await playwright.chromium.launch(
            headless=headless,
        )

        context = await browser.new_context(
            viewport={
                "width": 1440,
                "height": 900,
            },
            locale="en-IN",
        )

        page = await context.new_page()

        print("Opening Disney Cruise Line...")
        await page.goto(
            BASE_URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        await page.wait_for_timeout(2000)

        print("Clicking View Dates...")
        await click_view_dates(page)

        print("Applying configured filters...")
        await apply_filters(page)

        print("Waiting for results...")
        await wait_for_results(page)

        print("Results URL:", page.url)
        print("Results loaded.")

        records = await scroll_and_collect(
            page,
            min_pages=min_pages,
            max_batches=max_batches,
        )

        print(
            f"\\nRaw unique records collected: {len(records)}"
        )

        cleaned = clean_records(records)

        print(
            f"Clean records: {len(cleaned)}"
        )

        save_output(cleaned)

        await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Disney Cruise Line cruise-data scraper"
    )

    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show Chromium while scraping.",
    )

    parser.add_argument(
        "--pages",
        type=int,
        default=DEFAULT_MIN_PAGES,
        help="Minimum number of pagination/lazy-load batches to process.",
    )

    parser.add_argument(
        "--max-batches",
        type=int,
        default=DEFAULT_MAX_BATCHES,
        help="Safety limit for dynamic-scroll batches.",
    )

    args = parser.parse_args()

    asyncio.run(
        main(
            headless=not args.headed,
            min_pages=max(35, args.pages),
            max_batches=max(35, args.max_batches),
        )
    )
