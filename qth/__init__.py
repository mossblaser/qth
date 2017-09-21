"""
A reference implementation of the Qth home-automation oriented conventions for
MQTT.
"""

import os
import asyncio
import functools
import json
import inspect
import traceback
import sentinel
import random
import string

import aiomqtt

from .version import __version__  # noqa

_NOT_GIVEN = sentinel.create("_NOT_GIVEN")

class Client(object):
    """A Qth-compliant MQTT client."""

    def __init__(self, client_id, description=None, loop=None,
                 make_client_id_unique=True,
                 host=None, port=None, keepalive=10):
        """Connect to an MQTT server.

        Parameters
        ----------
        client_id : str
            A unique identifier for this client. (Required) Will be prepended
            with a randomly generated suffix to avoid collisions when several
            instances of the same client are created. Set make_client_id_unique
            to False to disable this behaviour.
        description : str
            A human-readable description of the client. Defaults to being
            empty.
        loop : asyncio.AbstractEventLoop
            The asyncio event loop to run in. If omitted or None, uses the
            default loop.
        make_client_id_unique : bool
            If set to False, don't add a unique, random suffix to the
            client_id.
        host : str
            The hostname of the MQTT server. (Defaults to the value of the
            QTH_HOST environment variable, or if that is not set, localhost).
        port : int
            The port number of the MQTT server. (Defaults to the value of the
            QTH_PORT environment variable, or if that is not set, 1883).
        keepalive : float
            Number of seconds between pings to the MQTT server.
        """
        if make_client_id_unique:
            # Add a random suffix to the client ID by default to avoid
            # collisions
            self._client_id = "{}-{}".format(
                client_id,
                "".join(random.choices(string.hexdigits, k=8)).upper())
        else:
            self._client_id = client_id

        self._description = description
        self._loop = loop or asyncio.get_event_loop()

        # Lookup from MID to future to return upon arrival of that MID.
        self._publish_mid_to_future = {}
        self._subscribe_mid_to_future = {}
        self._unsubscribe_mid_to_future = {}

        # Mapping from topics to lists of callbacks for that topic
        self._subscriptions = {}

        # When the 'retain all' subscription mode is used, this dict will have
        # a value for the topic name. If no messages have been received for
        # this topic the value will be None, otherwise it will be the last
        # MQTTMessage object received for the topic.
        self._subscription_retained_message = {}

        # Event which is set when connecting/connected and cleared when
        # disconnected
        self._connected_event = asyncio.Event(loop=self._loop)

        # Event which is set when disconnected and cleared when connected
        self._disconnected_event = asyncio.Event(loop=self._loop)
        self._disconnected_event.set()

        # The registration data (sent to the Qth registration system) for this
        # client.
        self._registration = {}

        self._mqtt = aiomqtt.Client(self._loop)

        # Clear the registration when we disconnect ungracefully.
        self._mqtt.will_set("meta/clients/{}".format(self._client_id),
                            None, qos=2, retain=True)

        self._mqtt.on_connect = self._on_connect
        self._mqtt.on_disconnect = self._on_disconnect
        self._mqtt.on_publish = self._on_publish
        self._mqtt.on_subscribe = self._on_subscribe
        self._mqtt.on_unsubscribe = self._on_unsubscribe

        self._mqtt.loop_start()
        self._mqtt.connect_async(
            host or os.environ.get("QTH_HOST", "localhost"),
            port or int(os.environ.get("QTH_PORT", "1883")),
            keepalive,
        )

    async def _call_func_or_coro(self, f, *args, **kwargs):
        """Internal use only. Call a function or coroutine and return the
        result.
        """
        retval = f(*args, **kwargs)
        if inspect.isawaitable(retval):
            return await retval
        else:
            return retval

    def _on_connect(self, _mqtt, _userdata, flags, rc):
        self._disconnected_event.clear()

        async def f():
            # Re-subscribe to all subscriptions
            if self._subscriptions:
                await self._subscribe([(topic, 2)
                                       for topic
                                       in self._subscriptions])

            # Publish any paths to the Qth registry
            await self.publish_registration()

            # Unblock anything waiting for connection to complete
            self._connected_event.set()
        self._loop.create_task(f())

    def _on_disconnect(self, _mqtt, _userdata, rc):
        self._connected_event.clear()
        self._disconnected_event.set()

    def _on_publish(self, _mqtt, _userdata, mid):
        future = self._publish_mid_to_future.get(mid)
        if future is not None:
            future.set_result(None)

    def _on_subscribe(self, _mqtt, _userdata, mid, granted_qos):
        future = self._subscribe_mid_to_future.get(mid)
        if future is not None:
            future.set_result(None)

    def _on_unsubscribe(self, _mqtt, _userdata, mid):
        future = self._unsubscribe_mid_to_future.get(mid)
        if future is not None:
            future.set_result(None)

    def _on_message(self, nominal_topic, _mqtt, _userdata, message):
        topic = message.topic
        if message.payload is None or message.payload == b"":
            payload = Empty
        else:
            payload = json.loads(message.payload)
        args = (topic, payload)

        # If required, retain the most recent message
        if nominal_topic in self._subscription_retained_message:
            self._subscription_retained_message[nominal_topic] = args

        # Run all callbacks associated with the nominal topic. Copy taken in
        # case a callback results in (un)subscription.
        for callback in list(self._subscriptions.get(nominal_topic, [])):
            try:
                self._loop.create_task(self._call_func_or_coro(
                    callback, *args))
            except:
                traceback.print_exc()

    async def register(self, path, behaviour, description,
                       on_unregister=_NOT_GIVEN, delete_on_unregister=False):
        """Coroutine. Register a path with the Qth registration system.

        Parameters
        ----------
        path : string
            The topic path for the endpoint being registered.
        behaviour : string
            The qth behaviour name which describes how this endpoint will be
            used.
        description : string
            A human-readable string describing the purpose or higher-level
            behaviour of the endpoint.
        on_unsubscribe : JSON-serialisable value
            If given the on disconnection (either clean or dirty), sets the
            value of the registered proprety/sends the registered event with
            the given value.
        delete_on_unregister : bool
            If true, deletes the registered property. If set to True for an
            event, behaviour is undefined. If set at the same time as
            on_subscribe, behaviour is undefined.
        """
        self._registration[path] = {
            "behaviour": behaviour,
            "description": description
        }
        
        if on_unregister is not _NOT_GIVEN:
            self._registration[path]["on_unregister"] = on_unregister
        elif delete_on_unregister:
            self._registration[path]["delete_on_unregister"] = True

        try:
            await self.publish_registration()
        except MQTTError:
            pass

    async def unregister(self, path):
        """Coroutine. Unregister a path with the Qth registration system.

        Parameters
        ----------
        path : string
            The path to unregister.
        """
        self._registration.pop(path, None)

        try:
            await self.publish_registration()
        except MQTTError:
            pass

    async def send_event(self, topic, value=None):
        """Coroutine. Sends a Qth event.

        Parameters
        ----------
        topic : str
            The topic of the event.
        value : JSON-serialiseable
            (Optional) JSON-serialiseable value to send with the event.
        """
        await self.publish(topic, value)

    async def watch_event(self, topic, callback):
        """Coroutine. Calls a callback whenever a Qth event occurs.

        Parameters
        ----------
        topic : str
            The topic of the event.
        callback : function or coroutine
            This function or coroutine is called with the event topic and
            deserialised value of the event when the event occurs.
        """
        await self.subscribe(topic, callback)

    async def unwatch_event(self, topic, callback):
        """Coroutine. Stop watching a particular Qth event."""
        await self.unsubscribe(topic, callback)

    async def set_property(self, topic, value):
        """Coroutine which sets the value of a Qth property.

        Parameters
        ----------
        topic : str
            The topic of the property.
        value : JSON-serialiseable
            JSON-serialiseable value to set the property to.
        """
        await self.publish(topic, value, retain=True)

    async def get_property(self, topic):
        """Coroutine which returns a `PropertyWatcher` object.

        Blocks until the property value is known.

        Parameters
        ----------
        topic : str
            The topic of the property. Must not be a wildcard!

        Returns
        -------
        :py:class:`PropertyWatcher`
            An interface to the current property value. The ``value`` field
            will be updated with the most recent value of the property. Setting
            the ``value`` will cause the property to be written

            When you're finished with it either call
            `PropertyWatcher.close` or ``Client.unwatch_property(topic, p)``
            where ``topic`` is the topic of the property and ``p`` is the
            `PropertyWatcher`.
        """
        p = PropertyWatcher(self, topic)
        await p.wait()
        return p

    async def watch_property(self, topic, callback):
        """Coroutine which calls a callback whenever a Qth property is set.

        Parameters
        ----------
        topic : str
            The topic of the property. Must not be a wildcard!
        callback : function or coroutine
            This function or coroutine is called with the topic and
            deserialised value of the property when the property value is set.
        """
        await self.subscribe(topic, callback, retain_all=True)

    async def unwatch_property(self, topic, callback):
        """Coroutine. Stop watching a particular Qth property."""
        await self.unsubscribe(topic, callback)

    async def delete_property(self, topic):
        """Coroutine which deletes a Qth property. Watchers will receive a
        final value of ``qth.Empty``.

        Parameters
        ----------
        topic : str
            The topic of the property to delete.
        """
        await self.set_property(topic, Empty)

    async def publish_registration(self):
        """Coroutine. For advanced users only. Publish the Qth client
        registration message, if connected.

        This method is called automatically upon (re)connection and when the
        registration is changed. It is unlikely you'll need to call this by
        hand.
        """
        await self._publish(
            "meta/clients/{}".format(self._client_id),
            {
                "description": self._description,
                "topics": self._registration,
            },
            retain=True)

    async def _subscribe(self, topic):
        """(Internal use only.) Subscribe to a (set of) topic(s) and wait until
        the subscription is confirmed. Must be called while connected. Does not
        update the list of subscribed topics.
        """
        # Subscribe to the topic(s)
        result, mid = self._mqtt.subscribe(topic, 2)
        if result != aiomqtt.MQTT_ERR_SUCCESS:
            raise MQTTError(result)

        # Wait for the subscription to be confirmed
        future = asyncio.Future(loop=self._loop)
        self._subscribe_mid_to_future[mid] = future
        await future

    async def subscribe(self, topic, callback, retain_all=False):
        """For advanced users only. Coroutine which subscribes to a MQTT topic
        (with QoS 2) and registers a callback for message arrival on that
        topic. Returns once the subscription has been confirmed.

        If the client reconnects, the subscription will be automatically
        renewed.

        Many callbacks may be associated with the same topic pattern. See the
        description of the ``retain_all`` flag.

        Parameters
        ----------
        topic : str
            The topic to subscribe to.
        callback : function or coroutine
            A callback function or coroutine to call when a message matching
            this subscription is received. The function will be called with a
            two arguments: the topic and the deserialised payload.
        retain_all : bool
            If True, treat all received messages on this topic as retained.
            Future subscriptions to the same topic will always immediately
            receive a copy of the most recent message matching the
            subscription.

            If False, only the first subscription to a given topic will receive
            the server-retained message, if any. Future subscriptions to the
            same topic will only receive subsequent messages.

            This option is a work-around for MQTT not exposing when a received
            message was sent with the retain flag.

            All subscribers to a particular topic must set this argument to the
            same value.
        """
        new_subscription = topic not in self._subscriptions

        # Setup a handler to handle messages to this topic
        if new_subscription:
            self._mqtt.message_callback_add(
                topic,
                functools.partial(self._on_message, topic))
            self._subscriptions[topic] = []

        # Register the user-supplied callback
        self._subscriptions[topic].append(callback)

        # If retain_all, create a relevant entry
        if retain_all and topic not in self._subscription_retained_message:
            self._subscription_retained_message[topic] = None

        # 'Receive' any locally retained messages for topics with retain_all
        # set.
        retained_message = self._subscription_retained_message.get(topic, None)
        if retained_message is not None:
            self._loop.create_task(self._call_func_or_coro(
                callback, *retained_message))

        # If not already subscribed and currently connected, subscribe
        if new_subscription:
            try:
                await self._subscribe(topic)
            except MQTTError as e:
                # May have disconnected while subscribing, just give up and
                # wait for reconnect in this case.
                if e.code != aiomqtt.MQTT_ERR_NO_CONN:
                    raise

    async def _unsubscribe(self, topic):
        """(Internal use only.) Unsubscrube from a topic. Must be called while
        connected. Does not update set of subscribed topics."""
        # Unsubscribe from the topic(s)
        result, mid = self._mqtt.unsubscribe(topic)
        if result != aiomqtt.MQTT_ERR_SUCCESS:
            raise MQTTError(result)

        # Wait for the unsubscription to be confirmed
        future = asyncio.Future(loop=self._loop)
        self._unsubscribe_mid_to_future[mid] = future
        await future

    async def unsubscribe(self, topic, callback):
        """Coroutine. For advanced users only. Unsubscribe from a topic.

        Parameters
        ----------
        topic : str
            The topic pattern used when subscribing.
        callback : function or coroutine
            The callback or coroutine used when subscribing.
        """
        callbacks = self._subscriptions[topic]
        callbacks.remove(callback)

        # Unsubscribe completely if no more callbacks are associated.
        if not callbacks:
            # Remove the callback, any retained message and the MQTT client
            # callback
            del self._subscriptions[topic]
            self._subscription_retained_message.pop(topic, None)
            self._mqtt.message_callback_remove(topic)

            # Unregister with the broker
            try:
                result, mid = self._mqtt.unsubscribe(topic)
            except MQTTError as e:
                # If we're not connected, we didn't need to do anything anyway
                # so just give up!
                if e.code != aiomqtt.MQTT_ERR_NO_CONN:
                    raise

    async def _publish(self, topic, payload, retain=False):
        """(Internal use only.) Publish a message, waiting until the
        publication has been acknowledged.
        """
        mid = None
        if payload is Empty:
            payload = None
        else:
            payload = json.dumps(payload)
        result, mid = self._mqtt.publish(topic, payload, 2, retain)
        if result != aiomqtt.MQTT_ERR_SUCCESS:
            raise MQTTError(result)

        # Wait for the message to be confirmed published
        future = asyncio.Future(loop=self._loop)
        self._publish_mid_to_future[mid] = future
        await future

    async def publish(self, topic, payload, retain=False):
        """Coroutine. For advanced users only. Publish a message with QoS 2,
        waiting until connected and the publication has been acknowledged.

        Parameters
        ----------
        topic : str
            The topic to publish to
        payload : JSON-serialiseable value or ``qth.Empty``
            The payload of the message. Will be sent seriallised as JSON or if
            ``qth.Empty`` is passed, will be a completely empty MQTT message.
        retain : bool
            Should the message be retained by the MQTT server?
        """
        mid = None
        while mid is None:
            await self.ensure_connected()
            try:
                return await self._publish(topic, payload, retain)
            except MQTTError as e:
                if e.code != aiomqtt.MQTT_ERR_NO_CONN:
                    raise

    async def ensure_connected(self):
        """For advanced users only (most commands call this internally).
        Coroutine. Block until the client has connected to the MQTT server and
        all registration and subscription commands have completed.
        """
        await self._connected_event.wait()

    async def close(self):
        """Coroutine. Permanently close the connection to the MQTT server."""
        try:
            # Indicate disconnection to registration server. If this fails
            # it'll be sorted out by the will.
            await self._publish("meta/clients/{}".format(self._client_id),
                                Empty, retain=True)

            # Actually disconnect
            self._mqtt.disconnect()
            await self._disconnected_event.wait()
        except MQTTError as e:
            if e.code != aiomqtt.MQTT_ERR_NO_CONN:
                raise
        finally:
            # Stop the event loop thread
            await self._mqtt.loop_stop()

    @property
    def client_id(self):
        """This client's Qth client ID."""
        return self._client_id


