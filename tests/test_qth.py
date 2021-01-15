import pytest

from mock import Mock

import asyncio
import qth


@pytest.fixture(scope="module")
def port():
    # A port which is likely to be free for the duration of tests...
    return 11223


@pytest.fixture(scope="module")
def hostname():
    return "localhost"


@pytest.fixture(scope="module")
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
    c = qth.Client("test-client",
                   host=hostname, port=port,
                   loop=event_loop)
    try:
        yield c
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_uniqueified_id(client, event_loop):
    # Should have something appended to the supplied client ID
    assert client.client_id.startswith("test-client-")
    assert len(client.client_id) > len("test-client-")

    # The test_register test further below tests that uniqification of IDs can
    # be disabled.


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
    await client.publish("test/foo", {"hello": "world"})
    await asyncio.wait_for(on_message_evt.wait(), 5.0, loop=event_loop)
    on_message.assert_called_once_with("test/foo", {"hello": "world"})

    # Check that publishing an Empty message works
    on_message_evt.clear()
    on_message.reset_mock()
    assert not on_message_evt.is_set()
    await client.publish("test/foo", qth.Empty)
    await asyncio.wait_for(on_message_evt.wait(), 5.0, loop=event_loop)
    on_message.assert_called_once_with("test/foo", qth.Empty)

    # Unsubscribe and check we don't get a message
    on_message.reset_mock()
    on_message_evt.clear()
    await client.unsubscribe("test/foo", on_message)
    await client.publish("test/foo", {"hello": "there"})
    await asyncio.sleep(0.1, loop=event_loop)
    assert not on_message.called


@pytest.mark.asyncio
async def test_pub_sub_coroutine(client, event_loop):
    # Subscribe to a topic with a coroutine callback.
    on_message_evt = asyncio.Event(loop=event_loop)

    async def on_message(message, payload):
        assert message == "test/foo"
        assert payload == {"hello": "world"}
        await asyncio.sleep(0.1, loop=event_loop)
        on_message_evt.set()

    await client.subscribe("test/foo", on_message)

    assert not on_message_evt.is_set()
    await client.publish("test/foo", {"hello": "world"})
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
async def test_register(client, hostname, port, event_loop):
    # NB: as a side effect, this test also tests the make_client_id_unique
    # option can be overridden.

    # Make a client to check the registrations of
    dut = qth.Client("test-monitor", "A test client.",
                     make_client_id_unique=False,
                     host=hostname, port=port, loop=event_loop)
    try:
        # Register some endpoints
        await dut.ensure_connected()
        await dut.register("test/someepehm", qth.EVENT_ONE_TO_MANY,
                           "An example...")
        await dut.register("test/anotherephem", qth.EVENT_MANY_TO_ONE,
                           "A further example...", on_unregister=None)
        await dut.register("test/somesensor", qth.PROPERTY_ONE_TO_MANY,
                           "Another example...", on_unregister=123)
        await dut.register("test/something", qth.PROPERTY_MANY_TO_ONE,
                           "A final example...", delete_on_unregister=True)

        # Subscribe to registration updates
        sub_evt = asyncio.Event(loop=event_loop)
        sub = Mock(side_effect=lambda *_: sub_evt.set())
        await client.subscribe("meta/clients/test-monitor", sub)

        # See what we get!
        await asyncio.wait_for(sub_evt.wait(), 0.5, loop=event_loop)
        assert sub.mock_calls[-1][1][0] == "meta/clients/test-monitor"
        assert sub.mock_calls[-1][1][1] == {
            "description": "A test client.",
            "topics": {
                "test/someepehm": {
                    "behaviour": "EVENT-1:N",
                    "description": "An example...",
                },
                "test/anotherephem": {
                    "behaviour": "EVENT-N:1",
                    "description": "A further example...",
                    "on_unregister": None,
                },
                "test/somesensor": {
                    "behaviour": "PROPERTY-1:N",
                    "description": "Another example...",
                    "on_unregister": 123,
                },
                "test/something": {
                    "behaviour": "PROPERTY-N:1",
                    "description": "A final example...",
                    "delete_on_unregister": True,
                },
            },
        }

        # Unregister something and see if the update is sent
        sub_evt.clear()
        await dut.unregister("test/someepehm")
        await asyncio.wait_for(sub_evt.wait(), 0.5, loop=event_loop)
        assert sub.mock_calls[-1][1][0] == "meta/clients/test-monitor"
        assert sub.mock_calls[-1][1][1] == {
            "description": "A test client.",
            "topics": {
                "test/anotherephem": {
                    "behaviour": "EVENT-N:1",
                    "description": "A further example...",
                    "on_unregister": None,
                },
                "test/somesensor": {
                    "behaviour": "PROPERTY-1:N",
                    "description": "Another example...",
                    "on_unregister": 123,
                },
                "test/something": {
                    "behaviour": "PROPERTY-N:1",
                    "description": "A final example...",
                    "delete_on_unregister": True,
                },
            }
        }

        # Make sure everything goes away when the client disconnects
        sub_evt.clear()
        await dut.close()
        await asyncio.wait_for(sub_evt.wait(), 0.5, loop=event_loop)
        assert sub.mock_calls[-1][1][0] == "meta/clients/test-monitor"
        assert sub.mock_calls[-1][1][1] is qth.Empty

        # Make sure nothing is retained afterwards
        await client.unsubscribe("meta/clients/test-monitor", sub)
        sub_evt.clear()
        sub.reset_mock()
        await client.subscribe("meta/clients/test-monitor", sub)
        await asyncio.sleep(0.1, loop=event_loop)
        assert len(sub.mock_calls) == 0

    finally:
        await dut.close()


