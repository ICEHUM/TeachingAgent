class TeachingControlError(RuntimeError):
    """Base error for deterministic teaching-control contract violations."""


class InvalidContextError(TeachingControlError):
    """The event does not belong to the current attempt, task, or actor."""


class AuthorizationError(TeachingControlError):
    """The actor is not authorized for the requested transition."""


class StaleStateVersionError(TeachingControlError):
    """The caller attempted to continue from a stale business-state version."""


class PolicyViolationError(TeachingControlError):
    """A generated result exceeded the server-side teaching policy."""


class StageAdvanceError(TeachingControlError):
    """A stage transition was attempted without a passing assessment."""
