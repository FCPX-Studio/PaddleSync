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
    - Invalid payload (ValidationError): permanent, retrying won't help. Logged and acked.
    - PermanentError (e.g. BundleIdConflictError): the event conflicts with existing data. Logged and acked.
    - Anything else (DB down, MissingDependencyError, EmailConflictError...): treated as transient and re-raised. EventConsumer
      logs it, retries it later, and moves it to `paddle:events:dead` after 5 attempts.

    Args:
        event (PaddleEvent): Any paddle event
    """
    handler = HANDLERS.get(event.event_type)
    if handler is None:
        logger.debug("Ignoring %s %s", event.event_type, event.paddle_event_id)
        return

    try:
        await handler(event)
    except ValidationError:
        logger.exception("Invalid payload for %s %s, skipped", event.event_type, event.paddle_event_id)
        return
    except PermanentError:
        # No payload in the log: it can hold the customer's email
        logger.exception("Can't apply %s %s, skipped", event.event_type, event.paddle_event_id)
        return

    logger.info("Processed %s %s", event.event_type, event.paddle_event_id)


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
