"""Custom exceptions for Chronicle."""


class ChronicleError(Exception):
    """Base exception for all Chronicle errors."""


class ScrapeError(ChronicleError):
    """Raised when the scrape orchestration fails."""


class FetchError(ChronicleError):
    """Raised when an HTTP request fails after all retries."""


class ParseError(ChronicleError):
    """Raised when parsing HTML or JSON fails."""


class SchemaError(ChronicleError):
    """Raised when data does not match the declared schema."""


class ContractViolation(ChronicleError):
    """Raised when a data contract is violated."""


class DriftDetected(ChronicleError):
    """Raised when significant schema or distribution drift is detected."""


class StorageError(ChronicleError):
    """Raised when the storage layer encounters an error."""