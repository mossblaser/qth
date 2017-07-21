Qth: A home automation focused layer on MQTT
============================================

Qth is a spiritual successor to [SHET (SHET Home Event
Tunnelling)](https://github.com/18sg/shet), the asynchronous communication
framework used by the hacked-together home automation system in [my old student
house](http://18sg.github.io/).

Qth is a set of conventions for using [MQTT (Message Queue Telemetry
Transport)](http://mqtt.org/) which forms the basis of communication between
parts of my ongoing home automation project. This repository contains a Python
implementation of these conventions and serves as a reference implementation of
sorts.

Though anybody is free to use Qth, it currently is being developed solely for
my own use and entertainment.


Why does this exist?
--------------------

MQTT is a light weight network protocol for exchanging messages between
software and devices on a network. In particular, devices may *publish*
messages which are received by any device which has subscribed to received
them. For example, motion sensors might publish messages which are received by
lighting controllers. Similarly, lighting controllers may publish messages
received by the lights themselves to change their state.

MQTT uses a file-system-like directory structure to organise messages. For
example, messages from sensors in the lounge might be named as
`lounge/movement` and `lounge/temperature`. This makes it fairly easy to
structure everything.

MQTT implementations are widely available for most programming languages and
for both computers and microcontrollers. It is also an open standard with many
very carefully thought out features to cope with the headaches of real-world
systems and networks.

Unfortunately, MQTT isn't perfect. In particular:

* **Semantics are not explicitly defined.** Qth defines a set of standard
  usage patterns and specifies JSON payloads to reduce ambiguity.

* **MQTT doesn't provide a way to list available endpoints in the system.** For
  example, there would be no way to know what sensors are available in a room
  except by listening for *all* possible messages within a directory and
  waiting and hoping to see a message published by every sensor. This makes
  building user interfaces on top of MQTT difficult. SHET featured a simple
  command line tool which allowed tab completion of paths which proved
  invaluable. Likewise, a small number of graphical browsers were also
  developed.

Qth provides a set of conventions for using MQTT which address these
limitations.


Qth Conventions for MQTT
------------------------

The following is the definition of the conventions Qth uses for MQTT.

### Paths

Paths in the system should be arranged in a file-system-like manner. This means
that in general there should be a concept of a hierarchy of 'directories'
containing things. Good taste and judgement should be used to design a suitable
hierarchy.

If you have a path such as `lounge/lights` which indicates the current
on/off/dimming state of a light, it may be appropriate to have 'advanced'
things in subdirectories of that path (e.g. `lounge/lights/timeout`).

### Message payloads

All payloads should be valid JSON values, for example `null`, `true`, `1.5`,
`"Quoted"` or `{"values": [123, 456]}`.


### Message semantics

The following conventions should be used for each of the following signalling
behaviours.

#### Ephemeral events

* Examples: Motion detected, push-button button pressed.
* Message type: Non-persistent.
* Payload: JSON value giving further detail if required, typically `null`.
* Frequency: Only when event occurs, timing is significant.
* Published by: The source of the events.
* Subscribed to by: Interested parties.
* QoS: Use sensible judgement.

#### Sensor values

* Examples: Temperature, power, door locked.
* Message type: Persistent.
* Payload: JSON value giving the sensor value, typically a bool or float. Units
  should be obvious or easily guessed from context. For proportional amounts,
  prefer ranges of 0.0 to 1.0 over percentages.
* Frequency: Only when value changes. If sensor produces noisy data (e.g.
  temperature) new values should only be reported if the sensor has changed by
  a significant amount, e.g. half a degree.
* Published by: The sensor.
* Subscribed to by: Interested parties.
* QoS: Use sensible judgement.

#### Actuator states

* Examples: Light state, volume level, playing state.
* Message type: Persistent.
* Payload: The state of the actuator as a JSON value.
* Frequency: Only when changed.
* Published by: Systems which wish to control the actuator. The actuator may
  also write the value to clamp/replace invalid values or when a hard-wired
  control on the actuator is activated (e.g. a physical override switch).
* Subscribed to by: Interested parties, the actuator itself.
* QoS: 1 or 2 (at-least-once or exactly-once delivery)

Note that this pattern only applies to actors with (near) instantaneous
response such as light switches and not to those with longer response times
(e.g. a thermostat). This is because a new value published by a system setting
the actuator state will be immediately received by other subscribers and
therefore must reflect the true state of the actuator. For slower acting
actuators, see 'properties'.

#### Properties

* Examples: Thermostat set points, timer timeout settings.
* Message type: Persistent.
* Payload: The property value as a JSON value.
* Frequency: Only when changed.
* Published by: Any system which wishes to set the property.
* Subscribed to by: Interested parties, including the system(s) whose behaviour
  depends on the property.
* QoS: 1 or 2 (at-least-once or exactly-once delivery)

NB: This is distinct from the 'actuator states' pattern in that properties do
not directly reflect the state of a system but instead represent parameters for
a system. This difference is just semantic.

### Registration

Devices and systems connected to a Qth-compliant system should register all
paths owned by the client with the qth registry. This enables generic user
interfaces to sensibly display and interact with things connected to the
system.

Devices and systems are not obliged to check for the registration of paths they
intend to use.

The registration API is exposed via MQTT within the `misc/` directory.

#### Client registration

Clients should be assigned or pick a unique ID.

The client should setup a will which publishes a `null` payload, QoS 2,
non-persistant message to `meta/clients/{node id}` when the client disconnects
ungracefully. Before a client disconnects gracefully it should also manually
publish such a message.

When a client connects, and whenever the set of MQTT paths owned by the device
or system changes, the client should publish a message to `meta/clients/{node
id}`. This message should have QoS 2, be persistant and have a payload of the
following form:

    {
        "description": "Brief description of the client's purpose.",
        "paths": {
            "path/goes/here": <PATH DESCRIPTION HERE>,
            "another/path/here": <PATH DESCRIPTION HERE>,
            ...
        }
    }

Each value in the paths dictionary should be of the following form:

    {
        "behaviour": <BEHAVIOUR NAME HERE>,
        "description": "Brief human-readble description, stating units etc.",
    }

The following behaviours are defined below and correspond to the conventions
described earlier:

* `"ephemeral"` (e.g. motion detectors)
* `"sensor"` (e.g. temperature)
* `"state"` (e.g. light)
* `"property"` (e.g. thermostat setpoint)


#### Discovery

The Qth registration service uses the messages published into `meta/clients/+`
to produce a hierarchy of persostant, QoS 2 messages in `meta/ls/#`. Each
message contains the latest path information for that directory.

For example, `meta/ls/some/path`` will contain a message with information about
the paths available in `some/path/+`. Likewise `meta/ls` will contain a message
describing paths available in the root.

The messages in each path will have the following form:

    {
        "subpath": [
            {
                "behaviour": <BEHAVIOUR NAME HERE>,
                "description": "Brief description here.",
                "client": "client id here"
            },
            ...
        ],
        ...
    }

Each subpath has a *list* of path descriptions associated with it. The path
description is is derrived from the description provided by the client on
connection with the addition of a "client" field giving the client's ID.

Path descriptions are stored in a list (in order of registration) to account
for the fact a path may be both an path in its own right and a directory at the
same time (e.g. the paths `lounge/light` and `lounge/light/timeout` may exist
simultaneously). When a path is also a directory the special behaviour
`"directory"` will be given in the path description.

Note that paths are named as subpaths so for example in `meta/ls/some` the path
`some/path` will be listed as `path`.


The Name
--------

Qth is an acronym for 'QB Than's House' where 'Than' is pronounced as in
'jonaTHAN' (my name) and Qb is pronounced 'Cube-ie', my wife's nickname. Qth is
pronounced 'cue-th'.
