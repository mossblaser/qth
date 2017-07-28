Qth Conventions for MQTT
========================

The following outlines the conventions defined by Qth for MQTT.

Topics, Payloads and QoS
------------------------

Topics should be organised in a directory-tree-like fashion. As a
recommendation, physical location should be at the base of the hierarchy.
Leaves of the hierarchy should (in general) represent simple entities, for
example the state of a light or the temperature of a room.

All messages must have valid UTF-8 encoded `JSON <http://json.org>`_ payloads,
even if that means setting an unused payload to 'null'.

Unless you have good reason to do otherwise, QoS 2 should be used.

Events and Properties
---------------------

The style of use of a particular topic in Qth systems should be consistent and
well defined over time. Topic uses are divided into the following behaviour
classes:

* *Events* always have the 'retain' flag cleared and represent an ephemeral
  report or command.

  * **Many-to-One Events** have one subscriber and many publishers, for example
    a bell which can be rung.
  * **One-to-Many Events** have one publisher and many subscribers, for example
    a motion sensor.

* *Properties* always have the 'retain' flag set and represent a value which
  changes or may be changed over time.

  * **Many-to-One Properties** have one subscriber and many publishers, for
    example the state of a light.
  * **One-to-Many Properties** have one publisher and many subscribers, for
    example the humidity of a room.

Registration
------------

Since MQTT doesn't provide a way to list all of the topics in use at a given
time, Qth requires that the topics used by the system must be registered.

Qth clients are responsible for registering the topics of all events and
properties for which they are the 'one' side of. For example, a Many-to-One
Event should be registered by the subscriber while a One-to-Many event should
be registered by the publisher.

A registration server collates the information published by each client to
produce a hierarchy of One-to-Many Properties which enumerate the complete
hierarchy of MQTT topics.

Client Registration
```````````````````

Clients should pick, or be assigned, a globally unique ID.

Registration is performed by publishing a QoS 2, retained message to
``meta/clients/<CLIENT-ID>``. Whenever the set of topics to be registered by
the client changes, a new retained message should be published to this same
topic.

The registration message should take the following form:

.. code-block:: javascript

    {
        "description": "<HUMAN-READABLE DESCRIPTION OF CLIENT'S PURPOSE>",
        "topics": {
            "<TOPIC>": <TOPIC DESCRIPTION>,
            ...
        }
    }

The 'topics' object should be a mapping from topics registered by the client to
a description of that topic. A topic description is an object of the following
form:

.. code-block:: javascript

    {
        "behaviour": <TOPIC BEHAVIOUR>,
        "description": "<HUMAN-READABLE DESCRIPTION OF TOPIC>"
    }

Here, the 'behaviour' value must be one of:

* `"EVENT-1:N"` For One-to-Many Events.
* `"EVENT-N:1"` For Many-to-One Events.
* `"PROPERTY-1:N"` For One-to-Many Properties.
* `"PROPERTY-N:1"` For Many-to-One Properties.

Upon client disconnection, the client must publish a QoS 2, retained empty
message to ``meta/clients/<CLIENT-ID>`` to clear their registration.  This
message should be set as the client's MQTT will.


Registration server
```````````````````

The Qth registration service aggregates messages published into
``meta/clients/+`` to produce a hierarchy of One-to-Many properties in
``meta/ls/#`` whose topics end in trailing slashes.

The topic ``meta/ls/`` (note the trailing slash) describes the topics at the
root of the address space. Likewise ``meta/ls/foo/`` describes the topics in
``foo/``, e.g. ``foo/bar`` and ``foo/baz``, but not topics in subdirectories
such as ``foo/quz/quo``.

Each directory's property contains a JSON object of the following format:

.. code-block:: javascript

    {
        "<TOPIC>": [
            {"behavior": "...", "description": "...", "client_id": "...", },
            ...
        ],
        ...
    }

For every topic (and subdirectory) in a path a corresponding entry in this
object with ``<TOPIC>`` set to just the basename will be created. For example,
if we have a topic ``foo/bar`, the property ``meta/ls/foo/`` will have an entry
``bar`` (not ``foo/bar``).

Each item contains a list of JSON objects describing the uses of the topic.
Typically a topic will only have one use (e.g. it might be an event) but there
are occasions where it may have several. For example a topic may be both an
event and a directory simultaneously. Also, if two clients mistakenly register
the same topic, both of these registrations may be listed to aid debugging.

In addition to the 'behaviour' and 'description' fields provided in the
client's subscription, the ID of the client which initiated the registration is
also included.

Note that 'subdirectories' are also included and their 'behaviour' is defined
as `"DIRECTORY"`.

For example, say we had the following set of topics:

* ``lounge/light`` (Many-to-One Property)
* ``lounge/light/power_usage`` (One-to-Many Property)
* ``lounge/motion`` (One-to-Many Event)
* ``lounge/tmperature`` (One-to-Many Property)
* ``lounge/tv/power`` (Many-to-One Property)
* ``lounge/tv/channel`` (Many-to-One Property)
* ``bedroom/light`` (Many-to-One Property)
* ``bedroom/motion`` (One-to-Many Event)
* ...and so on...

The property ``meta/ls/lounge/`` would be as follows:

.. code-block:: javascript

    {
        "light": [ 
            {"behaviour": "PROPERTY-N:1", "description": "...", "client": "..."},
            {"behaviour": "DIRECTORY", "description": "...", "client": "..."}
        ],
        "motion": [ 
            {"behaviour": "EVENT-1:N", "description": "...", "client": "..."},
        ],
        "temperature": [ 
            {"behaviour": "PROPERTY-1:N", "description": "...", "client": "..."},
        ],
        "tv": [ 
            {"behaviour": "DIRECTORY", "description": "...", "client": "..."}
        ],
    }
