class DependencyUnavailable(Exception):
    """Secrets Manager or the session store could not be used. The request fails closed.

    `category` is a safe, fixed label (never provider text, secrets or user content).
    """

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


class AccessRejected(Exception):
    """A credential (cookie, grant, session) was not acceptable.

    `reason` is a safe category for logs. Callers return one uniform response for all
    reasons, so a client cannot learn whether a session exists.
    """

    def __init__(self, reason: str, session_id: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.session_id = session_id  # only for a session that ended idle, so its data can go