Empty = sentinel.create("Empty")
"""Payload value to send to generate a genuinely empty MQTT message."""

EVENT_ONE_TO_MANY = "EVENT-1:N"
"""Behaviour name for One-to-Many Events."""

EVENT_MANY_TO_ONE = "EVENT-N:1"
"""Behaviour name for Many-to-One Events."""

PROPERTY_ONE_TO_MANY = "PROPERTY-1:N"
"""Behaviour name for One-to-Many Properties."""

PROPERTY_MANY_TO_ONE = "PROPERTY-N:1"
"""Behaviour name for Many-to-One Properties."""

DIRECTORY = "DIRECTORY"
"""Behaviour used for directory entries in a Qth registry's directory listing.
Not to be used by regular clients."""


class MQTTError(Exception):
    """Thrown when an MQTT-related error occurs. Has a 'code' member variable
    indicating the Paho-MQTT error code.
    """

    def __init__(self, code):
        self.code = code

    def __str__(self):
        return "MQTTError {}: {}".format(self.code,
                                         aiomqtt.error_string(self.code))


class PropertyWatcher(object):
    """A utility class which allows convenient access to the most recent value
    of a Qth property. Create using :py:meth:`Client.get_property`.
    """

    def __init__(self, client, topic):
        """Using the supplied client, watch the supplied property."""
        self._client = client
        self._topic = topic

        # The most recently received value
        self._value = Empty

        loop = self._client._loop

        self._has_been_set = asyncio.Event(loop=loop)
        loop.create_task(self._client.watch_property(topic, self))

    def __call__(self, topic, new_value):
        """Internal use only. The callback when the property value is
        updated.
        """
        self._value = new_value
        self._has_been_set.set()

    @property
    def value(self):
        """The most recently received property value. If no value has yet been
        received, this value will be ``qth.Empty``.

        Calling `PropertyWatcher.wait` will wait until this property has a
        valid value.
        """
        return self._value

    @value.setter
    def value(self, new_value):
        """Try and set the value, at some point in the future.

        .. warning::
            If setting the property fails for any reason, there is no way to
            discover this when using this API. Consider using the
            `Client.set_property` instead.
        """
        self._client._loop.create_task(self._client.set_property(
            self._topic,
            new_value))

    async def wait(self):
        """Wait until the value of the property is known."""
        await self._has_been_set.wait()

    async def close(self):
        """Permanently stop watching the property, invalidating the value of
        this object.
        """
        await self._client.unwatch_property(self._topic, self)
