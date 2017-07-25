Qth: A home automation focused layer on MQTT
============================================

Qth is a set of conventions for using [MQTT (Message Queue Telemetry
Transport)](http://mqtt.org/) as the backbone of a home automation system.
These conventions were inspired by [SHET (SHET Home Event
Tunnelling)](https://github.com/18sg/shet), the asynchronous communication
framework used by the hacked-together home automation system in [my old student
house](http://18sg.github.io/).

This repository contains a specification of these conventions and a Python
'reference' implementation.

[**Documentation:**](http://qth.rtfd.org/) The Qth specification and reference
implementation can be found in [the documentation](http://qth.rtfd.org/).

API Sample Usage
----------------

The following example shows each of the main parts of the Qth API, though sadly
doesn't achieve anything especially exciting.

    #!/usr/bin/env python
    import qth
    import asyncio
    
    async def main():
        c = qth.Client("example-client",
                       "An example Qth client illustrating the basics.")
        
        # Examples showing the registering client's perspective of the four types
        # of topic.
        
        # Example One-to-Many Event: A motion sensor.
        await c.register("example/motion", qth.EVENT_ONE_TO_MANY,
                         "A motion sensor.")
        await c.send_event("example/motion")
        
        # Example Many-to-One Event: A bell.
        await c.register("example/bell", qth.EVENT_MANY_TO_ONE,
                         "A bell which can be rung.")
        def on_bell(topic, payload):
            print("Ding!")
        await c.watch_event("example/bell", on_bell)
        
        # Example One-to-Many Property: A temperature sensor.
        await c.register("example/temperature", qth.PROPERTY_ONE_TO_MANY,
                         "The temperature of a room (*C).")
        await c.set_property("example/temperature", 18.5)
        
        # Example Many-to-One Property: The state of a light bulb.
        await c.register("example/light", qth.PROPERTY_MANY_TO_ONE,
                         "State of a light in a room (boolean).")
        
        def on_set_light(topic, payload):
            print("Light is now", "on" if payload else "off")
        await c.watch_property("example/light", on_set_light)
        
        # Examples showing the other side of the picture!
        
        # Watching a One-to-Many Event
        def on_motion_detected(topic, payload):
            print("Stop, thief!")
        await c.watch_event("example/motion", on_motion_detected)
        
        # Sending a Many-to-One Event
        await c.send_event("example/bell")
        
        # Reading to a One-to-Many Property
        def on_temperature_changed(topic, payload):
            print("Temperature now:", payload)
        await c.watch_property("example/temperature", on_temperature_changed)
        
        # Reading to a One-to-Many Property: Alternative.
        temperature = await c.get_property("example/temperature")
        print(temperature.value)  # Always prints the latest temperature
        
        # Writing to a Many-to-One Property
        await c.set_property("example/light", True)
    
    loop = asyncio.get_event_loop()
    loop.create_task(main())
    loop.run_forever()



The Name
--------

Qth is an acronym for 'QB Than's House' where 'Qb' is pronounced 'Cube-ie' (my
wife's nickname) and 'Than' is pronounced as in 'jonaTHAN' (my name). Qth is
pronounced 'cue-th'.

Disclaimer
----------
Though anyone is free to use it under the understanding that Qth is presently
being developed solely for my own enjoyment.
