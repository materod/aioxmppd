import asyncio
import pytest

from click.testing import CliRunner
from loguru import logger
from multiprocessing import Process
from aioxmppd.aioxmppd import AioxmppServer
from slixmpp import ClientXMPP


@pytest.fixture
def config():
    config = {
        "logger": {"level": "DEBUG", "filename": "test_aioxmppd.log", "rotation": None},
        "host": {
            "hostname": "localhost",
            "ports": {"client": 5222, "server": 5269},
        },
    }
    return config


@pytest.fixture()
def axiompdd_server(config):
    server = AioxmppServer(config)
    process = Process(target=server.run)
    process.start()
    print("INI AIOXMPPD SERVER")
    yield process
    print("FIN AIOXMPPD SERVER")
    process.terminate()


@pytest.fixture
def slixmpp_client():
    class TestClientBot(ClientXMPP):
        def __init__(self, jid, password):
            ClientXMPP.__init__(self, jid, password)
            self.add_event_handler("connection_failed", self.connection_error)
            self.add_event_handler("session_start", self.start)
            self.error = False

        async def connection_error(self, event):
            logger.error(f"ERROR: {event}")
            self.error = True
            self.disconnect()

        async def start(self, event):
            self.send_presence()
            await self.get_roster()
            self.send_message(mto="test@localhots", mbody="test message", mtype="chat")
            self.disconnect()

    return TestClientBot("test@localhost", "")


def test_client_connection(axiompdd_server, slixmpp_client):
    slixmpp_client.connect()
    slixmpp_client.process(forever=False)
    assert not slixmpp_client.error
