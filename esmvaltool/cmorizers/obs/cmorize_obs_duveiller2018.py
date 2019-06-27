# pylint: disable=invalid-name
"""ESMValTool CMORizer for Duveiller2018 data.

Tier
   Tier 2: other freely-available dataset.

Source
   https://www.nature.com/articles/sdata201814

Last access
   20190430

Download and processing instructions
   Download the dataset albedo_IGBPgen.nc

Modification history
   20190627-A_crez_ba: added an extensive callback function to properly handle time bnds
   20190430-A_crez_ba: started with cmorize_obs_Landschuetzer2016.py as an example to follow

"""

import logging
import os
from warnings import catch_warnings, filterwarnings

import cf_units
import numpy as np
import iris
from dask import array as da
import calendar
import datetime


from .utilities import (set_global_atts, fix_coords, fix_var_metadata,
                        read_cmor_config, save_variable, constant_metadata, convert_timeunits, flip_dim_coord)

logger = logging.getLogger(__name__)



def duveiller2018_callback_function(cube,field,filename):
    # Rename 'Month' to 'time'
    cube.coord('Month').rename('time')

    # Create arrays for storing datetime objects
    custom_time = np.zeros((12),dtype=object)
    custom_time_bounds = np.empty((12,2),dtype=object)
    custom_time_units = 'days since 1950-01-01'
    
    # Now fill the object arrays defined above with datetime objects corresponding to correct time and time_bnds
    for i in range(custom_time_bounds.shape[0]):
        n_month = i+1 # we start with month number 1, at position 0
        weekday,ndays_in_month = calendar.monthrange(2010,n_month)  # Start with bounds
        time_bnd_a = datetime.datetime(2010,n_month,1)
        time_bnd_b = datetime.datetime(2010,n_month,ndays_in_month)
        time_midpoint = time_bnd_a + 0.5*(time_bnd_b - time_bnd_a)
        custom_time_bounds[n_month-1,0] = time_bnd_a
        custom_time_bounds[n_month-1,1] = time_bnd_b
        custom_time[n_month-1] = time_midpoint
    
    # Convert them
    time_bnds = cf_units.date2num(custom_time_bounds,custom_time_units,cf_units.CALENDAR_GREGORIAN)
    time_midpoints = cf_units.date2num(custom_time,custom_time_units,cf_units.CALENDAR_GREGORIAN)
    
    # Add them to the cube
    cube.coord('time').bounds = time_bnds
    cube.coord('time').points = time_midpoints

    # Set the correct time unit, as defined above
    cube.coord('time').units = cf_units.Unit(custom_time_units)

def extract_variable(var_info, raw_info, out_dir, attrs, cfg):
    """Extract to all vars."""
    var = var_info.short_name
    with catch_warnings():
        filterwarnings(
            action='ignore',
            message='Ignoring netCDF variable .* invalid units .*',
            category=UserWarning,
            module='iris',
        )
        cubes = iris.load(raw_info['file'], callback=duveiller2018_callback_function)
    rawvar = raw_info['name']
    print(cubes)
    for cube in cubes:
        if cube.var_name == rawvar:
            # Extracting a certain vegetation transition code
            # Read iTr parameter from the cfg
            iTr = cfg['parameters']['iTr']
            iTr_index = np.where(cube.coords('Vegetation transition code')[0].points == iTr)[0][0]
            cube = cube[iTr_index,:,:,:]
            # Add the vegetation transition code as an attribute to keep it on the file
            cube.attributes['Vegetation transition code'] = iTr
            # Remove it as a coordinate, since it is not allowed as a CMOR coordinate
            cube.remove_coord('Vegetation transition code')
            # Fix metadata
            fix_var_metadata(cube, var_info)
            # Fix coords
            fix_coords(cube)
            # Latitude has to be increasing (not fixed in fix_coords), so flip it
            flip_dim_coord(cube, 'latitude')
            # Global attributes
            set_global_atts(cube, attrs)
            save_variable(
                cube,
                var,
                out_dir,
                attrs,
                local_keys=['positive']
            )

def cmorization(in_dir, out_dir, cfg):
    """Cmorization func call."""
    cmor_table = cfg['cmor_table']
    glob_attrs = cfg['attributes']

    logger.info("Starting cmorization for Tier%s OBS files: %s",
                glob_attrs['tier'], glob_attrs['dataset_id'])
    logger.info("Input data from: %s", in_dir)
    logger.info("Output will be written to: %s", out_dir)

    # run the cmorization
    for var, vals in cfg['variables'].items():
        inpfile = os.path.join(in_dir, vals['file'])
        logger.info("CMORizing var %s from file %s", var, inpfile)
        var_info = cmor_table.get_variable(vals['mip'], var)
        print("var = ",var)
        raw_info = {'name': vals['raw'], 'file': inpfile}
        glob_attrs['mip'] = vals['mip']
        with catch_warnings():
            filterwarnings(
                action='ignore',
                message=('WARNING: missing_value not used since it\n'
                         'cannot be safely cast to variable data type'),
                category=UserWarning,
                module='iris',
            )
            extract_variable(var_info, raw_info, out_dir, glob_attrs, cfg)

