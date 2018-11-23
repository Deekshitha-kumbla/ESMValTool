"""Fixes for GFDL ESM2G"""
import iris
from ..fix import Fix


class co2(Fix):
    """Fixes for co2"""

    def fix_data(self, cube):
        """
        Fix data

        Fixes discrepancy between declared units and real units

        Parameters
        ----------
        cube: iris.cube.Cube

        Returns
        -------
        iris.cube.Cube

        """
        metadata = cube.metadata
        cube *= 1e6
        cube.metadata = metadata
        return cube


class o2(Fix):
    """Fixes for o2"""

    def fix_file(self, filepath, output_dir):
        """
        Apply fixes to the files prior to creating the cube.

        Should be used only to fix errors that prevent loading or can
        not be fixed in the cube (i.e. those related with missing_value
        and _FillValue or missing standard_name).
        Parameters
        ----------
        filepath: basestring
            file to fix.
        output_dir: basestring
            path to the folder to store the fix files, if required.
        Returns
        -------
        basestring
            Path to the corrected file. It can be different from the original
            filepath if a fix has been applied, but if not it should be the
            original filepath.
        """
        new_path = Fix.get_fixed_filepath(output_dir, filepath)
        cubes = iris.load(filepath)

        ###
        # There are several fields in this NetCDF,
        # so we need to find the right one.
        found_o2 = False
        for cube in cubes:
            if cube.name() == 'Dissolved Oxygen Concentration':
                found_o2 = True
                break
        if not found_o2:
            assert 0
        std = 'mole_concentration_of_dissolved_molecular_oxygen_in_sea_water'
        long_name = 'Dissolved Oxygen Concentration'

        cube.long_name = long_name
        cube.standard_name = std

        iris.save(cube, new_path)
        return new_path
