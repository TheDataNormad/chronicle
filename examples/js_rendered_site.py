"""Example: scraping a JavaScript-rendered page with Playwright.

This site only renders the quotes after JavaScript runs — a plain
httpx fetch returns an empty shell. PlaywrightFetcher launches a
headless Chromium, waits for the content, and returns the final HTML.

Requires:
    pip install "chronicle-ds[browser]"
    playwright install chromium

Run with:
    python examples/js_rendered_site.py
"""

from chronicle import Scrape


def main() -> None:
    result = Scrape(
        url="https://quotes.toscrape.com/js/",
        selectors={
            "_container": "div.quote",
            "text":   "span.text",
            "author": "small.author",
            "tags":   "div.tags a.tag",
        },
        use_playwright=True,
        wait_for="div.quote",
        store=False,
    ).run()

    print(result)
    df = result.to_dataframe()
    print(df.head(10))


if __name__ == "__main__":
    main()