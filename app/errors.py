"""Domain errors raised by services and domain logic.

These know nothing about HTTP. main.py maps each one to a status code in a
single exception handler, so no service or router builds error responses itself.
Every message is safe to show to API clients.
"""


class DomainError(Exception):
    """Base class for an expected, client-caused failure."""


class NotFoundError(DomainError):
    """A referenced group or user does not exist. Mapped to 404."""


class RuleViolationError(DomainError):
    """The request is well-formed but breaks a business rule. Mapped to 422."""


class ConflictError(DomainError):
    """The request clashes with existing data, e.g. a duplicate phone number. Mapped to 409."""