@pytest.mark.asyncio
async def test_register_merging(client, hostname, port, event_loop):
    # Make a client to check the registrations of
    dut = qth.Client("test-monitor", "A test client.",
                     make_client_id_unique=False,
                     host=hostname, port=port, loop=event_loop)
    try:
        # Subscribe to registration updates
        sub_evt = asyncio.Event(loop=event_loop)
        sub = Mock(side_effect=lambda *_: sub_evt.set())
        await client.subscribe("meta/clients/test-monitor", sub)

        # Register many endpoints
        await dut.ensure_connected()
        await asyncio.wait([dut.register("test/num-{}".format(i),
                                         qth.EVENT_ONE_TO_MANY, "A test")
                            for i in range(100)], loop=event_loop)

        # We should get many fewer registration messages than there were calls
        # to register.
        await asyncio.sleep(0.1, loop=event_loop)
        assert sub.mock_calls[-1][1][0] == "meta/clients/test-monitor"
        assert len(sub.mock_calls[-1][1][1]["topics"]) == 100
        assert len(sub.mock_calls) < 100

    finally:
        await dut.close()


@pytest.mark.asyncio
async def test_event(client, event_loop):
    on_event_evt = asyncio.Event(loop=event_loop)
    on_event = Mock(side_effect=lambda *_: on_event_evt.set())
    await client.watch_event("test/event", on_event)

    # Check default value is None
    await client.send_event("test/event")
    await asyncio.wait_for(on_event_evt.wait(), 0.5, loop=event_loop)
    assert on_event.mock_calls[-1][1][1] is None

    # Check JSON goes through
    on_event_evt.clear()
    await client.send_event("test/event", {"foo": "bar"})
    await asyncio.wait_for(on_event_evt.wait(), 0.5, loop=event_loop)
    assert on_event.mock_calls[-1][1][1] == {"foo": "bar"}

    # Check unsubscribe works
    await client.unwatch_event("test/event", on_event)
    on_event_evt.clear()
    await client.send_event("test/event")
    await asyncio.sleep(0.1, loop=event_loop)
    assert not on_event_evt.is_set()


