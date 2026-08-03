from fastcs import __version__
from fastcs.launch import launch

from .temperature_attr import TemperatureController

launch(TemperatureController, version=__version__)
