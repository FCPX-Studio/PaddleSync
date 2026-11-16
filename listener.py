from logger import logger
from handlers import HANDLERS
from schemas.exceptions import PermanentError

from events import EventConsumer, PaddleEvent, close_redis
from events.streams import PADDLE_EVENTS

from pydantic import ValidationError

import asyncio
import signal


async def hot_event(event: PaddleEvent) -> None:
    """
    Catch an event from the listener, validate it, build the ORM model and save it to the DB.
    Unbound events are ignored.

    Must be idempotent: the same event can be delivered more than once!

    Error policy:
    - Invalid payload (ValidationError): permanent, retrying won't help. Logged (one line, the invalid fields) and acked.
    - PermanentError (e.g. BundleIdConflictError): the event conflicts with existing data. Logged (one line) and acked.
    - Anything else (DB down, MissingDependencyError, EmailConflictError...): treated as transient and re-raised. EventConsumer
      logs it, retries it later, and moves it to `paddle:events:dead` after 5 attempts.

    Args:
        event (PaddleEvent): Any paddle event
    """
    handler = HANDLERS.get(event.event_type)
    if handler is None:
        logger.debug("Ignoring %s %s", event.event_type, event.paddle_event_id)
        return

    # Rejected events are a data problem (fix it in the Paddle dashboard), not a service failure:
    # one WARNING line with the reason, no traceback.
    try:
        applied = await handler(event)
    except ValidationError as e:
        logger.warning(
            "Invalid payload for %s %s, skipped: %s", event.event_type, event.paddle_event_id, _validation_summary(e)
        )
        return
    except PermanentError as e:
        logger.warning("Can't apply %s %s, skipped: %s", event.event_type, event.paddle_event_id, e)
        return

    # A handler that skips an event on purpose logs why itself: one line per event
    if applied:
        logger.info("Processed %s %s", event.event_type, event.paddle_event_id)


def _validation_summary(error: ValidationError) -> str:
    """
    Field locations and messages, e.g. "custom_data.bundle_id: Field required".
    Never the input values: they can hold customer data (email, name).
    """
    return "; ".join(
        f"{'.'.join(str(part) for part in err['loc']) or 'payload'}: {err['msg']}"
        for err in error.errors(include_input=False, include_url=False)
    )


async def main() -> None:
    """
    Consume until SIGINT/SIGTERM. An infrastructure failure (Redis down...) is not caught:
    the process exits non-zero and Docker's restart policy brings it back.
    """
    consumer = EventConsumer(PADDLE_EVENTS, group="paddlesync")

    # Clean shutdown on Ctrl+C / `docker stop`. A message interrupted mid-handler
    # stays pending in Redis and is retried, which is fine since handlers are idempotent.
    task = asyncio.current_task()
    assert task is not None  # always set inside asyncio.run()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, task.cancel)

    try:
        await consumer.run(hot_event)
    except asyncio.CancelledError:
        logger.info("Shutting down")
    finally:
        await close_redis()


if __name__ == "__main__":
    asyncio.run(main())
