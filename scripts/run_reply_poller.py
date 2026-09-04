import asyncio
import logging
import os
import sys
 
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
 
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
 
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
 
from app.db.connection import init_pool, close_pool
from app.graph.hitl import dispatch_resume
from app.notifications.reply_poller import poll_once
 
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("run_reply_poller")
 
POLL_INTERVAL_SECONDS = 30

async def main():
    pool = await init_pool()
 
    async with AsyncPostgresSaver.from_conn_string(os.environ["DATABASE_URL"]) as checkpointer:
        await checkpointer.setup()
 
        logger.info("Reply poller started. Polling every %ss. Ctrl+C to stop.", POLL_INTERVAL_SECONDS)
 
        while True:
            try:
                resolved = await poll_once(pool)
                for approval in resolved:
                    try:
                        await dispatch_resume(
                            pool, checkpointer,
                            agent=approval["agent"],
                            thread_id=approval["thread_id"],
                            customer_id=approval["customer_id"],
                            decision_status=approval["status"],
                        )
                        logger.info("Successfully resumed thread_id=%s", approval["thread_id"])
                    except Exception:
                        # One bad resume shouldn't kill the poller loop or
                        # block other pending approvals from being handled.
                        logger.exception("Failed to resume thread_id=%s", approval["thread_id"])
            except Exception:
                # Same reasoning: one bad poll cycle (IMAP hiccup, etc.)
                # shouldn't crash the whole long-lived process.
                logger.exception("Poll cycle failed")
 
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
 
 
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass