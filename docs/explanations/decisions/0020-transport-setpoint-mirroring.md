# 20. Transports mirror the attribute setpoint rather than tracking their own

Date: 2026-08-03

## Status

Accepted

## Context

An `AttrRW` has two values a transport must present: the readback (what the device
reports) and the setpoint (what was last asked of it). Readbacks were already
published by callback - the attribute calls back on every change and each transport
posts it - but setpoints were not.

Instead, each transport maintained its own setpoint display and updated it directly
in its write path, on the assumption that the only thing that could change a
setpoint was a write arriving through that same transport. That assumption is wrong
as soon as there is more than one transport, or a device that reports its own
setpoint.

It also left a visible gap at startup. A setpoint display starts at the datatype's
default, which is usually not the device's actual value, so each transport grew a
one-shot "seeding" hack: subscribe to the *readback* callback, and the first time a
value arrives, copy it into the setpoint display and unsubscribe. Two transports had
near-identical copies of this, and it only worked for `AttrRW` (a pure `AttrW` has no
readback to seed from).

The two EPICS transports had also drifted apart on ordering. PVA posted the setpoint
as soon as the put arrived, before the setter ran; CA posted it only after the update
callback completed, so a slow setter left the CA setpoint stale for the duration of
the write.

## Decision

The attribute owns the setpoint, and transports mirror it.

- `AttrW` gains `add_setpoint_callback()`, the setpoint-side counterpart of
  `AttrR.add_readback_callback()` (renamed from `add_on_update_callback()` for the
  symmetry). Every transport registers one and posts whatever it is given.
- `AttrW.update_setpoint()` caches a setpoint and fires those callbacks. `set()`
  calls it before running the setter, so the requested value is visible immediately;
  a value returned by the setter goes through it again, so a clamped or rejected
  value replaces it.
- A getter or setter can also drive the setpoint by returning
  `Update(readback=..., setpoint=...)` - the mechanism for a device that reports its
  own setpoint. `setpoint=None` (the default) leaves the cached setpoint alone.
- Seeding is gone. An `AttrRW` starts with no known setpoint, and the first readback
  to arrive - from a poll, a scan, or anything else calling `update()` - establishes
  it. Subsequent readbacks do not, so a readback that disagrees with the setpoint
  does not silently rewrite what the user asked for.

Transports must not update their own setpoint display directly in their write path.

## Consequences

Every transport shows the same setpoint, whichever transport was written through, and
CA and PVA now agree on when it appears: at the start of the write, before the setter
runs. That is the behaviour PVA already had, and it is the one that gives GUIs
immediate feedback.

Attribution is lost at the transport layer - a client cannot tell from the setpoint
alone which transport originated a write. This is deliberate: consistency between
transports is worth more than attribution, and attribution is recoverable from the
logs, which record the originating transport for every `set()`.

The one-shot seeding blocks in the CA and PVA transports are deleted, along with the
`isinstance(attribute, AttrR)` checks that guarded them, since the mechanism now works
for a pure `AttrW` too.
