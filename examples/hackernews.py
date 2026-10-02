"""Example: scrape Hacker News front page with selectors.

Run with:  python examples/hackernews.py
"""

from chronicle import Scrape


def main() -> None:
    result = Scrape(
        url="https://news.ycombinator.com",
        selectors={
            "_container": "tr.athing",
            "rank":  "span.rank",
            "title": "span.titleline > a",
            "url":   "span.titleline > a@href",
        },
        store=False,
    ).run()

    print(result)
    df = result.to_dataframe()
    print(df.head(10))


if __name__ == "__main__":
    main()