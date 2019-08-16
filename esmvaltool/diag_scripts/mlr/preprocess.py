#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Simple preprocessing of MLR model input.

Description
-----------
This diagnostic performs simple preprocessing operations for datasets used as
MLR model input in a desired way.

Author
------
Manuel Schlund (DLR, Germany)

Project
-------
CRESCENDO

Configuration options in recipe
-------------------------------
aggregate_by : dict, optional
    Aggregate over given coordinates (dict keys) using a desired aggregator
    (dict values). Allowed aggregators are `mean`, `median`, `sum`, `std`,
    `var`, and `trend`.
anomaly : dict, optional
    Calculate anomalies using reference datasets indicated by `ref: true`. Two
    datasets are matched using the list of metadata attributes given by the
    `matched_by` key. Additionally, the anomaly can be calculated relative to
    the (total) mean of the reference dataset if `mean: true` is specified.
area_weighted : bool, optional (default: True)
    Calculate weighted averages/sums for area (using grid cell boundaries).
argsort : dict, optional
    Calculate :mod:`numpy.ma.argsort` along given coordinate to get ranking.
    The coordinate can be specified by the `coord` key. If `descending: true`
    is given, use descending order insted of ascending.
convert_units_to : str, optional
    Convert units of the input data. Can also be given as dataset option.
mean : list of str, optional
    Calculate the mean over the specified coordinates.
normalize_mean : bool, optional (default: false)
    Remove total mean of the dataset in the last step (resulting mean will be
    0.0).
normalize_std : bool, optional (default: false)
    Scale total standard deviation of the dataset in the last (resulting
    standard deviation will be 1.0).
pattern : str, optional
    Pattern matched against ancestor files.
return_trend_stderr : bool, optional
    Return standard error of slope in case of trend calculations (as `var_type`
    `prediction_input_error`).
save_ref_data : bool, optional (default: False)
    Save data marked as `ref: true`.
sum : list of str, optional
    Calculate the sum of over the specified coordinates.
tag : str, optional
    Tag for the variable used in the MLR model.
time_weighted : bool, optional (default: True)
    Calculate weighted averages/sums for time (using grid cell boundaries).
trend : str, optional
    Calculate trend of data along the specified coordinate.

