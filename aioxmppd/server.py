import asyncio
import platform
import random
import signal
import sys
from loguru import logger
from . import network


async def wakeup():
    # No va en windows
    # https://stackoverflow.com/questions/27480967/why-does-the-asyncios-event-loop-suppress-the-keyboardinterrupt-on-windows
    while True:
        await asyncio.sleep(1)  # random.random())


class GracefulExit(SystemExit):
    code = 1


def _raise_graceful_exit() -> None:
    raise GracefulExit()


class AioxmppServer:
    __slots__ = ("_host", "_port_client", "_port_server", "_ssl_context")

    def __init__(self, config):
        logger.add(
            config["logger"]["filename"],
            enqueue=True,
            format="<green>{time}</green> - <level>{level}: {message}</level>",
            rotation=config["logger"]["rotation"],
            level=config["logger"]["level"],
        )
        self._host = config["host"]["hostname"]
        self._port_client = config["host"]["ports"]["client"]
        self._port_server = config["host"]["ports"]["server"]
        self._ssl_context = None

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

    def start(self):
        logger.info("Starting server...")

        # Create SSL context
        # ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        # ssl_context.options |= (
        #    ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1 | ssl.OP_NO_COMPRESSION
        # )
        # ssl_context.load_cert_chain(certfile="cert.crt", keyfile="cert.key")
        # ssl_context.set_alpn_protocols(["xmpp"])

        loop = asyncio.get_event_loop()

        # Add handlers to signal events
        try:
            loop.add_signal_handler(signal.SIGINT, _raise_graceful_exit)
            loop.add_signal_handler(signal.SIGABRT, _raise_graceful_exit)
            loop.add_signal_handler(signal.SIGTERM, _raise_graceful_exit)
        except NotImplementedError:  # pragma: no cover
            # add_signal_handler is not implemented on Windows
            pass

        client_listener = None
        server_listener = None

        try:
            # Each client/server connection will create a new protocol instance

            client_corutine = loop.create_server(
                lambda: network.XMLStreamProtocol("jabber:client"),
                self._host,
                self._port_client,
                ssl=self._ssl_context,
            )
            server_corutine = loop.create_server(
                lambda: network.XMLStreamProtocol("jabber:server"),
                self._host,
                self._port_server,
                ssl=self._ssl_context,
            )

            client_listener = loop.run_until_complete(client_corutine)
            server_listener = loop.run_until_complete(server_corutine)

            logger.info(
                f"Server is listening clients on {client_listener.sockets[0].getsockname()}"
            )
            logger.info(
                f"Server is listening servers on {server_listener.sockets[0].getsockname()}"
            )

            # Run server forever until Ctrl + C is pressed
            main_task = loop.create_task(wakeup())
            loop.run_until_complete(main_task)

            loop.run_forever()
        except (GracefulExit, KeyboardInterrupt):  # pragma: no cover
            pass
        finally:
            # Close listeners
            if client_listener:
                client_listener.close()
            if server_listener:
                server_listener.close()
            self.stop(loop)
