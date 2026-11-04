import numpy as np

from fastcs.datatypes.types import DType


def numpy_to_python_type(np_type) -> type[DType]:
    """Converts numpy types to python types for widget creation.

    Only types important for widget creation are explicitly converted.
    """
    if np.issubdtype(np_type, np.integer):
        return int
    elif np.issubdtype(np_type, np.floating):
        return float
    elif np.issubdtype(np_type, np.bool_):
        return bool
    else:
        return str