"""

import logging
import os
from copy import deepcopy
from functools import partial
from pprint import pformat

import iris
import numpy as np
from cf_units import Unit
from scipy import stats

from esmvaltool.diag_scripts.mlr import get_absolute_time_units, write_cube
from esmvaltool.diag_scripts.shared import (get_diagnostic_filename, io,
                                            run_diagnostic, select_metadata)

logger = logging.getLogger(os.path.basename(__file__))

AGGREGATORS = {
    'mean': iris.analysis.MEAN,
    'median': iris.analysis.MEDIAN,
    'std': iris.analysis.STD_DEV,
    'sum': iris.analysis.SUM,
    'var:': iris.analysis.VARIANCE,
    'trend': 'trend',
}


def _apply_aggregator(cfg, cube, data, coord_name, operation):
    """Apply aggregator to cube."""
    if operation == 'trend':
        (cube, data) = _apply_trend_aggregator(cfg, cube, data, coord_name)
    else:
        cube = cube.aggregated_by(coord_name, operation)
    return (cube, data)


def _apply_trend_aggregator(cfg, cube, data, coord_name):
    """Apply aggregator `trend` to cube."""
    coord_values = np.unique(cube.coord(coord_name).points)
    cubes = iris.cube.CubeList()
    cubes_stderr = iris.cube.CubeList()
    for val in coord_values:
        cube_slice = cube.extract(iris.Constraint(**{coord_name: val}))
        coord_dims = cube.coord_dims(coord_name)
        if len(coord_dims) != 1:
            raise ValueError(
                f"Trend aggregation along coordinate {coord_name} "
                f"requires 1D coordinate, got {len(coord_dims)}D "
                f"coordinate")
        dim_coord = cube.coord(dim_coords=True, dimensions=coord_dims[0])
        logger.debug("Calculating trend along coordinate '%s' for '%s' = %s",
                     dim_coord.name(), coord_name, val)
        return_stderr = (data.get('var_type') == 'prediction_input'
                         and cfg['return_trend_stderr'])
        (cube_slice, cube_slice_stderr,
         units) = _calculate_slope_along_coord(cube_slice,
                                               dim_coord.name(),
                                               return_stderr=return_stderr)
        cubes.append(cube_slice)
        if cube_slice_stderr is not None:
            cubes_stderr.append(cube_slice_stderr)
    cube = cubes.merge_cube()
    if cubes_stderr:
        cube_stderr = cubes_stderr.merge_cube()
    else:
        cube_stderr = None
    (cube, data) = _set_trend_metadata(cfg, cube, cube_stderr, data, units)
    data['trend'] = f'aggregated along coordinate {coord_name}'
    return (cube, data)


def _calculate_slope_along_coord(cube, coord_name, return_stderr=False):
    """Calculate slope of a cube along a given coordinate."""
    coord = cube.coord(coord_name)
    coord_dims = cube.coord_dims(coord_name)
    if len(coord_dims) != 1:
        raise ValueError(
            f"Trend calculation along coordinate {coord_name} requires "
            f"1D coordinate, got {len(coord_dims)}D coordinate")

    # Get slope and error if desired
    x_data = coord.points
    y_data = np.moveaxis(cube.data, coord_dims[0], -1)
    slope = _get_slope(x_data, y_data)
    if return_stderr:
        slope_stderr = _get_slope_stderr(x_data, y_data)
    else:
        slope_stderr = None

    # Get units
    if coord_name == 'time':
        units = get_absolute_time_units(coord.units)
    else:
        units = coord.units

    # Apply dummy aggregator for correct cell method and set data
    aggregator = iris.analysis.Aggregator('trend', _remove_axis)
    cube = cube.collapsed(coord_name, aggregator)
    cube.data = np.ma.masked_invalid(slope)
    if slope_stderr is not None:
        cube_stderr = cube.copy()
        cube_stderr.data = np.ma.masked_invalid(slope_stderr)
        logger.debug("Calculated standard error of trend")
    else:
        cube_stderr = None
    return (cube, cube_stderr, units)


def _get_anomaly_base(cfg, cube):
    """Get base value(s) for anomaly calculation."""
    if cfg['anomaly'].get('mean') and cube.shape != ():
        area_weights = _get_area_weights(cfg, cube)
        base = np.ma.average(cube.data, weights=area_weights)
    else:
        base = cube.data
    return base


def _get_area_weights(cfg, cube):
    """Calculate area weights."""
    area_weights = None
    if cfg.get('area_weighted', True):
        for coord in cube.coords(dim_coords=True):
            if not coord.has_bounds():
                logger.debug("Guessing bounds of coordinate '%s' of cube",
                             coord.name())
                logger.debug(cube)
                coord.guess_bounds()
        if _has_valid_coords(cube, ['latitude', 'longitude']):
            logger.debug("Calculating area weights")
            area_weights = iris.analysis.cartography.area_weights(cube)
    return area_weights


@partial(np.vectorize, excluded=['x_arr'], signature='(n),(n)->()')
def _get_slope(x_arr, y_arr):
    """Get slope of linear regression of two (masked) arrays."""
    if np.ma.is_masked(y_arr):
        x_arr = x_arr[~y_arr.mask]
        y_arr = y_arr[~y_arr.mask]
    if len(y_arr) < 2:
        return np.nan
    reg = stats.linregress(x_arr, y_arr)
    return reg.slope


@partial(np.vectorize, excluded=['x_arr'], signature='(n),(n)->()')
def _get_slope_stderr(x_arr, y_arr):
    """Get standard error of linear slope of two (masked) arrays."""
    if np.ma.is_masked(y_arr):
        x_arr = x_arr[~y_arr.mask]
        y_arr = y_arr[~y_arr.mask]
    if len(y_arr) < 2:
        return np.nan
    reg = stats.linregress(x_arr, y_arr)
    return reg.stderr


def _get_time_weights(cfg, cube):
    """Calculate time weights."""
    time_weights = None
    if cfg.get('time_weighted', True):
        for coord in cube.coords(dim_coords=True):
            if not coord.has_bounds():
                logger.debug("Guessing bounds of coordinate '%s' of cube",
                             coord.name())
                logger.debug(cube)
                coord.guess_bounds()
        if _has_valid_coords(cube, ['time']):
            logger.debug("Calculating time weights")
            time = cube.coord('time')
            time_weights = time.bounds[:, 1] - time.bounds[:, 0]
            new_axis_pos = np.delete(np.arange(cube.ndim),
                                     cube.coord_dims('time'))
            for idx in new_axis_pos:
                time_weights = np.expand_dims(time_weights, idx)
            time_weights = np.broadcast_to(time_weights, cube.shape)
    return time_weights


def _has_valid_coords(cube, coord_names):
    """Check if a cube has valid coordinates (length > 1)."""
    for coord_name in coord_names:
        try:
            coord = cube.coord(coord_name)
        except iris.exceptions.CoordinateNotFoundError:
            return False
        if coord.shape[0] <= 1:
            return False
    return True


def _remove_axis(data, axis=None):
    """Remove given axis of arrays by using index `0`."""
    return np.take(data, 0, axis=axis)


def _set_trend_metadata(cfg, cube, cube_stderr, data, units):
    """Set correct metadata for trend calculation."""
    cube.units /= units
    data['standard_name'] += '_trend'
    data['short_name'] += '_trend'
    data['long_name'] += ' (trend)'
    data['units'] += f' ({units})-1'
    if cube_stderr is not None:
        cube_stderr.units /= units
        stderr = deepcopy(data)
        stderr['standard_name'] += '_standard_error'
        stderr['short_name'] += '_stderr'
        stderr['long_name'] += ' (Standard Error)'
        stderr['var_type'] = 'prediction_input_error'
        (cube_stderr, stderr) = convert_units(cfg, cube_stderr, stderr)
        stderr = cache_cube(cfg, cube_stderr, stderr)
        data['stderr'] = stderr
    return (cube, data)


def add_standard_errors(input_data):
    """Add calculated standard errors to list of data."""
    new_input_data = []
    for data in input_data:
        new_input_data.append(data)
        if 'stderr' in data:
            stderr_data = data.pop('stderr')
            stderr_data['stderr'] = True
            new_input_data.append(stderr_data)
            logger.info("Added standard error for %s", data['filename'])
    return new_input_data


def aggregate(cfg, cube, data):
    """Aggregate cube over specified coordinate."""
    for (coord_name, aggregator) in cfg.get('aggregate_by', {}).items():
        iris_op = AGGREGATORS.get(aggregator)
        if iris_op is None:
            logger.warning("Unknown aggregation option '%s', skipping",
                           aggregator)
            continue
        logger.debug("Aggregating coordinate %s by calculating %s", coord_name,
                     aggregator)
        try:
            (cube, data) = _apply_aggregator(cfg, cube, data, coord_name,
                                             iris_op)
        except iris.exceptions.CoordinateNotFoundError:
            if hasattr(iris.coord_categorisation, f'add_{coord_name}'):
                getattr(iris.coord_categorisation, f'add_{coord_name}')(cube,
                                                                        'time')
                logger.debug("Added coordinate '%s' to cube", coord_name)
                (cube, data) = _apply_aggregator(cfg, cube, data, coord_name,
                                                 iris_op)
            else:
                logger.warning(
                    "'%s' is not a coordinate of cube %s and cannot be added "
                    "via iris.coord_categorisation", coord_name, cube)
    return (cube, data)


def cache_cube(cfg, cube, data):
    """Cache cube."""
    path = data['filename']
    basename = os.path.splitext(os.path.basename(path))[0]
    if cube.var_name is not None:
        basename = basename.replace(cube.var_name, data['short_name'])
    new_path = get_diagnostic_filename(basename, cfg)
    data['filename'] = new_path
    data['cube'] = cube
    if 'tag' in cfg and 'tag' not in data:
        data['tag'] = cfg['tag']
    if 'ref' not in data:
        data['ref'] = False
    return data


def calculate_anomalies(cfg, input_data):
    """Calculate anomalies using reference datasets."""
    if not cfg.get('anomaly'):
        return input_data
    metadata = cfg['anomaly'].get('matched_by', [])
    logger.info("Calculating anomalies using attributes %s to match datasets",
                metadata)
    ref_data = select_metadata(input_data, ref=True)
    regular_data = select_metadata(input_data, ref=False)
    for data in regular_data:
        if 'stderr' in data:
            logger.debug("Skipping standard error %s for anomaly calculation",
                         data)
            continue
        kwargs = {m: data[m] for m in metadata if m in data}
        ref = select_metadata(ref_data, **kwargs)
        if not ref:
            logger.warning(
                "No reference for dataset %s found, skipping "
                "anomaly calculation", data)
            continue
        if len(ref) > 1:
            logger.warning(
                "Reference data for dataset %s is not unique, found %s. "
                "Consider extending list of metadata for 'anomaly' option "
                "specified by the 'matched_by' key", data, ref)
            continue
        ref = ref[0]
        base = _get_anomaly_base(cfg, ref['cube'])
        data['cube'].data -= base
        data['standard_name'] += '_anomaly'
        data['short_name'] += '_anomaly'
        data['long_name'] += ' (anomaly)'
        data['anomaly'] = (
            f"Relative to {ref['short_name']} of {ref['dataset']} (project "
            f"{ref['project']}) of the {ref['exp']} run (years "
            f"{ref['start_year']} -- {ref['end_year']})")
    return input_data


def calculate_argsort(cfg, cube, data):
    """Calculate :mod:`numpy.ma.argsort` along given axis (= Ranking)."""
    argsort = cfg.get('argsort')
    if not argsort:
        return (cube, data)
    coord = argsort.get('coord')
    if not coord:
        raise ValueError("When 'argsort' is given, a valid 'coord' needs to "
                         "specified as key")
    logger.info("Calculating argsort along coordinate '%s' to get ranking",
                coord)
    axis = cube.coord_dims(coord)[0]
    mask = np.ma.getmaskarray(cube.data)
    if argsort.get('descending'):
        ranking = np.ma.argsort(-cube.data, axis=axis, fill_value=-np.inf)
        cube.attributes['order'] = 'descending'
    else:
        ranking = np.ma.argsort(cube.data, axis=axis, fill_value=np.inf)
        cube.attributes['order'] = 'ascending'
    cube.data = np.ma.array(ranking, mask=mask, dtype=cube.dtype)
    cube.units = Unit('no unit')
    data['standard_name'] += '_ranking'
    data['short_name'] += '_ranking'
    data['long_name'] += ' (ranking)'
    data['units'] = 'no unit'
    return (cube, data)


def calculate_sum_and_mean(cfg, cube, data):
    """Calculate sum and mean."""
    cfg = deepcopy(cfg)
    ops = [('mean', iris.analysis.MEAN), ('sum', iris.analysis.SUM)]
    for (oper, iris_op) in ops:
        if cfg.get(oper):
            logger.debug("Calculating %s over %s", oper, cfg[oper])
            if cfg[oper] == 'all':
                cfg[oper] = [
                    coord.name() for coord in cube.coords(dim_coords=True)
                ]

            # Latitude and longitude (weighted)
            area_weights = _get_area_weights(cfg, cube)
            if 'latitude' in cfg[oper] and 'longitude' in cfg[oper]:
                cube = cube.collapsed(['latitude', 'longitude'],
                                      iris_op,
                                      weights=area_weights)
                cfg[oper].remove('latitude')
                cfg[oper].remove('longitude')
                if oper == 'sum' and area_weights is not None:
                    cube.units *= Unit('m2')
                    data['units'] = str(cube.units)

            # Time (weighted)
            time_weights = _get_time_weights(cfg, cube)
            if 'time' in cfg[oper]:
                time_units = get_absolute_time_units(cube.coord('time').units)
                cube = cube.collapsed(['time'], iris_op, weights=time_weights)
                cfg[oper].remove('time')
                if oper == 'sum' and time_weights is not None:
                    cube.units *= time_units
                    data['units'] = str(cube.units)

            # Remaining operations
            if cfg[oper]:
                cube = cube.collapsed(cfg[oper], iris_op)
    return (cube, data)


def calculate_trend(cfg, cube, data):
    """Calculate trend."""
    if cfg.get('trend'):
        coord_name = cfg['trend']
        logger.debug("Calculating trend along coordinate '%s'", coord_name)
        if coord_name not in [c.name() for c in cube.coords()]:
            logger.warning(
                "Cannot calculate trend along '%s', cube does not contain "
                "coordinate with that name", coord_name)
            return (cube, data)
        return_stderr = (data.get('var_type') == 'prediction_input'
                         and cfg['return_trend_stderr'])
        (cube, cube_stderr,
         units) = _calculate_slope_along_coord(cube,
                                               coord_name,
                                               return_stderr=return_stderr)
        (cube, data) = _set_trend_metadata(cfg, cube, cube_stderr, data, units)
        data['trend'] = f'along coordinate {coord_name}'
    return (cube, data)


def convert_units(cfg, cube, data):
    """Convert units if desired."""
    cfg_settings = cfg.get('convert_units_to')
    data_settings = data.get('convert_units_to')
    if cfg_settings or data_settings:
        units_to = cfg_settings
        if data_settings:
            units_to = data_settings
        logger.debug("Converting units from '%s' to '%s'", cube.units,
                     units_to)
        try:
            cube.convert_units(units_to)
        except ValueError:
            logger.warning("Cannot convert units from '%s' to '%s'",
                           cube.units, units_to)
        else:
            data['units'] = units_to
    return (cube, data)


def normalize(cfg, cube, data):
    """Normalize final dataset (by mean and/or by standard deviation)."""
    units = cube.units
    if cfg.get('normalize_mean'):
        logger.debug("Normalizing mean")
        area_weights = _get_area_weights(cfg, cube)
        mean = np.ma.average(cube.data, weights=area_weights)
        cube.data -= mean
        data['long_name'] += ' (mean normalized)'
        data['normalize_mean'] = (
            f"Mean normalized to 0.0 {units} by subtraction, original mean "
            f"was {mean} {units}")
    if cfg.get('normalize_std'):
        logger.debug("Normalizing standard_deviation")
        std = np.ma.std(cube.data)
        cube.data /= std
        data['long_name'] += ' (std normalized)'
        data['units'] = Unit('1')
        data['normalize_std'] = (
            f"Standard deviation scaled to 1.0 by division, original std was "
            f"{std} {units}")
        data['original_units'] = str(units)
    return (cube, data)


def main(cfg):
    """Run the diagnostic."""
    input_data = list(cfg['input_data'].values())
    input_data.extend(io.netcdf_to_metadata(cfg, pattern=cfg.get('pattern')))
    input_data = deepcopy(input_data)
    logger.debug("Found files")
    logger.debug(pformat([d['filename'] for d in input_data]))

    # Default options
    cfg.setdefault('return_trend_stderr', False)

    # Process data
    for data in input_data:
        path = data['filename']
        logger.info("Processing %s", path)
        cube = iris.load_cube(path)

        # Aggregation
        (cube, data) = aggregate(cfg, cube, data)

        # Sum and mean
        (cube, data) = calculate_sum_and_mean(cfg, cube, data)

        # Trend
        (cube, data) = calculate_trend(cfg, cube, data)

        # Argsort
        (cube, data) = calculate_argsort(cfg, cube, data)

        # Convert units
        (cube, data) = convert_units(cfg, cube, data)

        # Cache cube
        data = cache_cube(cfg, cube, data)

    # Add standard errors to regular data
    input_data = add_standard_errors(input_data)

    # Calculate anomalies
    input_data = calculate_anomalies(cfg, input_data)
    data_to_save = (input_data if cfg.get('save_ref_data', False) else
                    select_metadata(input_data, ref=False))

    # Save cubes
    for data in data_to_save:
        data.pop('stderr', None)
        data.pop('ref')
        cube = data.pop('cube')

        # Normalize and write cubes
        (cube, data) = normalize(cfg, cube, data)
        write_cube(cube, data, data['filename'])


# Run main function when this script is called
if __name__ == '__main__':
    with run_diagnostic() as config:
        main(config)
