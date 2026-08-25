# The queue is not a waiting room

We used to describe the ingestion queue as a waiting room. That metaphor made
the dashboard look harmless: a few jobs sitting patiently until a worker called
their names. It also hid the failure mode.

A queue is stored pressure. When producers outrun consumers, every new item
borrows time from the items behind it. At 09:42 last Tuesday, that debt reached
eleven minutes. Nothing crashed. Customers still waited.

We now page on queue age, not queue length. Length changes with batch size; age
tracks the promise users actually hear. The old chart remains beside the new
one, mostly as a reminder that a calm graph can tell the wrong story.
