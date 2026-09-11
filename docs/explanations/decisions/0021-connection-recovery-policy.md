# 21. A connection holds its recovery policy rather than inheriting it

Date: 2026-09-11

## Status

Accepted

## Context

A connection whose reconnect fails is retried every `reconnect_period` until
`reconnect_attempts` consecutive failures, then gives up and stalls its dependents
until the process restarts. For some failures that is the wrong shape. A device node
injected by a Kubernetes DRA claim - a USB/IP serial port, say - is established when
the pod starts; once it has gone it will not reappear in that pod, so every retry
is noise and the connection then sits there, apparently healthy, until someone
notices. The only fix is a pod restart.

Commit `0f62b58` expressed this by inheritance. `Connection` gained two hooks,
`is_terminal(exc)` and `unrecoverable_reason()`, and `fastcs.connections.dra` added a
`DRADeviceMixin` that overrode them, requiring a driver to implement an abstract
`_node_path` property:

```python
class DRASerialConnection(DRADeviceMixin, SerialConnection):
    @property
    def _node_path(self) -> str:
        return self._settings.port
```

The runner never consulted either hook, so the mixin had no effect yet. Porting
`fastcs-ximc` to it surfaced four problems, all of them about inheritance rather than
the behaviour:

1. **MRO order was a silent trap.** `class X(DRADeviceMixin, SerialConnection)`
   worked; `class X(SerialConnection, DRADeviceMixin)` inherited
   `Connection.is_terminal`, returned `False`, and the mixin did nothing - no error,
   no warning, nothing in a diff to notice.
2. **A class per transport × policy.** Serial needed `DRASerialConnection`; a claimed
   IP or HTTP device needed another, and a second policy would multiply them again. A
   claimed node behaves identically behind any transport, so this was duplication
   with no content.
3. **The policy could not change without changing the class.** Whether a node comes
   from a DRA claim is a deployment fact, but inheritance fixed it at authoring time.
4. **The contract was a private abstract hook.** `_node_path` was what a driver had
   to implement, and it appeared in no public signature.

## Decision

Replace the mixin with a policy object the connection holds.

- `fastcs.connections.recovery` defines `Recovery`, the default: it calls no failure
  terminal. It has `is_terminal(exc)`, `reason(connection)` for the log line, and
  `is_fatal`, which says whether a terminal failure should bring the application
  down. `DRANode` is the first subclass: `FileNotFoundError` is terminal, and fatal.
  Policies are stateless, so one instance can be shared.
- `Connection.recovery` is a class attribute defaulting to `Recovery()`. A connection
  class sets it, or it is assigned to one instance. It is deliberately not a
  constructor argument: the launcher builds a connection's config schema from its
  `__init__` signature, so an argument would appear in every connection's schema,
  and choosing a policy in YAML would need a discriminated union.
- `Connection.is_terminal` and `Connection.unrecoverable_reason` are removed rather
  than kept as delegating wrappers, which would give two ways to say one thing with
  undefined precedence when a subclass both overrode a method and held a policy.
- `Connection.label` is a public property naming the device - the port, address or
  base URL for the framework connections, and the class name otherwise. It replaces
  `_node_path`.
- The `ControllerRunner` consults the policy on every failed reconnect. A terminal
  failure gives up immediately, without spending the rest of the retry budget; if
  the policy is fatal, the runner also reports it through `fatal_error`, which
  `FastCS.serve` already turns into a clean shutdown. The first `connect` at startup
  is not affected, since a failure there already aborts startup.
- `fastcs.connections.dra` is deleted.

`reconnect_period` and `reconnect_attempts` stay on the connection. Whether a failure
is terminal is a device fact that no deployment should be able to contradict; the
period and the budget are what a site tunes, and are configurable per connection in
`fastcs.yaml`. Folding them into the policy would mean either a site could overrule
the device fact or the numbers stopped being configurable.

## Consequences

One policy serves any transport, and a connection is given one by assignment, so the
four problems above go away: there is no base-class order to get wrong, no class per
combination, a policy can be changed on an instance, and the contract a policy relies
on (`label`) is public.

A DRA-claimed device whose node disappears now gives up on the first failed reconnect
and shuts the application down with a message saying why, so the orchestrator
restarts the pod and the claim is re-established.

The policy is not selectable from `fastcs.yaml`; the connection class carries the
choice. That can be revisited if a deployment needs to change policy without
changing `type:`. A later extension could let a policy own the retry *schedule*
(for example a backoff computed from the connection's `reconnect_period`) while the
connection keeps owning the numbers; that is a separate decision.
