import asyncio
import functools
import json
import inspect
import traceback
import random

import aiomqtt


class MQTTError(Exception):

    def __init__(self, code):
        self.code = code

    def __str__(self):
        return "MQTTError {}: {}".format(self.code,
                                         aiomqtt.error_string(self.code))


class FunctionError(Exception):
    pass


class FunctionTimeoutError(Exception):
    pass


class Client(object):
    """A Qth-compliant MQTT client."""

    def __init__(self, client_id, loop=None,
                 host="localhost", port=1883, keepalive=10):
        """Connect to an MQTT server.

        Parameters
        ----------
        client_id : str
            A unique identifier for this client. (Required)
        loop : asyncio.AbstractEventLoop
            The asyncio event loop to run in. If omitted or None, uses the
            default loop.
        host : str
            The hostname of the MQTT server.
        port : int
            The port number of the MQTT server.
        keepalive : float
            Number of seconds between pings to the MQTT server.
        """
        self._client_id = client_id
        self._loop = loop or asyncio.get_event_loop()

        # Lookup from MID to future to return upon arrival of that MID.
        self._publish_mid_to_future = {}
        self._subscribe_mid_to_future = {}
        self._unsubscribe_mid_to_future = {}

        # Mapping from topics to lists of callbacks for that topic
        self._subscriptions = {}

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
                            "null", qos=2, retain=False)

        self._mqtt.on_connect = self._on_connect
        self._mqtt.on_disconnect = self._on_disconnect
        self._mqtt.on_publish = self._on_publish
        self._mqtt.on_subscribe = self._on_subscribe
        self._mqtt.on_unsubscribe = self._on_unsubscribe

        self._mqtt.loop_start()
        self._mqtt.connect_async(host, port, keepalive)

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
        # Run all callbacks associated with the nominal topic
        for callback in self._subscriptions.get(nominal_topic, []):
            try:
                retval = callback(message.topic, message.payload)
                if inspect.isawaitable(retval):
                    self._loop.create_task(retval)
            except:
                traceback.print_exc()

    async def close(self):
        """Permanently close the connection to the MQTT server."""
        try:
            # Indicate disconnection to registration server. If this fails
            # it'll be sorted out by the will.
            await self._publish("meta/clients/{}".format(self._client_id),
                                "null", retain=False)

            # Actually disconnect
            self._mqtt.disconnect()
            await self._disconnected_event.wait()
        except MQTTError as e:
            if e.code != aiomqtt.MQTT_ERR_NO_CONN:
                raise
        finally:
            # Stop the event loop thread
            await self._mqtt.loop_stop()

    async def ensure_connected(self):
        """Block until the client has connected to the MQTT server and all
        registration and subscription commands have completed.
        """
        await self._connected_event.wait()

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

    async def subscribe(self, topic, callback):
        """Coroutine which subscribes to a MQTT topic (with QoS 2) and
        registers a callback for message arrival on that topic. Returns once
        the subscription has been confirmed.

        If the client reconnects, the subscription will be automatically
        renewed.

        Many callbacks may be associated with the same topic.

        Parameters
        ----------
        topic : str
            The topic to subscribe to.
        callback : function or coroutine
            A callback function or coroutine to call when a message matching
            this subscription is received. The function will be called with a
            two arguments: the topic and the payload.
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

        # If required and currently connected, subscribe
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
        """Unsubscribe from a topic.

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
            # Remove the callback topic and MQTT client callback
            del self._subscriptions[topic]
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
        result, mid = self._mqtt.publish(topic, payload, 2, retain)
        if result != aiomqtt.MQTT_ERR_SUCCESS:
            raise MQTTError(result)

        # Wait for the message to be confirmed published
        future = asyncio.Future(loop=self._loop)
        self._publish_mid_to_future[mid] = future
        await future

    async def publish(self, topic, payload, retain=False):
        """Publish a message with QoS 2, waiting until connected and the
        publication has been acknowledged.

        Parameters
        ----------
        topic : str
            The topic to publish to
        payload : str or bytes
            The payload of the message
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

    async def create_function(self, topic, f):
        """Expose a request-response function 'f' using the Qth convention.
        Note that you must register this function yourself.

        Parameters
        ----------
        topic : str
            The base topic name for the function.
        f : function or coroutine
            The function or coroutine to expose. This function will be called
            with the arguments received. Its return value or exception will be
            returned.  This function should be re-entrant (if a coroutine) as
            calls are not queued.
        """
        async def callback(topic, payload):
            topic = topic
            value = None
            error = None
            try:
                # Run the user's function, passing in the arguments
                args = json.loads(payload)
                value = f(*args["args"], **args["kwargs"])

                # If function is asynchronous, let it complete...
                if inspect.isawaitable(value):
                    value = await value
            except Exception as e:
                error = str(e)
                traceback.print_exc()

            # Send the response
            await self.publish("{}/response".format(topic),
                               json.dumps({"error": error,
                                           "value": value}))

        await self.subscribe("{}/+".format(topic), callback)

    async def call(self, topic, *args, timeout=5, **kwargs):
        """Call a function using the Qth convention (e.g. using
        create_function).

        Parameters
        ----------
        topic : string
            The topic name of the function to call.
        *args, **kwargs
            The arguments for the function.
        timeout : float
            Number of seconds to wait for a response before timing out.
        """
        randid = "{}-{}".format(self._client_id, random.random())

        call_topic = "{}/{}".format(topic, randid)
        resp_topic = "{}/{}/response".format(topic, randid)

        response = asyncio.Future(loop=self._loop)

        # Subscribe to the response
        subscription_callback = (lambda t, p: response.set_result(p)
                                 if not response.done() else None)
        await self.subscribe(resp_topic, subscription_callback)
        try:
            # Send the call
            await self.publish(call_topic, json.dumps({"args": args,
                                                       "kwargs": kwargs}))

            # Wait for the reply
            payload = await asyncio.wait_for(response,
                                             timeout,
                                             loop=self._loop)
            response = json.loads(payload)
            if response.get("error") is not None:
                raise FunctionError("{}: {}".format(topic, response["error"]))
            else:
                return response["value"]
        except asyncio.TimeoutError:
            raise FunctionTimeoutError(topic)
        finally:
            # Unsubscribe from further replies
            await self.unsubscribe(resp_topic, subscription_callback)

    async def publish_registration(self):
        """Publish the Qth client registration message, if connected.

        This method is called automatically upon (re)connection and when the
        registration is changed. It is unlikely you'll need to call this by
        hand.
        """
        await self._publish("meta/clients/{}".format(self._client_id),
                            json.dumps(self._registration),
                            retain=True)

    async def register(self, path, behaviour, description):
        """Register a path with the Qth registration system.

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
        """
        self._registration[path] = {
            "behaviour": behaviour,
            "description": description
        }

        try:
            await self.publish_registration()
        except MQTTError:
            pass

    async def unregister(self, path):
        """Unregister a path with the Qth registration system.

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
