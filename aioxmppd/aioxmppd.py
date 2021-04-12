import asyncio
import os
import platform
import random
import signal
import sys
from loguru import logger


async def dummy_task():
    while True:
        logger.debug("Hago cosas...")
        await asyncio.sleep(random.random())


class GracefulExit(SystemExit):
    code = 1


def _raise_graceful_exit() -> None:
    raise GracefulExit()


class AioxmppServer:
    def __init__(self, log_level, log_file, log_rotation):
        logger.add(
            log_file,
            enqueue=True,
            format="<green>{time}</green> - <level>{level}: {message}</level>",
            rotation=log_rotation,
            level=log_level,
        )

    def stop(self, loop):
        logger.info("Stopping the server...")

        tasks_to_cancel = asyncio.all_tasks(loop)
        logger.info(f"Cancelling {len(tasks_to_cancel)} outstanding tasks")
        for task in tasks_to_cancel:
            task.cancel()
        loop.run_until_complete(
            asyncio.gather(*tasks_to_cancel, loop=loop, return_exceptions=True)
        )
        for task in tasks_to_cancel:
            if task.cancelled():
                continue
            if task.exception() is not None:
                loop.call_exception_handler(
                    {
                        "message": "unhandled exception during asyncio.run() shutdown",
                        "exception": task.exception(),
                        "task": task,
                    }
                )

        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
        asyncio.set_event_loop(None)
        logger.info("Server stopped")

    def run(self):
        pid = os.getpid()
        logger.info(f"Running server as PID: {pid}")

        loop = asyncio.get_event_loop()

        try:
            loop.add_signal_handler(signal.SIGINT, _raise_graceful_exit)
            loop.add_signal_handler(signal.SIGTERM, _raise_graceful_exit)
        except NotImplementedError:  # pragma: no cover
            # add_signal_handler is not implemented on Windows
            pass

        try:
            main_task = loop.create_task(dummy_task())
            loop.run_until_complete(main_task)
        except (GracefulExit, KeyboardInterrupt):  # pragma: no cover
            pass
        finally:
            self.stop(loop)
