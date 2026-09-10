from abc import abstractmethod


class DRADeviceMixin:
    """Device node injected by a Kubernetes DRA claim."""

    @property
    @abstractmethod
    def _node_path(self) -> str:
        """The device node, for the log message. Usually from this connection's
        settings."""

    def is_terminal(self, exc: BaseException) -> bool:
        return isinstance(exc, FileNotFoundError)

    def unrecoverable_reason(self) -> str:
        return (
            f"Device node {self._node_path} has gone away. It comes from a Kubernetes "
            "DRA claim and will not reappear in this pod. Restart the pod to "
            "re-establish the claim."
        )