@pytest.mark.asyncio
async def test_property(client, event_loop):
    on_property_evt = asyncio.Event(loop=event_loop)
    on_property = Mock(side_effect=lambda *_: on_property_evt.set())
    await client.watch_property("test/property", on_property)

    # Check set and subscribe
    await client.set_property("test/property", {"hello": "world"})
    await asyncio.wait_for(on_property_evt.wait(), 0.5, loop=event_loop)
    assert on_property.mock_calls[-1][1][1] == {"hello": "world"}

    # Check unsubscribe works
    await client.unwatch_property("test/property", on_property)
    on_property_evt.clear()
    await client.set_property("test/property", "foo")
    await asyncio.sleep(0.1, loop=event_loop)
    assert not on_property_evt.is_set()


@pytest.mark.asyncio
async def test_delete_property(client, event_loop):
    # Check property values are usually retained
    await client.set_property("test/deleted-property", {"hello": "world"})
    on_property_evt = asyncio.Event(loop=event_loop)
    on_property = Mock(side_effect=lambda *_: on_property_evt.set())
    await client.watch_property("test/deleted-property", on_property)
    await asyncio.wait_for(on_property_evt.wait(), 0.5, loop=event_loop)
    assert on_property.mock_calls[-1][1][1] == {"hello": "world"}

    # Check deleting the property removes it
    on_property_evt.clear()
    on_property.reset_mock()
    await client.delete_property("test/deleted-property")
    await asyncio.wait_for(on_property_evt.wait(), 0.5, loop=event_loop)
    assert on_property.mock_calls[-1][1][1] is qth.Empty

    # Check no value is retained
    on_property_evt.clear()
    on_property.reset_mock()
    await client.unwatch_property("test/deleted-property", on_property)
    await client.watch_property("test/deleted-property", on_property)
    await asyncio.sleep(0.1, loop=event_loop)
    assert len(on_property.mock_calls) == 0


@pytest.mark.asyncio
async def test_property_watcher(client, event_loop):
    # Ensure initial value gets through
    await client.set_property("test/property", 123)
    property = await client.get_property("test/property")
    assert property.value == 123

    # See if changes come through
    await client.set_property("test/property", 321)
    await asyncio.sleep(0.1, loop=event_loop)
    assert property.value == 321

    # See if the value can be changed
    on_property_evt = asyncio.Event(loop=event_loop)
    on_property = Mock(side_effect=lambda *_: on_property_evt.set())
    await client.watch_property("test/property", on_property)
    property.value = "bam!"
    await asyncio.sleep(0.1, loop=event_loop)
    assert on_property.mock_calls[-1][1][1] == "bam!"
    await client.unwatch_property("test/property", on_property)

    # Create another PropertyWatcher
    await client.set_property("test/another_property", "bam!")
    another_property = await client.get_property("test/another_property")
    assert another_property.value == "bam!"

    # Test both modes of closing
    await client.unwatch_property("test/property", property)
    await another_property.close()

    # Values now shouldn't change when property does
    await client.set_property("test/property", None)
    await asyncio.sleep(0.1, loop=event_loop)
    assert property.value == "bam!"
    assert another_property.value == "bam!"


@pytest.mark.asyncio
async def test_retain_all(client, event_loop):
    # Ensure the subscription 'retain-all' mode allows multiple subscriptions
    # to a single property to work.

    await client.set_property("test/property", "foo")

    # Subscribe to that property and make sure it arrives
    on_property_evt = asyncio.Event(loop=event_loop)
    on_property = Mock(side_effect=lambda *_: on_property_evt.set())
    await client.watch_property("test/property", on_property)
    await asyncio.wait_for(on_property_evt.wait(), 5.0, loop=event_loop)
    assert on_property.mock_calls[-1][1][1] == "foo"

    # Simultaneously subscribe to the same property and make sure it arrives
    property = await client.get_property("test/property")
    assert property.value == "foo"
