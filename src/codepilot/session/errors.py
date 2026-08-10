class UnsupportedSessionSchema(RuntimeError):
    def __init__(self, current: int, supported: int) -> None:
        super().__init__(f"unsupported Session schema version: {current}; expected {supported}")


class SessionProtocolMismatch(RuntimeError):
    """The persisted session does not satisfy the Native replay protocol."""
