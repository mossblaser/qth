import pytest

from mock import Mock

import random
import json

import asyncio
import qth


@pytest.fixture("module")
def port():
    # A port which is likely to be free for the duration of tests...
    return 11223


@pytest.fixture("module")
def hostname():
    return "localhost"


@pytest.fixture("module")
def event_loop():
    return asyncio.get_event_loop()


@pytest.yield_fixture(scope="module")
def server(event_loop, port):
    mosquitto = event_loop.run_until_complete(asyncio.create_subprocess_exec(
        "mosquitto", "-p", str(port),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        loop=event_loop))

    try:
        yield
    finally:
        mosquitto.terminate()
        event_loop.run_until_complete(mosquitto.wait())


@pytest.fixture
async def client(server, hostname, port, event_loop):
    c = qth.Client("test-client-{}".format(random.random()),
                   host=hostname, port=port,
                   loop=event_loop)
    try:
        yield c
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_initial_connection(client, event_loop):
    await client.ensure_connected()


@pytest.mark.asyncio
async def test_sub_pub_unsub(client, event_loop):
    # Subscribe to test/foo
    on_message_evt = asyncio.Event(loop=event_loop)
    on_message = Mock(side_effect=lambda *_: on_message_evt.set())
    await client.subscribe("test/foo", on_message)

    # Publish to test/foo and check we get the message
    assert not on_message_evt.is_set()
    await client.publish("test/foo", "hello")
    await asyncio.wait_for(on_message_evt.wait(), 5.0, loop=event_loop)
    on_message.assert_called_once_with("test/foo", b"hello")

    # Unsubscribe and check we don't get a message
    on_message.reset_mock()
    on_message_evt.clear()
    await client.unsubscribe("test/foo", on_message)
    await client.publish("test/foo", "hello")
    await asyncio.sleep(0.1, loop=event_loop)
    assert not on_message.called


@pytest.mark.asyncio
async def test_pub_sub_coroutine(client, event_loop):
    # Subscribe to a topic with a coroutine callback.
    on_message_evt = asyncio.Event(loop=event_loop)

    async def on_message(message, payload):
        assert message == "test/foo"
        assert payload == b"hello"
        await asyncio.sleep(0.1, loop=event_loop)
        on_message_evt.set()

    await client.subscribe("test/foo", on_message)

    assert not on_message_evt.is_set()
    await client.publish("test/foo", "hello")
    await asyncio.wait_for(on_message_evt.wait(), 5.0, loop=event_loop)


@pytest.mark.asyncio
async def test_sub_pub_unsub_multiple(client, event_loop):
    # Subscribe to the same topic several times
    callback_a = Mock(side_effect=Exception())
    callback_b = Mock()
    callback_c = Mock()
    await client.subscribe("test/foo", callback_a)
    await client.subscribe("test/foo", callback_b)
    await client.subscribe("test/foo", callback_c)
    await client.subscribe("test/foo", callback_c)

    # Publish to test/foo and check we get the message
    await client.publish("test/foo", "hello")
    await asyncio.sleep(0.1, loop=event_loop)

    # Should have been called appropriate number of times
    assert callback_a.call_count == 1
    assert callback_b.call_count == 1
    assert callback_c.call_count == 2

    # Should be able to unsubscribe from a single instance of the repeated
    # callback
    await client.unsubscribe("test/foo", callback_c)
    await client.publish("test/foo", "hello")
    await asyncio.sleep(0.1, loop=event_loop)
    assert callback_a.call_count == 2
    assert callback_b.call_count == 2
    assert callback_c.call_count == 3

    # Should be able to unsubscribe completely
    await client.unsubscribe("test/foo", callback_a)
    await client.unsubscribe("test/foo", callback_b)
    await client.unsubscribe("test/foo", callback_c)
    assert client._subscriptions == {}


@pytest.mark.asyncio
async def test_functions(client, event_loop):
    def my_function(a, b, c):
        if a != b != c:
            return [a + b, c]
        else:
            raise Exception("Numbers are same!")

    await client.create_function("test/myfunc", my_function)

    # Function call works correctly and can accept and return non-trivial types
    assert await client.call("test/myfunc", 1, c={"three": 3}, b=2) == [
        3, {"three": 3}]

    # Exceptions are returned to the caller
    with pytest.raises(qth.FunctionError):
        await client.call("test/myfunc", 1, 1, 1)

    # Timeouts work for non-existant functions
    before = event_loop.time()
    with pytest.raises(qth.FunctionTimeoutError):
        await client.call("test/notexist", timeout=0.1)
    after = event_loop.time()
    assert after - before >= 0.1


@pytest.mark.asyncio
async def test_coroutines(client, event_loop):
    # Should be able to call coroutines too!
    async def my_coroutine(a, b):
        await asyncio.sleep(0.1, loop=event_loop)
        return a + b

    await client.create_function("test/mycoro", my_coroutine)
    assert await client.call("test/mycoro", 1, 2) == 3


@pytest.mark.asyncio
async def test_register(client, hostname, port, event_loop):
    # Make a client to check the registrations of
    dut = qth.Client("test-monitor", host=hostname, port=port, loop=event_loop)
    try:
        # Register some endpoints
        await dut.ensure_connected()
        await dut.register("test/someepehm", "ephemeral", "An example...")
        await dut.register("test/somesensor", "sensor", "Another example...")

        # Subscribe to registration updates
        sub_evt = asyncio.Event(loop=event_loop)
        sub = Mock(side_effect=lambda *_: sub_evt.set())
        await client.subscribe("meta/clients/test-monitor", sub)

        # See what we get!
        await asyncio.wait_for(sub_evt.wait(), 0.5, loop=event_loop)
        assert sub.mock_calls[-1][1][0] == "meta/clients/test-monitor"
        assert json.loads(sub.mock_calls[-1][1][1]) == {
            "test/someepehm": {
                "behaviour": "ephemeral",
                "description": "An example...",
            },
            "test/somesensor": {
                "behaviour": "sensor",
                "description": "Another example...",
            },
        }

        # Unregister something and see if the update is sent
        sub_evt.clear()
        await dut.unregister("test/someepehm")
        await asyncio.wait_for(sub_evt.wait(), 0.5, loop=event_loop)
        assert sub.mock_calls[-1][1][0] == "meta/clients/test-monitor"
        assert json.loads(sub.mock_calls[-1][1][1]) == {
            "test/somesensor": {
                "behaviour": "sensor",
                "description": "Another example...",
            },
        }

        # Make sure everything goes away when the client disconnects
        sub_evt.clear()
        await dut.close()
        await asyncio.wait_for(sub_evt.wait(), 0.5, loop=event_loop)
        assert sub.mock_calls[-1][1][0] == "meta/clients/test-monitor"
        assert sub.mock_calls[-1][1][1] == b"null"
    finally:
        await dut.close()
