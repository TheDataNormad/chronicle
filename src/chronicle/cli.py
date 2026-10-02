"""Chronicle command-line interface."""

from __future__ import annotations

from pathlib import Path

import typer

from chronicle import Scrape
from chronicle.storage import Storage

app = typer.Typer(
    name="chronicle",
    help="The web scraping library that remembers.",
    no_args_is_help=True,
)


@app.command()
def version() -> None:
    """Print the Chronicle version."""
    from chronicle import __version__
    typer.echo(f"chronicle {__version__}")


@app.command()
def scrape(
    url: str = typer.Argument(..., help="URL to scrape"),
    out: Path = typer.Option(
        Path("out.parquet"), "--out", "-o", help="Output file (.parquet or .csv)"
    ),
    pages: int = typer.Option(1, "--pages", "-p", help="Number of pages"),
    rate_limit: float = typer.Option(
        1.0, "--rate-limit", "-r", help="Requests per second"
    ),
) -> None:
    """Scrape a URL and save the result."""
    typer.echo(f"Scraping {url}...")
    result = Scrape(url, pages=pages, rate_limit=rate_limit).run()

    df = result.to_dataframe()
    typer.echo(f"Scraped {len(df)} rows")

    if out.suffix == ".csv":
        df.to_csv(out, index=False)
    else:
        df.to_parquet(out, index=False)
    typer.echo(f"Saved to {out}")


@app.command()
def runs(
    url: str = typer.Argument(..., help="URL to list runs for"),
    limit: int = typer.Option(10, "--limit", "-n", help="Max runs to show"),
) -> None:
    """List recent runs for a URL."""
    storage = Storage()
    rows = storage.list_runs(url=url, limit=limit)
    if not rows:
        typer.echo("No runs found.")
        return
    for r in rows:
        typer.echo(
            f"{r['run_id']}  rows={r['row_count']}  "
            f"valid={r['valid_count']}  invalid={r['invalid_count']}"
        )


if __name__ == "__main__":
    app()