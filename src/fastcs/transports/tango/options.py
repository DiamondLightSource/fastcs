from dataclasses import dataclass


@dataclass
class TangoDSROptions:
    dsr_instance: str = "MY_SERVER_INSTANCE"
    debug: bool = False
