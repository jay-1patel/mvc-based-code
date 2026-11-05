"""Background listener for send2.digital Opt-Out / Log updates.

DISABLED — the external polling endpoint is no longer active.
The poll_optout_status() coroutine now returns immediately.
"""

import asyncio
import logging

logger = logging.getLogger("OptoutListener")


async def poll_optout_status(stop_event: asyncio.Event = None) -> None:
    """Disabled — returns immediately."""
    logger.info("Opt-out listener DISABLED — skipping polling")
    if stop_event:
        stop_event.set()
