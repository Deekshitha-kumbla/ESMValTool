"""Derivation of variable `gtfgco2`."""
import iris
from iris import Constraint

import numpy as np
import cf_units

from ._derived_variable_base import DerivedVariableBase


def calculate_total_flux(fgco2_cube, cube_area):
    """
    Calculate the area of unmasked cube cells.

    Requires a cube with two spacial dimensions. (no depth coordinate).

    Parameters
    ----------
    cube: iris.cube.Cube
        Data Cube

    Returns
    -------
    numpy array:
        An numpy array containing the time points.
    numpy.array:
        An numpy array containing the total ice extent or total ice area.

    """
    data = []
    times = fgco2_cube.coord('time')
    time_dim = fgco2_cube.coord_dims('time')[0]

    fgco2_cube.data = np.ma.array(fgco2_cube.data)
    for time_itr, time in enumerate(times.points):

        total_flux = fgco2_cube[time_itr].data * cube_area.data

        total_flux = np.ma.masked_where(fgco2_cube[time_itr].data.mask,
                                        total_flux)
        # print (time_itr, time, total_flux.sum())
        data.append(total_flux.sum())

    ######
    # Create a small dummy output array
    data = np.array(data)
    return data


class DerivedVariable(DerivedVariableBase):
    """Derivation of variable `gtfgco2`."""

    # Required variables
    _required_variables = {
        'vars': [{
            'short_name': 'fgco2',
            'field': 'TO2M'
         }],
        'fx_files': ['areacello', ]
    }

    def calculate(self, cubes, use_fx_files=False, fx_files={None}):
        """Compute longwave cloud radiative effect."""
        fgco2_cube = cubes.extract_strict(
            Constraint(name='surface_downward_mass_flux_of_carbon_dioxide'
                            '_expressed_as_carbon'))

        try:
            cube_area = cubes.extract_strict(
                Constraint(name='cell_area'))
        except iris.exceptions.ConstraintMismatchError:
            pass

        total_flux = calculate_total_flux(fgco2_cube, cube_area)

        result = fgco2_cube.collapsed(['latitude', 'longitude'],
                                      iris.analysis.MEAN,)
        result.units = fgco2_cube.units * cube_area.units

        result.data = total_flux
        return result
