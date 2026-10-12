"""Chronicle — the web scraping library that remembers."""

from chronicle._version import __version__
from chronicle.core import Scrape, ScrapeResult

__all__ = ["Scrape", "ScrapeResult", "__version__"]
