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
        return "MQTTError {}: {}".format(self.code, aiomqtt.error_string(self.code))


class FunctionError(Exception):
    pass

class FunctionTimeoutError(Exception):
    pass


class Client(object):
    
    def __init__(self, node_id, prefix="", loop=None,
                 host="localhost", port=1883, keepalive=10):
        self._node_id = node_id
        self._prefix = prefix
        self._loop = loop or asyncio.get_event_loop()

        # Lookup from MID to future to return upon arrival of that MID.
        self._publish_mid_to_future = {}
        self._subscribe_mid_to_future = {}
        self._unsubscribe_mid_to_future = {}
        
        # List of topic, qos pairs to subscribe to
        self._subscriptions = set()
        
        # Event which is set when connected and cleared when disconnected
        self._connected_event = asyncio.Event()

        self._mqtt = aiomqtt.Client(self._loop)
        
        self._mqtt.on_connect = self._on_connect
        self._mqtt.on_disconnect = self._on_disconnect
        self._mqtt.on_message = self._on_message
        self._mqtt.on_publish = self._on_publish
        self._mqtt.on_subscribe = self._on_subscribe
        self._mqtt.on_unsubscribe = self._on_unsubscribe
        
        self._mqtt.connect_async(host, port, keepalive)
    
    def _on_connect(self, _mqtt, _userdata, flags, rc):
        async def f():
            if self._subscriptions:
                await self._subscribe(list(self._subscriptions))
            self._connected_event.set()
        self._loop.create_task(f())
    
    def _on_disconnect(self, _mqtt, _userdata, rc):
        self._connected_event.clear()
    
    def _on_message(self, _mqtt, _userdata, message):
        pass
    
    def _on_publish(self, _mqtt, _userdata, mid):
        future = self._publish_mid_to_future.get(mid)
        if future is not None:
            future.set_result(None)
    
    def _on_subscribe(self, _mqtt, _userdata, mid, granted_qos):
        future = self._subscribe_mid_to_future.get(mid)
        if future is not None:
            future.set_result(granted_qos)
    
    def _on_unsubscribe(self, _mqtt, _userdata, mid):
        future = self._unsubscribe_mid_to_future.get(mid)
        if future is not None:
            future.set_result(None)
    
    async def ensure_connected(self):
        await self._connected_event.wait()
    
    async def _subscribe(self, topic, qos=2):
        """Subscribe to a (set of) topic(s) and wait until the subscription is
        confirmed. Must be called while connected.
        """
        # Subscribe to the topic(s)
        result, mid = self._mqtt.subscribe(topic, qos)
        if result != aiomqtt.MQTT_ERR_SUCCESS:
            raise MQTTError(result)
        
        # Wait for the subscription to be confirmed
        future = asyncio.Future(loop=self._loop)
        self._subscribe_mid_to_future[mid] = future
        await future
    
    async def subscribe(self, topic, callback, qos=2):
        """Coroutine which subscribes to a MQTT topic and registers a callback
        for message arrival on that topic.
        """
        # Setup a callback (this persists across reconnects)
        @functools.wraps(callback)
        def wrapper(_client, _userdata, message):
            callback(message)
        self._mqtt.message_callback_add(topic, wrapper)
        
        # Keep a list of the subscribed topics to resubscribe on reconnect
        self._subscriptions.add((topic, qos))
        
        # If currently connected, subscribe
        try:
            await self._subscribe(topic, qos)
        except MQTTError as e:
            # May have disconnected while subscribing, just give up and wait
            # for reconnect in this case.
            if e.code != aiomqtt.MQTT_ERR_NO_CONN:
                raise
    
    async def _unsubscribe(self, topic):
        """Unsubscrube from a topic. Must be called while connected."""
        # Unsubscribe from the topic(s)
        result, mid = self._mqtt.unsubscribe(topic)
        if result != aiomqtt.MQTT_ERR_SUCCESS:
            raise MQTTError(result)
        
        # Wait for the unsubscription to be confirmed
        future = asyncio.Future(loop=self._loop)
        self._unsubscribe_mid_to_future[mid] = future
        await future
    
    async def unsubscribe(self, topic):
        """Unsubscribe from a topic."""
        # Normalise topic to list of topics
        if isinstance(topic,  str):
            topic = [topic]
        
        for topic_name in topic:
            # Prevent resubscription occurring
            for qos in [1, 2, 3]:
                self._subscriptions.discard((topic_name, qos))
        
            # Remove all callbacks
            self._mqtt.message_callback_remove(topic_name)
        
        # Unregister with the broker
        try:
            result, mid = self._mqtt.unsubscribe(topic)
        except MQTTError as e:
            # If we're not connected, we didn't need to do anything anyway so
            # just give up!
            if e.code != aiomqtt.MQTT_ERR_NO_CONN:
                raise
    
    async def publish(self, topic, payload, qos=2, retain=False):
        """Publish a message, waiting until connected and the publication has
        been acknowledged.
        """
        mid = None
        while mid is None:
            await self.ensure_connected()
            result, mid = self._mqtt.publish(topic, payload, qos, retain)
            if result == aiomqtt.MQTT_ERR_SUCCESS:
                break
            elif result != aiomqtt.MQTT_ERR_NO_CONN:
                raise MQTTError(result)
        
        future = asyncio.Future(loop=self._loop)
        self._publish_mid_to_future[mid] = future
        await future
    
    async def create_function(self, topic, f):
        """Expose a function 'f' via MQTT, without registering it.
        
        When the topic is called, f will be called with two arguments: the
        topic name and the argument value (deserialised from JSON). The
        function should return a JSON serialisable value.
        
        Since MQTT doesn't expose a function-call like primitive, the following
        convention is used by QTH to implement this behaviour.  A function is
        assigned a topic such as 'my/function'.  To call this function:
        
        * The caller first subscribes to 'my/function/<random-id-here>/response'
        * Next the caller publishes 'my/function/<same-random-id-here>' with a payload containing
          a valid JSON object of the form {"args": [positional, args, go,
          here]), "kwargs": {"keyword": args, "go": here}}.
        * The implementer of the function should respond to receiving this
          message by publishing a response to
          'my/function/<same-random-id-here>/response' with the payload set to a
          JSON value of the form {"error": string-or-null, "value": something
          if error is not null}.
        * The caller should then unsubscribe from
          'my/function/<same-random-id-here>/response'.
        """
        async def async_callback(message):
            topic = message.topic
            value = None
            error = None
            try:
                # Run the user's function, passing in the arguments
                args = json.loads(message.payload)
                function_topic = "/".join(message.topic.split("/")[:-1])
                value = f(function_topic, *args["args"], **args["kwargs"])
                
                # If function is asynchronous, let it complete...
                if inspect.isawaitable(value):
                    value = await value
            except Exception as e:
                error = str(e)
                traceback.print_exc()
            
            # Send the response
            await self.publish("{}/response".format(topic), json.dumps({"error": error, "value": value}))

        def callback(message):
            self._loop.create_task(async_callback(message))
        
        await self.subscribe("{}/+".format(topic), callback)
    
    async def call(self, topic, *args, timeout=5, **kwargs):
        """Call a function exposed via MQTT.
        
        When the topic is called, f will be called with two arguments: the
        topic name and the argument value (deserialised from JSON). The
        function should return a JSON serialisable value.
        
        See create_function for a description of the calling convention used.
        """
        randid = "{}-{}".format(self._node_id, random.random())
        
        call_topic = "{}/{}".format(topic, randid)
        resp_topic = "{}/{}/response".format(topic, randid)
        
        response = asyncio.Future(loop=self._loop)
        
        # Subscribe to the response
        await self.subscribe(resp_topic, lambda msg: response.set_result(msg))
        try:
            # Send the call
            await self.publish(call_topic, json.dumps({"args": args, "kwargs": kwargs}))
            
            # Wait for the reply
            message = await asyncio.wait_for(response, timeout, loop=self._loop)
            response = json.loads(message.payload)
            if response.get("error") is not None:
                raise FunctionError("{}: {}".format(topic, response["error"]))
            else:
                return response["value"]
        except asyncio.TimeoutError:
            raise FunctionTimeoutError(topic)
        finally:
            # Unsubscribe from further replies
            await self.unsubscribe(resp_topic)
