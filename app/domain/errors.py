from __future__ import annotations


class DomainError(Exception):
    pass


class DomainValidationError(DomainError):
    pass


class DomainInvariantError(DomainError):
    pass


class DomainDependencyError(DomainError):
    pass


class UnsupportedFormatError(DomainValidationError):
    pass


class NormalizationParseError(DomainError):
    pass
