class MissingDependency(Exception):
    """
    A row the webhook refers to isn't in the DB yet (e.g. a transaction arrives before its
    customer or price webhook). Transient: the event is retried later by EventConsumer.
    """


class PermanentError(Exception):
    """
    The event can never be applied, whatever the retries (e.g. it conflicts with existing data).
    Logged and acked by the listener, like a ValidationError.
    """


class EmailConflict(PermanentError):
    """
    Another Paddle customer already uses this email (User.email is unique).
    """
