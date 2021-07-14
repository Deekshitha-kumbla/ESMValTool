"""
GWT Time series diagnostics
=======================

Diagnostic to produce figures of the time development of the GWT field from
cubes. These plost show time on the x-axis and cube value (ie temperature) on
the y-axis.

Two types of plots are produced: individual model timeseries plots and
multi model time series plots. The inidivual plots show the results from a
single cube, even if this is a mutli-model mean made by the _multimodel.py
preproccessor. The multi model time series plots show several models
on the same axes, where each model is represented by a different line colour.

Note that this diagnostic assumes that the preprocessors do the bulk of the
hard work, and that the cube received by this diagnostic (via the settings.yml
and metadata.yml files) has a time component, no depth component, and no
latitude or longitude coordinates.

An approproate preprocessor for a 3D+time field would be::

  preprocessors:
    prep_timeseries_1:# For Global Volume Averaged
      volume_statistics:
        operator: mean


An approproate preprocessor for a 3D+time field at the surface would be::

    prep_timeseries_2: # For Global surface Averaged
      extract_levels:
        levels:  [0., ]
        scheme: linear_extrap
      area_statistics:
        operator: mean


An approproate preprocessor for a 2D+time field would be::

    prep_timeseries_2: # For Global surface Averaged
      area_statistics:
        operator: mean


This tool is part of the ocean diagnostic tools package in the ESMValTool.

Author: Lee de Mora (PML)
        ledm@pml.ac.uk
"""

import logging
import os
import datetime
import iris
import matplotlib.pyplot as plt
logging.getLogger('matplotlib.font_manager').disabled = True
from matplotlib import gridspec
import numpy as np
from itertools import product
import cf_units
import glob
import shelve
import csv

from esmvaltool.diag_scripts.ocean import diagnostic_tools as diagtools
from esmvaltool.diag_scripts.shared import run_diagnostic


# This part sends debug statements to stdout
logger = logging.getLogger(os.path.basename(__file__))


def timeplot(cube, **kwargs):
    """
    Create a time series plot from the cube.

    Note that this function simple does the plotting, it does not save the
    image or do any of the complex work. This function also takes and of the
    key word arguments accepted by the matplotlib.pyplot.plot function.
    These arguments are typically, color, linewidth, linestyle, etc...

    If there's only one datapoint in the cube, it is plotted as a
    horizontal line.

    Parameters
    ----------
    cube: iris.cube.Cube
        Input cube

    """
    cubedata = np.ma.array(cube.data)
    if len(cubedata.compressed()) == 1:
        plt.axhline(cubedata.compressed(), **kwargs)
        return

    times = diagtools.cube_time_to_float(cube)
    plt.plot(times, cubedata, **kwargs)

ssp_title_dict = {
    'ssp119': 'SSP1 1.9',
    'ssp125': 'SSP1 2.5',
    'ssp126': 'SSP1 2.6',
    'ssp245': 'SSP2 4.5',
    'ssp370': 'SSP3 7.0',
    'ssp585': 'SSP5 8.5',
    }


def moving_average(cube, window):
    """
    Calculate a moving average.

    The window is a string which is a number and a measuremet of time.
    For instance, the following are acceptable window strings:

    * ``5 days``
    * ``12 years``
    * ``1 month``
    * ``5 yr``

    Also note the the value used is the total width of the window.
    For instance, if the window provided was '10 years', the the moving
    average returned would be the average of all values within 5 years
    of the central value.

    In the case of edge conditions, at the start an end of the data, they
    only include the average of the data available. Ie the first value
    in the moving average of a ``10 year`` window will only include the average
    of the five subsequent years.

    Parameters
    ----------
    cube: iris.cube.Cube
        Input cube
    window: str
        A description of the window to use for the

    Returns
    ----------
    iris.cube.Cube:
        A cube with the movinage average set as the data points.

    """
    if 'time' not in [coord.name() for coord in cube.coords()]:
        return

    window = window.split()
    window_len = int(window[0]) / 2.
    win_units = str(window[1])

    if win_units not in [
            'years', 'yrs',
            'year', 'yr'
    ]:
        raise ValueError("Moving average window units not recognised: " +
                         "{}".format(win_units))

    times = cube.coord('time').units.num2date(cube.coord('time').points)

    cal_dt = diagtools.guess_calendar_datetime(cube)

    output = []

    times = np.array([
        cal_dt(time_itr.year, time_itr.month, time_itr.day, time_itr.hour,
               time_itr.minute) for time_itr in times
    ])

    for time_itr in times:
        tmin = cal_dt(time_itr.year - window_len, time_itr.month,
                      time_itr.day, time_itr.hour, time_itr.minute)

        tmax = cal_dt(time_itr.year + window_len, time_itr.month,
                      time_itr.day, time_itr.hour, time_itr.minute)

        #if time_itr.year - times.min().year < window_len:

        arr = np.ma.masked_where((times < tmin) + (times > tmax), cube.data)
        output.append(arr.mean())
    cube.data = np.array(output)
    return cube


def calculate_anomaly(cube, anomaly, calc_average=False):
    """
    Calculate the anomaly using a specified time range.

    The anomaly window is a list which includes a starting year and and end
    year to indicate the start and end of the time period in which to calculate
    the anomaly.

    Parameters
    ----------
    cube: iris.cube.Cube
        Input cube
    anomaly: list
        A start year and end year to calculate an anomaly.
    calc_average: bool
        Flag to return the average of the anomaly period, instead of subtracting
        it from the cube.
    Returns
    ----------
    iris.cube.Cube:
        A cube with the anomaly calculated.
    """
    start_year = int(np.array(anomaly).min())
    end_year = int(np.array(anomaly).max())
    end_day = 31
    time_units = cube.coord('time').units
    if time_units.calendar == '360_day':
        end_day = 30

    start_date = datetime.datetime(int(start_year), 1, 1)
    end_date = datetime.datetime(int(end_year), 12, end_day)

    t_1 = time_units.date2num(start_date)
    t_2 = time_units.date2num(end_date)
    constraint = iris.Constraint(
        time=lambda t: t_1 < time_units.date2num(t.point) < t_2)

    new_cube = cube.extract(constraint)
    if new_cube is None:
        return None
    mean = new_cube.data.mean()
    if calc_average:
       return mean
    cube.data = cube.data - mean
    return cube


def get_threshold_exceedance_date(cube, threshold):
    """
    Calculate the threshold exceedance date.

    Assumes that model run ends 2100, and uses 20 year window.
    """
    loc = np.where(cube.data > threshold)[0]
    if not len(loc): return None
    times = cube.coord('time').units.num2date(
        cube.coord('time').points)
    time = times[loc[0]]
    if time.year > 2090.:
        return None
    return time


def print_exceedance_dates(cfg, exceedance_dates, window = 10, short_name = 'tas',mip='Amon', preprocessor='prep_1', grid = 'gn'):
    """
    prints the exceedance_dates in a format ready to go into a recipe.
    exceednace key: (metadata['exp'], metadata['ensemble'], threshold)
    Like this:
      tas_ssp119_15:
        short_name: tas
        preprocessor: prep_1
        additional_datasets:
          - {dataset: UKESM1-0-LL, project: CMIP6, mip: Amon, exp: [historical, ssp119], ensemble: r1i1p1f2, start_year: 2014, end_year: 2034, grid: gn}
          - {dataset: UKESM1-0-LL, project: CMIP6, mip: Amon, exp: [historical, ssp119], ensemble: r3i1p1f2, start_year: 2013, end_year: 2033, grid: gn}
          - {dataset: UKESM1-0-LL, project: CMIP6, mip: Amon, exp: [historical, ssp119], ensemble: r4i1p1f2, start_year: 2017, end_year: 2037, grid: gn}
    """
    # Define the contents
    exps = set()
    ensembles = set()
    thresholds = set()

    for (exp, ens, thresh) in exceedance_dates.keys():
        if exp == 'historical':
            continue
        # print(exp, exp.find('-')+1, exp[exp.find('-')+1:])
        # exp = exp[exp.find('-'):]
        exps.add(exp)
        ensembles.add(ens)
        thresholds.add(thresh)

    txt='      # Procedurally generated recipe contents:'
    # Add the historical ensembles:
    lines = []
    lines.append('\n') #
    lines.append('      '+ '_'.join([short_name, 'historical'])+':') #  tas_ssp119_15:
    lines.append('        short_name: '+ short_name)
    lines.append('        preprocessor: '+ preprocessor)
    lines.append('        additional_datasets:')
    for ens in sorted(ensembles):
        lines.append('         - {'
                     'dataset: UKESM1-0-LL, '
                     'project: CMIP6, '
                     'mip: ' + mip + ', '
                     'exp: historical, '
                     'ensemble: ' + ens + ', '
                     'start_year: 1850, '
                     'end_year: 1900, '
                     'grid: ' + grid + '}'
                     )
    txt += '\n'.join(lines)

    # For each combination of short_name, threshold:
    for exp, thresh in product(sorted(exps), sorted(thresholds)):
        ssp = exp[exp.find('-')+1:]
        lines = []
        lines.append('\n') #
        lines.append('      '+ '_'.join([short_name, ssp, str(thresh)])+':') #  tas_ssp119_15:
        lines.append('        short_name: '+ short_name)
        lines.append('        preprocessor: '+ preprocessor)
        lines.append('        additional_datasets:')

        # matches = []
        for ens in sorted(ensembles):
            print(exp, thresh, ens)
            try:
                exceedance_date = float(exceedance_dates[(exp, ens, thresh)])
            except:
                continue

            start_year = str(int(exceedance_date - window))
            end_year = str(int(exceedance_date + window))

            # What if end year is after 2100?
            if int(end_year)> 2099:
                continue

            lines.append('         - {'
                         'dataset: UKESM1-0-LL, '
                         'project: CMIP6, '
                         'mip: ' + mip + ', '
                         'exp: [historical, ' + ssp + '], '
                         'ensemble: ' + ens + ', '
                         'start_year: ' + start_year + ', '
                         'end_year: ' + end_year + ', '
                         'grid: ' + grid + '}'
                         )
        if len(lines) == 5:
            continue
        txt += '\n'.join(lines)

    txt += '\n'
    print(txt)
    fn = cfg['work_dir']+'/new_recipe.yml'
    print('Saved to: ', fn)
    out = open(fn, 'w')
    out.write(txt)
    out.close()


def marine_gt(data_dict, short, gt): #, cumul=False):
    """
    Calculate global from the data dictionary.
    """
    areas = []
    for (short_name, exp, ensemble), cube in sorted(data_dict.items()):
        if short_name == 'areacello':
            areas.append(cube)

    if len(areas) != 1:
        assert 0
    areas = areas[0]
    if np.sum(areas.data.shape)>1:
        # assume properly masked! (done in preprocessor)
        #print(areas.data.shape)
        areas = areas.collapsed(['longitude', 'latitude'], iris.analysis.SUM)
        #print(areas.data)
        #assert 0
    for (short_name, exp, ensemble), cube in sorted(data_dict.items()):
        if short_name != short:
            continue
        if (gt, exp, ensemble) in data_dict.items():
            continue
        cubegt = cube.copy()
        # print(short, gt, cube.units)
        if cube.units == cf_units.Unit('kg m-2 s-1'):
            cubegt.data = cube.data * areas.data * 1.E-12 * (360*24*60*60)
            cubegt.units = cf_units.Unit('Pg yr^-1')
        elif cube.units == cf_units.Unit('kg m-2'):
            cubegt.data = cube.data * areas.data * 1.E-12
            cubegt.units = cf_units.Unit('Pg')
        elif cube.units == cf_units.Unit('mol m-2 s-1'):
            cubegt.data = cube.data * areas.data * 12.0107* 1.E-15 * (360*24*60*60)
            cubegt.units = cf_units.Unit('Pg yr^-1')
        else:
            print('Units not Recognised:', cube.units)
            assert 0
    #    if cumul:
   #         #print(cubegt.data, np.ma.masked_invalid(cubegt.data), np.cumsum(np.ma.masked_invalid(cubegt.data)))
  #          #assert 0
 #           cubegt.data = np.cumsum(np.ma.masked_invalid(cubegt.data))
#            cubegt.units = cf_units.Unit('Pg yr^-1')
        data_dict[(gt, exp, ensemble)] = cubegt
    return data_dict


def calculate_cumulative(data_dict, short_name, cumul_name, new_units=''):
    """
    Calculate the cumulative sum of the annual data.
    """
    hist_datas = {}
    for (short, exp, ensemble), cube in sorted(data_dict.items()):
        if short_name != short:
            continue
        if exp not in ['historical', ]:
            continue
        hist_cumul_cube = cube.copy()
        times = diagtools.cube_time_to_float(hist_cumul_cube)
        hist_cumul_cube.data = np.cumsum(np.ma.masked_invalid(hist_cumul_cube.data))
        data_dict[(cumul_name, exp, ensemble)] = hist_cumul_cube
        hist_datas[ensemble] = {'time': times, 'data': hist_cumul_cube.data}

    #calculate the cumulative value, and add the historical point to it.
    for (short, exp, ensemble), cube in sorted(data_dict.items()):
        if short_name != short:
            continue
        if exp in ['historical', ]:
            continue
        cumul_cube = cube.copy()
        times = diagtools.cube_time_to_float(cumul_cube)
        hist_point = get_threshold_point(hist_datas[ensemble], np.min(times))
        hist_cumul = hist_datas[ensemble]['data'][hist_point]
        cumul_cube.data = cumul_cube.data
        cumul_cube.data = np.cumsum(np.ma.masked_invalid(cumul_cube.data)) + hist_cumul
        if new_units:
            cumul_cube.units = cf_units.Unit(new_units)
        data_dict[(cumul_name, exp, ensemble)] = cumul_cube

    return data_dict


def fgco2gt(data_dict): return marine_gt(data_dict, short='fgco2', gt='fgco2gt')
def intppgt(data_dict): return marine_gt(data_dict, short='intpp', gt='intppgt')
def epc100gt(data_dict): return marine_gt(data_dict, short='epc100', gt='epc100gt')
def intdicgt(data_dict): return marine_gt(data_dict, short='intdic', gt='intdicgt')
def intpocgt(data_dict): return marine_gt(data_dict, short='intpoc', gt='intpocgt')
def fricgt(data_dict): return marine_gt(data_dict, short='fric', gt='fricgt')
def frocgt(data_dict): return marine_gt(data_dict, short='froc', gt='frocgt')
def frcgt(data_dict): return marine_gt(data_dict, short='frc', gt='frcgt')
def fgco2gt_cumul(data_dict):
    data_dict = marine_gt(data_dict, short='fgco2', gt='fgco2gt')
    return calculate_cumulative(data_dict, short_name='fgco2gt', cumul_name='fgco2gt_cumul', new_units = 'Pg')


def land_gt(data_dict, short='npp', gt='nppgt'):
    """
    Calculate land_gt from the data dictionary.
    """
    areas = []
    for (short_name, exp, ensemble), cube in sorted(data_dict.items()):
        if short_name == 'areacella':
            areas.append(cube)
    if len(areas) != 1:
        print(areas)
        assert 0
    areas = areas[0]
    if np.sum(areas.data.shape)>1:
        # assume properly masked! (done in preprocessor)
        areas = areas.collapsed(['longitude', 'latitude'], iris.analysis.SUM)

    for (short_name, exp, ensemble), cube in sorted(data_dict.items()):
        #rint('land_gt:', short_name, exp, ensemble, [short,gt])
        if short_name != short:
        #   print('land_gt:', short_name,'!=', short)
            continue
        if (gt, exp, ensemble) in data_dict.keys():
            print('land_gt:', gt, 'already calculated')
            continue
        print('land_gt:', short_name, exp, ensemble, [short,gt])
        cubegt = cube.copy()
        cubegt.data = cube.data * areas.data * 1.E-12 * (360*24*60*60)
        cubegt.units = cf_units.Unit('Pg yr^-1') #cube.units * areas.units
        print('land_gt:', (gt, exp, ensemble), cubegt.data.mean())
        data_dict[(gt, exp, ensemble)] = cubegt
    if short=='nbp':
        print(data_dict[(gt, exp, ensemble)])
    return data_dict


def rhgt(data_dict):
    """
    Calculate rhgt from the data dictionary.
    """
    return land_gt(data_dict, short='rh', gt='rhgt')

def nppgt(data_dict): return land_gt(data_dict, short='npp', gt='nppgt')
def gppgt(data_dict): return land_gt(data_dict, short='gpp', gt='gppgt')
def nbpgt(data_dict):
    return land_gt(data_dict, short='nbp', gt='nbpgt')

def nbpgt_cumul(data_dict):
    data_dict = land_gt(data_dict, short='nbp', gt='nbpgt')
    return calculate_cumulative(data_dict, short_name='nbpgt', cumul_name='nbpgt_cumul', new_units='Pg')


def frc(data_dict):
    """
    Calculate total flux to sea floor from the data dictionary.
    """
    #data_dict = fric(data_dict)
    exps = {}
    ensembles = {}
    for (short_name, exp, ensemble)  in sorted(data_dict.keys()):
        exps[exp] = True
        ensembles[ensemble] = True

    for exp, ensemble in product(exps, ensembles):
        if ('fric', exp, ensemble) not in data_dict: continue
        if ('froc', exp, ensemble) not in data_dict: continue
        cube = data_dict[('fric', exp, ensemble)].copy()
        cube2 = data_dict[('froc', exp, ensemble)]
        cube.data = cube.data + cube2.data
        data_dict[('frc', exp, ensemble)] = cube
    return data_dict


def exchange(data_dict, inverse=False):
    """
    Calculate exchange from the data dictionary.
    """
    data_dict = rhgt(data_dict)
    data_dict = nppgt(data_dict)

    exps = {}
    ensembles = {}
    for (short_name, exp, ensemble)  in sorted(data_dict.keys()):
        exps[exp] = True
        ensembles[ensemble] = True

    for exp, ensemble in product(exps, ensembles):
        if ('nppgt', exp, ensemble) not in data_dict: continue
        if inverse == False:
            cube = data_dict[('nppgt', exp, ensemble)].copy()
            cube2 = data_dict[('rhgt', exp, ensemble)]
            cube.data = cube.data - cube2.data
            data_dict[('exchange', exp, ensemble)] = cube

        if inverse == True:
            cube = data_dict[('rhgt', exp, ensemble)].copy()
            cube2 = data_dict[('nppgt', exp, ensemble)]
            cube.data = cube.data - cube2.data
            data_dict[('inverse_exchange', exp, ensemble)] = cube
    return data_dict


def inverse_exchange(data_dict,):
    """
    reverses calculation of exchange.
    """
    return exchange(data_dict, inverse=True)


def tas_norm(data_dict):
    """
    Calculate tas_norm from the data dictionary.
    """
    exps = {}
    ensembles = {}
    baselines={}
    for (short_name, exp, ensemble), cube  in data_dict.items():
        exps[exp] = True
        ensembles[ensemble] = True
        if short_name != 'tas': continue
        if exp != 'historical': continue
        baselines[(short_name, ensemble)] = calculate_anomaly(cube, [1850, 1900], calc_average=True)

    for exp, ensemble in product(exps, ensembles):
        if not ('tas', exp, ensemble) in data_dict.keys(): continue
        cube = data_dict[('tas', exp, ensemble)].copy()
        cube.data = cube.data - baselines[('tas', ensemble)]
        data_dict[('tas_norm', exp, ensemble)] = cube
    return data_dict


def norm_co2(data_dict, short='nppgt'):
    """
    Weight a value according to the ratio of the forcing co2 for each year
    against the average co2 forcing in 1850-1900.
    """
    print(data_dict.keys())
    print(data_dict[('co2', 'historical', 'r1i1p1f2' )]['time'][:50],
          data_dict[('co2', 'historical', 'r1i1p1f2' )]['co2'][:50])
    baseline = np.mean(data_dict[('co2', 'historical', 'r1i1p1f2' )]['co2'][:50])
    new_data_dict = {}
    for (short_name, exp, ensemble), cube  in data_dict.items():
        if short_name != short: continue
        #if exp != 'historical': continue
        cube = data_dict[(short, exp, ensemble)].copy()
        print('norm_co2:', short_name, exp, ensemble, 'baseline:',baseline)
        out = []
        co2_data= data_dict[('co2', exp, ensemble )]['co2']
        if len(cube.data) != len(co2_data):
            times = cube.coord('time').units.num2date(cube.coord('time').points)
            print('times do not match', (short_name, exp, ensemble), len(cube.data), '!=', len(co2_data))
            for t1, t2 in zip(times, data_dict[('co2', exp, ensemble )]['time']):
                print(short_name, exp, ensemble, short_name+':', t1, 'co2:', t2)
            assert 0
        for d,co2 in zip(cube.data, data_dict[('co2', exp, ensemble)]['co2']):
            out.append(d*baseline/co2)
        cube.data = np.ma.array(out)
        new_data_dict[(short+'_norm', exp, ensemble)] = cube
    data_dict.update(new_data_dict)
    return data_dict


def norm_co2_nppgt(data_dict): return norm_co2(data_dict, short='nppgt')
def norm_co2_rhgt(data_dict): return norm_co2(data_dict, short='rhgt')
def norm_co2_exchange(data_dict): return norm_co2(data_dict, short='exchange')
def norm_co2_fgco2gt(data_dict): return norm_co2(data_dict, short='fgco2gt')


def load_timeseries(cfg, short_names):
    """
    Load times series as a dict.

    Dict is :
    data_dict[(short_name, exp, ensemble) ] = cube
    assume only one model
    """
    data_dict_shelve = diagtools.folder([cfg['work_dir'], 'gwt_timeseries'])+'data_dict.shelve'
    load_from_shelve=True  
    if load_from_shelve and glob.glob(data_dict_shelve+'*'):
        print('loading:', data_dict_shelve )
        #assert 0
        sh = shelve.open(data_dict_shelve)
        data_dict = sh['data_dict']
        sh.close()
        return data_dict
    else:
        data_dict = {}

    transforms = {
        'fgco2gt': ['fgco2', 'areacello'],
        'gppgt': ['gpp', 'areacella'],
        'nppgt': ['npp', 'areacella'],
        'nbpgt': ['nbp', 'areacella'],
        'rhgt': ['rh', 'areacella'],
        'epc100gt': ['epc100', 'areacello'],
        'intppgt': ['intpp', 'areacello'],
        'intdicgt': ['intdic', 'areacello'],
        'intpocgt': ['intpoc', 'areacello'],
        'fricgt': ['fric', 'areacello'],
        'frocgt': ['froc', 'areacello'],
        'frc': ['fric', 'froc', 'areacello'],
        'frcgt': ['frc', ],
        'exchange': ['rh', 'npp', 'areacella'],
        'inverse_exchange': ['rh', 'npp', 'areacella'],
        'tas_norm': ['tas', ],
        'nppgt_norm': ['nppgt', ],
        'rhgt_norm': ['rhgt', ],
        'exchange_norm': ['exchange', ],
        'fgco2gt_norm': ['fgco2gt', ],
        'fgco2gt_cumul': ['fgco2', 'areacello' ],
        'nbpgt_cumul' : ['nbp', 'areacella' ],
        # 'tls': ['nbp', 'nbpgt', 'luegt']
        }

    transforms_functions = {
        'fgco2gt': fgco2gt,
        'fgco2gt_cumul': fgco2gt_cumul,
        'gppgt': gppgt,
        'nppgt': nppgt,
        'nbpgt': nbpgt,
        'nbpgt_cumul': nbpgt_cumul,

        'rhgt': rhgt,
        'epc100gt': epc100gt,
        'intppgt': intppgt,
        'intdicgt': intdicgt,
        'intpocgt': intpocgt,
        'fricgt': fricgt,
        'frocgt': frocgt,
        'frc': frc,
        'frcgt': frcgt,
        'exchange': exchange,
        'inverse_exchange': inverse_exchange,
        'tas_norm': tas_norm,
        'nppgt_norm':norm_co2_nppgt,
        'rhgt_norm':norm_co2_rhgt,
        'exchange_norm':norm_co2_exchange,
        'fgco2gt_norm':norm_co2_fgco2gt,
        }

    short_names_to_load = short_names.copy()

    for sn in short_names:
        if sn in transforms:
            short_names_to_load.extend(transforms[sn])

    for index, metadata_filename in enumerate(cfg['input_files']):
        logger.info('load_timeseries:\t%s', metadata_filename)

        metadatas = diagtools.get_input_files(cfg, index=index)
        for fn in sorted(metadatas):
            short_name = metadatas[fn]['short_name']
            exp = metadatas[fn]['exp']
            ensemble = metadatas[fn]['ensemble']

            if short_name not in short_names_to_load:
                continue
            if data_dict.get((short_name, exp, ensemble), False):
                continue
            cube = iris.load_cube(fn)
            #cube = diagtools.bgc_units(cube, short_name)

            print('load_timeseries:\t%s successfull loaded data:', (short_name, exp, ensemble), 'mean:', cube.data.mean())
            data_dict[(short_name, exp, ensemble)] = cube

    if 'co2' in short_names_to_load:
        data_dict = load_co2_forcing(cfg, data_dict)

    if 'luegt' in short_names_to_load or 'tls' in short_names_to_load:
        data_dict = load_luegt(cfg, data_dict)
        #if 'tls' in short_names_to_load:
        #    data_dict = calc_tls(cfg, data_dict)
        #    print(data_dict.keys())
        #    assert 0
    #print(short_names_to_load)

    if set(['emissions', 'cumul_emissions']) & set(short_names_to_load):
        data_dict = load_emissions_forcing(cfg, data_dict)

    for sn in short_names_to_load:
        if sn in transforms:
            data_dict = transforms_functions[sn](data_dict)

    calculate_mean = True
    if calculate_mean:
        short_names, exps = {}, {}
        for (short_name, exp, ensemble) in  data_dict.keys():
            short_names[short_name] = True
            exps[exp] = True
        for short_name, exp in product(short_names.keys(), exps.keys()):
            cubes = []
            for (short_name_i, exp_i, ensemble_i),cube in  data_dict.items():
                if short_name != short_name_i: continue
                if exp_i != exp: continue
                if ensemble_i == 'ensemble_mean': continue
                if short_name in ['co2', 'emissions', 'cumul_emissions', 'luegt', 'tls', 'atmos_carbon']:
                     continue
                cubes.append(cube)

            if not len(cubes):
                continue
            elif len(cubes) == 1:
                data_dict[(short_name, exp, 'ensemble_mean')] = cubes[0]
            else:
                data_dict[(short_name, exp, 'ensemble_mean')] = diagtools.make_mean_of_cube_list(cubes)

    if 'tls' in short_names_to_load:
        data_dict = calc_tls(cfg, data_dict)
        print(data_dict.keys())

    if 'atmos_carbon' in short_names_to_load:
        data_dict = calc_atmos_carbon(cfg, data_dict)
        print(data_dict.keys())


    print('saving::', data_dict_shelve )
    sh = shelve.open(data_dict_shelve)
    sh['data_dict'] = data_dict
    sh.close()
    return data_dict


def load_thresholds(cfg, data_dict, short_names = ['tas', ], thresholds = [1.5, 2., 3., 4., 5.], ):
    """
    Load thresholds  as a dict.

    Dict is :
    data_dict[(short_name, exp, ensemble) ] = {threshold: year}
    """
    thresholds_shelve = diagtools.folder([cfg['work_dir'], 'gwt_timeseries'])+'thresholds.shelve'
    if glob.glob(thresholds_shelve+'*'):
        print('opening:', thresholds_shelve)
        sh = shelve.open(thresholds_shelve)
        thresholds_dict = sh['thresholds_dict']
        sh.close()
        return thresholds_dict

    thresholds_dict = {}
    baselines = {}
    for (short_name, exp, ensemble), cube in sorted(data_dict.items()):
        if short_name not in short_names:
             continue
        if exp != 'historical': continue
        baselines[(short_name, ensemble)] = calculate_anomaly(cube, [1850, 1900], calc_average=True)

    for (short_name, exp, ensemble), cube in sorted(data_dict.items()):
        if short_name not in short_names:
             continue

        cube2 = moving_average(cube.copy(), '21 years')
        cube2.data = cube2.data - baselines[(short_name, ensemble)]

        thresholds_dict[(short_name, exp, ensemble)] = {}

        for threshold in thresholds:
            time = get_threshold_exceedance_date(cube2, threshold)
            thresholds_dict[(short_name, exp, ensemble)][threshold] = time

    print('Saving:', thresholds_shelve)
    sh = shelve.open(thresholds_shelve)
    sh['thresholds_dict'] = thresholds_dict
    sh.close()
    return thresholds_dict


def cube_to_years(cube):
    """
    Convert from time coordinate into years.

    Takes an iris time coordinate and returns a list of floats.
    Parameters
    ----------
    cube: iris.cube.Cube
        the opened dataset as a cube.

    Returns
    -------
    list
        List of floats showing the time coordinate in decimal time.

    """
    if not cube.coords('year'):
        iris.coord_categorisation.add_year(cube, 'time')
    return cube.coord('year').points


def load_co2_forcing(cfg, data_dict):
    """
    Load annual CO2 data from the auxiliary datasets.

    Unlike the rest of data_dcit, it's isn't loaded as a cube, but rather as a
    dict.
    """
    fold = cfg['auxiliary_data_dir']+'/atmos_co2_forcing/'
    files = glob.glob(fold+'*.dat')
    print(files)
    hist_datas = []
    hist_times = []
    ssp585_datas = []
    ssp585_times = []

    # load the co2 from the file.
    for fn in files:
        open_fn = open(fn, 'r')
        key = os.path.basename(fn).replace('_co2.dat', '')
        times = []
        data = []
        for line in open_fn.readlines()[1:]:
            line = line.split(' ')
            for x in range(len(line)):
                if '' in line: line.remove('')
                if '\n' in line: line.remove('\n')
            t = float(line[0]) + 0.5
            times.append(t)
            data.append(float(line[1]))
        if key == 'historical':
            hist_datas = np.array(data).copy()
            hist_times = np.array(times).copy()
        if key == 'ssp585':
            ssp585_datas = np.array(data).copy()
            ssp585_times = np.array(times).copy()
        for ens in ['r1', 'r2',  'r3', 'r4', 'r8']:
            data_dict[('co2', key, ens+'i1p1f2' )] = {'time': times, 'co2':data}
            print('load_co2_forcing:\t%s successfull loaded data:', ('co2', key, ens+'i1p1f2'), 'mean:', np.array(data).mean())
        data_dict[('co2', key, 'ensemble_mean' )] = {'time': times, 'co2':data}
        open_fn.close()

    # Check for historical-ssp scenarios pairs.
    tmp_dict = {}
    for (short_name, exp, ensemble), ssp_cube in data_dict.items():
        if short_name in ['co2', 'areacella', 'areacello',]:
            continue
        if ('co2', 'historical-'+exp, 'historical-ssp585-'+exp, ensemble ) in tmp_dict.keys():
            continue
        if exp == 'historical':
            continue
        ssp_only = exp.replace('historical-', '')
        if ssp_only == 'ssp585-ssp534-over':
            ssp_only = 'ssp534-over'
        new_times = []
        new_datas = []
        print((short_name, exp,(ssp_only), ensemble))
        ssp_times = cube_to_years(ssp_cube)
        min_time = np.array(ssp_times).min()
        if exp == 'historical-ssp585-ssp534-over':
            print(exp, len(new_times), len(new_datas))
            new_times = list(np.ma.masked_where(hist_times<min_time, hist_times).compressed())
            new_datas = list(np.ma.masked_where(hist_times<min_time, hist_datas).compressed())
            print(exp, len(new_times), len(new_datas) )
            new_times.extend(np.ma.masked_where(ssp585_times >= 2040., ssp585_times).compressed())
            new_datas.extend(np.ma.masked_where(ssp585_times >= 2040., ssp585_datas).compressed())
            print(exp, len(new_times), len(new_datas))
            new_times.extend(data_dict[('co2', ssp_only, ensemble)]['time'])
            new_datas.extend(data_dict[('co2', ssp_only, ensemble)]['co2'])
            print(exp, len(new_times), len(new_datas))
        else:
            if min_time > np.array(hist_times).max():
                print(short_name, exp, ensemble, 'no overlap', ('ssp:', min_time, '>', 'hist max:', np.array(hist_times).max()))
                # no overlap
                new_times = data_dict[('co2', ssp_only, ensemble)]['time']
                new_datas = data_dict[('co2', ssp_only, ensemble)]['co2']
            else:
                # Some overlap
                print(short_name, exp, ensemble,'some overlap', (min_time, '<=', np.array(hist_times).max()))
                new_times = list(np.ma.masked_where(hist_times<min_time, hist_times).compressed())
                new_datas = list(np.ma.masked_where(hist_times<min_time, hist_datas).compressed())
                new_times.extend(data_dict[('co2', ssp_only, ensemble)]['time'])
                new_datas.extend(data_dict[('co2', ssp_only, ensemble)]['co2'])


        if len(new_times) != len(ssp_times):
            print('New times do not match old times:', len(new_times), '!=', len(ssp_times),'\nnew:',new_times, '\nssp:',ssp_times)
            assert 0
        print('co2', exp, ensemble, len(new_times), len(new_datas))
        tmp_dict[('co2', exp, ensemble )] ={'time': new_times, 'co2':new_datas}

    data_dict.update(tmp_dict)
    # make sure the ensemble mean is set for all co2.
    for (short_name, exp, ensemble), ssp_cube in data_dict.items():
        if short_name not in ['co2', ]: continue
        tmp_dict[(short_name, exp, 'ensemble_mean')] = ssp_cube
    data_dict.update(tmp_dict)


    # Save the co2 image:
    path = diagtools.folder(cfg['plot_dir'])
    image_extention = diagtools.get_image_format(cfg)
    path += 'co2_forcing' + image_extention
    if not os.path.exists(path):
        exp_colours = {'historical':'black',
                       'ssp119':'green',
                       'ssp126':'dodgerblue',
                       'ssp245':'blue',
                       'ssp370':'purple',
                       'ssp434':'magenta',
                       'ssp585': 'red',
                       'ssp534-over':'orange'}
        for key in exp_colours.keys():
            plt.plot(data_dict[('co2', key, 'r1i1p1f2' )]['time'],
                     data_dict[('co2', key, 'r1i1p1f2' )]['co2'],
                     c=exp_colours[key],
                     label=key)
        plt.legend()
        plt.savefig(path)
        plt.close()
    image_extention = diagtools.get_image_format(cfg)
    path += 'co2_forcing_hists' + image_extention
    if not os.path.exists(path):
        exp_colours = {'historical':'black',
                       'historical-ssp119':'green',
                       'historical-ssp126':'dodgerblue',
                       'historical-ssp245':'blue',
                       'historical-ssp370':'purple',
                       'historical-ssp434':'magenta',
                       'historical-ssp585': 'red',
                       'historical-ssp585-ssp534-over':'orange'}
        for key in exp_colours.keys():
            plt.plot(data_dict[('co2', key, 'r1i1p1f2' )]['time'],
                     data_dict[('co2', key, 'r1i1p1f2' )]['co2'],
                     c=exp_colours[key],
                     label=key)
        plt.legend()
        plt.savefig(path)
        plt.close()
    return data_dict


def load_emissions_forcing(cfg, data_dict):
    """
    Load annual CO2 data from the auxiliary datasets.

    Unlike the rest of data_dcit, it's isn't loaded as a cube, but rather as a
    dict.
    """
    fold = cfg['auxiliary_data_dir']+'/emissions/'
    files = glob.glob(fold+'*.txt')
    #print(files)
    #hist_datas = []
    #hist_times = []
    #ssp585_datas = []
    #ssp585_times = []
    exps = {}
    ensembles = {'ensemble_mean': True}
    for (short_name, exp, ensemble)  in sorted(data_dict.keys()):
        exps[exp] = True
        ensembles[ensemble] = True

    # load the co2 from the file.
    for fn in files:
        times = []
        data = []

        open_fn = open(fn, 'r')
        scenario = os.path.basename(fn)
        scenario = scenario.replace('UKESM1_', '')
        scenario = scenario.replace('.txt', '')
        scenario = scenario.replace('historical_', 'historical-')

        for line in open_fn.readlines()[2:]:
            line = [x.replace('\n', '') for x in line.split(' ')]
            t = float(line[0]) + 0.5
            #if t > 2100.: continue
            times.append(t)
            data.append(float(line[3]))
            # print (fn, line)
        # for t,d in zip(times,data): print(scenario, t,d)
        if scenario.find('historical')==-1:
            # no need to double up.
            continue

        for ensemble in ensembles:
            data_dict[('emissions', scenario, ensemble)] = {'time': times, 'emissions':data}
            #if scenario.find('ssp119')> -1:
            #    print('load_emissions_forcing:', scenario, len(times), len(data))
            data_dict[('cumul_emissions', scenario, ensemble)] = {'time': times, 'cumul_emissions':np.cumsum(data)}

    return data_dict

def calc_tls(cfg, data_dict):
    """
    Load True  Land Sink by adding Land Use Emissions from file and nbp
    Net biome production.
    """
    exps = {'ssp119':True, 'ssp126':True, 'ssp245':True, 'ssp370':True, 'ssp585':True, 'historical':True}
    ensembles = {'ensemble_mean':True}
    for (short_name, exp, ensemble)  in sorted(data_dict.keys()):
        exps[exp] = True
        ensembles[ensemble] = True
    print(exps, ensembles)
    tmp_data_dict = {}
    short = 'tls'
    for (short, exp, ensemble), cube in data_dict.items():
        print('calc_tls:',(short, exp, ensemble))
        if short not in ['nbpgt_cumul', ]: # 'nbpgt_cumul']:
             continue
        times = diagtools.cube_time_to_float(cube)
        data = cube.data.copy()
        nbp_dict = {int(t):d for t,d in zip(times, data)}
        luegt_dict = data_dict.get(('luegt', exp, ensemble), False)
        if not luegt_dict: continue
        for t, d in zip(luegt_dict['time'], luegt_dict['luegt']):
            #rint(t,d, nbp_dict.get(t, nbp_dict.get(int(t), False)))
            if not nbp_dict.get(t, nbp_dict.get(int(t), False)):
                print('error:', (short, exp, ensemble), t, 'not in', nbp_dict.keys())
                continue
            nbp_dict[int(t)] += d

        new_times = np.array(sorted(nbp_dict.keys()))
        new_data = np.array([nbp_dict[t] for t in new_times])

        tmp_data_dict[('tls', exp, ensemble)] = {'time':new_times+0.5, 'tls': new_data }

    print(tmp_data_dict.keys(), tmp_data_dict)
    if not len(tmp_data_dict): assert 0
    data_dict.update(tmp_data_dict)
    return data_dict


def calc_atmos_carbon(cfg, data_dict):
    """
    Load remaining atmospheric carbon.
    cumul_emissions - nbpgt_cumul - fgco2gt_cumul
    """
    tmp_data_dict = {}
    new_short = 'atmos_carbon'
    for (short, exp, ensemble), tmp_data in data_dict.items():
        print('calc_atmos_carbon:',(short, exp, ensemble))
        if short not in ['cumul_emissions', ]:
            continue
        # tmp_data = {time:times, 'cumul_emissions': dat}
        tmp_data = zip_time(tmp_data, short)
        tmp_data = {int(t):d for t, d in tmp_data.items()}

        # subtrack nbp & ocean carbon flux.
        for cube_key in  ['fgco2gt_cumul', 'nbpgt_cumul']:
            tmp_cube_data = data_dict[(cube_key, exp, ensemble)]
            tmp_cube_data = {'time': diagtools.cube_time_to_float(tmp_cube_data),
                             cube_key: tmp_cube_data.data}
            tmp_cube_data = zip_time(tmp_cube_data, cube_key)
            for t,d in tmp_cube_data.items():
                t = int(t)
                tmp_data[t] = tmp_data[t] - d

        tmp_times, tmp_dat = unzip_time(tmp_data)
        tmp_times = tmp_times+0.5

        tmp_data_dict[(new_short, exp, ensemble)] = {'time': tmp_times, new_short: tmp_dat}

    data_dict.update(tmp_data_dict)
    return data_dict


def load_luegt(cfg, data_dict):
    """
    Load Land Use Emissions from file.
    """
    # load from file
    tmp_data_dict = {}
    short = 'luegt'
    # open files
    # for each column, create a dict
    # add year: value to the column.
    # edit file header so that it closely matches other data.
    # set one for each dataset

    exps = {'ssp119':True, 'ssp126':True, 'ssp245':True, 'ssp370':True, 'ssp585':True, 'historical':True}
    ensembles = {'ensemble_mean':True}
    for (short_name, exp, ensemble)  in sorted(data_dict.keys()):
        exps[exp] = True
        ensembles[ensemble] = True
    print(exps, ensembles)
    # assert 0
    # This data was added to the esmvaltool/diagnostics/ocean/aux_data directory.
    aux_fn = cfg['auxiliary_data_dir']+'/land_usage/landusage_ssp.txt'
    data = {}
    years = []
    header = {}
    print('load_luegt', ensembles, exps)
    with open(aux_fn) as csvDataFile:
        csvReader = csv.reader(csvDataFile, delimiter=';')
        for row_number, row in enumerate(csvReader):
            print('load_luegt: ', row_number, row)
            if row_number == 0:
                for d, da in enumerate(row):
                    print('load_luegt: header: ', d, da)
                    da = da.replace(' ', '')
                    header[d] = da
                    data[da] = []
                print('load_luegt: header:', header)
                continue

            years.append(float(row[0])+0.5)
            for d, da in enumerate(row):

                #if d == 0: continue
                if da == ' ': continue
                data[header[d]].append(float(da))

    for exp, ensemble in product(exps.keys(), ensembles.keys()):
        print(exp, ensemble)
        da =  data.get(exp, [])
        if not da:
            da =  data.get(exp.replace('historical-',''), [])
        if not da:
            da =  data.get('-'.join(['historical',exp]), [])
        if not da:
            print('problem', exp, 'and', exp.replace('historical-',''), 'not int', data.keys())
            #assert 0
            continue
        if len(da) != len(years):
            print("data and time don't match:", len(da),  len(years))
            y2 = years[:len(da)]
        else:
            y2 = years
        tmp_data_dict[(short, exp, ensemble)] = {'time': y2, short: da}

    #print(tmp_data_dict.keys())
    #assert 0
    data_dict.update(tmp_data_dict)
    #assert 0
    return data_dict




def get_threshold_point(cube, year):
    """
    get the location of the year provided.
    """
    if isinstance(cube, dict):
        if np.min(np.abs(np.array(cube['time']) - year)) > 5.:
             print('Problem with thresholds:', np.min(cube['time']), '-', np.max(cube['time']), year)
             return None
        arg_min = np.argmin(np.abs(np.array(cube['time']) - year))
        #print(arg_min, year, np.array(cube['time']))
    else:
        time_units = cube.coord('time').units
        date = datetime.datetime(int(year), 6, 1)
        t_1 = time_units.date2num(date)
        years = np.array(diagtools.cube_time_to_float(cube))
        if np.min(np.abs(years - year)) > 5.:
             print('Problem with thresholds:', np.min(years), '-', np.max(years), year)
             return None
        arg_min = np.argmin( np.abs(cube.coord('time').points - t_1))
    #print('get_threshold_point',t_1, date, arg_min)
    return arg_min


def get_long_name(name):
    """
    Get a title friendly longname.
    """
    longnames = {
        'tas' : 'Temperature',
        'tas_norm' : 'Temperature',
        'co2' : 'Atmospheric CO2',
        'emissions' : 'Anthropogenic emissions',
        'cumul_emissions': 'Cumulative Anthropogenic emissions',
        'luegt': 'Land Use Emissions',
        'tls': 'True Land Sink',
        'rh': 'Heterotrophic respiration',
        'intpp' : 'Marine Primary Production',
        'intdic' : 'Dissolved Inorganic Carbon',
        'intpoc' : 'Particulate Organic Carbon',
        'epc100' : 'POC flux at 100m',
        'npp'   : 'Net Primary Production on Land',
        'fgco2' : 'Air sea Flux of CO2',
        'frc':  'Carbon Flux at sea floor',
        'fric':  'Inorganic Carbon Flux at sea floor',
        'froc':  'Organic Carbon Flux at sea floor',
        'nbp': 'Net Biome Production',
        'nbpgt_cumul': 'Cumulative Global Total Net Biome Production',
        'fgco2gt_cumul': 'Cumulative Global Total Air sea Flux of CO2',
        'atmos_carbon': 'Remnat Atmospheric Anthropogenic CO2',
    }
    long_name = ''
    if name.find('gt_norm') > -1:
        long_name += 'Normalised Global Total '
        name = name[name.find('gt_norm')]
    elif name[-2:] == 'gt':
        long_name += 'Global Total '
        name = name[:-2]

    return long_name + longnames.get(name, name)


def make_ts_figure(cfg, data_dict, thresholds_dict, x='time', y='npp',
    markers='thresholds',
    draw_line=True,
    do_moving_average=True,
    ensemble_mean = False,
    fig=None,
    ax=None,
    #short_time_range = False,
    ):
    """
    make a 2D figure.
    x axis and y axis are determined by the short_names provuided in x and y
    vars.
    Markers are placed at certain points when the tas goes above thresholds.

    Parameters
    ----------
    cfg: dict
        the opened global config dictionairy, passed by ESMValTool.
    """
    exps = {}
    ensembles = {}
    for (short_name, exp, ensemble)  in sorted(data_dict.keys()):
         exps[exp] = True
         ensembles[ensemble] = True
         print(short_name, exp, ensemble)

    exp_colours = {'historical':'black',
                   'ssp119':'green',
                   'ssp126':'dodgerblue',
                   'ssp245':'blue',
                   'ssp370':'purple',
                   'ssp434':'magenta',
                   'ssp585': 'red',
                   'ssp534-over':'orange',
                   'historical-ssp119':'green',
                   'historical-ssp126':'dodgerblue',
                   'historical-ssp245':'blue',
                   'historical-ssp370':'purple',
                   'historical-ssp434':'magenta',
                   'historical-ssp585': 'red',
                   'historical-ssp585-ssp534-over':'orange'}

    marker_styles = {1.5: 'o', 2.:'*', 3.:'D', 4.:'s', 5.:'X'}

    if ensemble_mean: ensembles = ['ensemble_mean', ]
    exps = sorted(exps.keys())
    exps.reverse()
    print(exps, ensembles)
    print(data_dict.keys())

    if fig == None:
        fig = plt.figure()
        ax = fig.add_subplot(111)
        make_figure_here = True
    else:
        make_figure_here = False
        plt.sca(ax)

    x_label,y_label = [], []
    print('\n\n\n\n\nStaring plot:',
        'x:', x,
        'y:', y,
        'markers:', markers,
        'draw_line:', draw_line,
        'do_moving_average:', do_moving_average,
        'ensemble_mean:', ensemble_mean)
    #print(data_dict.keys())
    number_of_lines=0
    #for exp_1, ensemble_1 in product(exps, ensembles):
        #print('\nproduct loop', exp_1, ensemble_1)
        #txt =
        ##print('co2:', data_dict.get(('co2', exp_1, ensemble_1), 'Not Found'))
        #print('tas:', data_dict.get(('tas', exp_1, ensemble_1), 'Not Found'))

    label_dicts = {
        'co2': ' '.join(['Atmospheric co2, ppm']),
        'emissions': ' '.join(['Anthropogenic emissions, Pg/yr']),
        'cumul_emissions': ' '.join(['Cumulative Anthropogenic emissions, Pg']),
        'luegt': ' '.join(['Land use emissions, Pg']),
        'tls': ' '.join(['True Land Sink, Pg']),
        'atmos_carbon': 'Remnant Anthropogenic CO2, Pg',
    }
    for exp_1, ensemble_1 in product(exps, ensembles):

        x_data, y_data = [], []
        x_times, y_times = [], []
        for (short_name, exp, ensemble), cube in sorted(data_dict.items()):
            if short_name not in [x,y]: continue
            if exp != exp_1: continue
            if ensemble != ensemble_1: continue
            print('Everything matches', (short_name, exp, ensemble),'vs', [x,y], (exp_1, ensemble_1))
#           print(exp_1, ensemble_1, short_name, exp, ensemble, ensemble_mean)
#            if ensemble_mean and ensemble!= 'ensemble_mean': continue
#            if not ensemble_mean and ensemble == 'ensemble_mean': continue

            print('make_ts_figure: found', short_name, exp, ensemble, x,y)
            if x == 'time' and short_name == y:
                x_label = 'Year'
                if isinstance(cube, iris.cube.Cube):
                    x_data = diagtools.cube_time_to_float(cube)
                    x_times = x_data.copy()
                    print('setting x time to ',short_name, exp, ensemble)
                else:
                    x_data = cube['time']
                    x_times = x_data.copy()
                    print('setting x time to ',short_name, exp, ensemble)
            elif x == short_name and x in label_dicts.keys():
                x_data = cube[x].copy()
                x_times = cube['time'].copy()
                print('setting x time to ',short_name, exp, ensemble)
                x_label = label_dicts[x]

            elif short_name == x:
                x_data = np.array(cube.data.copy())
                x_times = diagtools.cube_time_to_float(cube)
                print('setting x axis to ',short_name, exp, ensemble, np.min(x_data), np.max(x_data))
                x_label = ' '.join([get_long_name(x), str(cube.units)])

            if y == 'time':
                print('what kind of crazy person plots time on y axis?')
                assert 0
            elif y == short_name and y in label_dicts.keys():
                #['co2', 'emissions', 'cumul_emissions', 'luegt']:
                y_data = cube[y].copy()
                y_times = cube['time'].copy()
                y_label = label_dicts[y]
                print('setting y time to ',short_name, exp, ensemble)
            elif short_name == y:
                print(short_name, 'is a cube for y ts plot..')
                y_data = cube.data.copy()
                y_times = diagtools.cube_time_to_float(cube)
                print('setting y time to ',short_name, exp, ensemble, y_data)
                y_label = ' '.join([get_long_name(y), str(cube.units)])

            print('make_ts_figure: loaded x data', short_name, exp, ensemble, x, np.mean(x_data))
            print('make_ts_figure: loaded y data', short_name, exp, ensemble, y, np.mean(y_data))
            #break

        if 0 in [len(x_data), len(y_data), len(x_times), len(y_times)]:
            print('no data found', x,'vs',y, (exp_1, ensemble_1), 'x:', len(x_data), 'y:',len(y_data))
            #assert 0
            continue

        if len(x_data) != len(x_times) or len(y_data) != len(y_times):
            print('x:', len(x_data), len(x_times), 'y:', len(y_data), len(y_times))
            assert 0

        label = ' '.join([exp_1, ensemble_1])
        if draw_line:
            x_times = np.ma.array(x_times)
            y_times = np.ma.array(y_times)
            number_of_lines+=1
            if ensemble_mean:
                lw=1.3
            else:
                lw=0.5
            if exp_1 == 'historical':
                histx_t = np.ma.masked_where(x_times > 2005., x_times)
                histy_t = np.ma.masked_where(y_times > 2005., y_times)
                histx_d = np.ma.masked_where(histx_t.mask, x_data).compressed()
                histy_d = np.ma.masked_where(histy_t.mask, y_data).compressed()

                print('historical', histx_t, histy_t, histx_d, histy_d)

                #plt.plot(np.ma.masked_where(x_times > 2005., x_data),
                #         np.ma.masked_where(y_times > 2005., y_data),
                #         lw=lw,
                #         color=exp_colours[exp_1])

                plt.plot(histx_d, #np.ma.masked_where(x_times > 2005., x_data),
                         histy_d, #np.ma.masked_where(y_times > 2005., y_data),
                         lw=lw,
                         color=exp_colours[exp_1])

            else:
                #print(exp_1, np.ma.masked_where((2005 > x_times) + (x_times > 2015), x_times))
                tdatcx = np.ma.masked_where((2004. > x_times) + (x_times > 2015.), x_times).compressed()
                tdatcy = np.ma.masked_where((2004. > y_times) + (y_times > 2015.), y_times).compressed()
                datcx = np.ma.masked_where((2004. > x_times) + (x_times > 2015.), x_data).compressed()
                datcy = np.ma.masked_where((2004. > y_times) + (y_times > 2015.), y_data).compressed()
                print('2004-2015:', (x,y),exp_1, tdatcx, tdatcy, datcx, datcy)
                if len(tdatcx) == len(tdatcy):
                    plt.plot(
                        datcx, # np.ma.masked_where((2004 > x_times) + (x_times > 2015), x_data).compressed(),
                        datcy, # np.ma.masked_where((2004 > y_times) + (y_times > 2015), y_data).compressed(),
                        lw=lw,
                        color=exp_colours['historical'])

                xdatc = np.ma.masked_where((x_times < 2015.) + (x_times > 2100.), x_data).compressed()
                ydatc = np.ma.masked_where((y_times < 2015.) + (y_times > 2100. ), y_data).compressed()
                xtdatc = np.ma.masked_where((x_times < 2015.) + (x_times > 2100.), x_times).compressed()
                ytdatc = np.ma.masked_where((y_times < 2015.) + (y_times > 2100. ), y_times).compressed()

                print('< 2015', exp_1, xdatc, ydatc, xtdatc, ytdatc)
                if len(xtdatc) == len(ytdatc):
                    plt.plot(xdatc, # np.ma.masked_where(x_times < 2015., x_data).compressed(),
                         ydatc, # np.ma.masked_where(y_times < 2015., y_data).compressed(),
                         lw=lw,
                         color=exp_colours[exp_1])

#                plt.plot(np.ma.masked_where(x_times < 2015., x_data).compressed(),
#                         np.ma.masked_where(y_times < 2015., y_data).compressed(),
#                         lw=lw,
#                         color=exp_colours[exp_1])


        if markers == 'thresholds':
            try: threshold_times = thresholds_dict[('tas', exp_1, ensemble_1)]
            except:
               threshold_times = {}
            ms = 8
            if ensemble_mean:
                ms = 8
            for threshold, time in threshold_times.items():
                if not time:
                    continue
                x_point = get_threshold_point({'time':x_times}, time.year)
                #_point = get_threshold_point(cube, time.year)
                y_point = get_threshold_point({'time':y_times}, time.year)

                print('thresholds:', exp_1, ensemble_1,x,y, threshold, time, x_point, y_point, len(x_data),len(y_data))
                #assert 0
                plt.plot(x_data[x_point],
                         y_data[y_point],
                         marker_styles[threshold],
                         markersize = ms,
                         fillstyle='none',
                         color=exp_colours[exp_1])
                plt.plot(x_data[x_point],
                         y_data[y_point],
                         'o',
                         markersize = 2,
                         #fillstyle='none',
                         color=exp_colours[exp_1])

#        if x == short_name == 'tas_norm':
#            for line in [0, 1.5, 2, 3, 4, 5]:
#                print(line, x,y,'x:',x_data)
#                plt.axvline(line, 'k', ':')

#        if y == short_name == 'tas_norm':
#            for line in [0, 1.5, 2, 3, 4, 5]:
#                print(line, x, y, 'y:',y_data)
#                plt.axhline(line, 'k', ':')

    if not number_of_lines:
        print('No lines plotted')
        plt.close()
        return
        #assert 0

    exp_colours_leg = {'historical':'black',
                   'ssp119':'green',
                   'ssp126':'dodgerblue',
                   'ssp245':'blue',
                   'ssp370':'purple',
                   'ssp434':'magenta',
                   'ssp585': 'red',
                   'ssp534-over':'orange'}


    plot_details = {}
    for exp,color in sorted(exp_colours_leg.items()):
        plot_details[exp] = {
                    'c': color,
                    'ls': '-',
                    'lw': 2.,
                    'label': exp
                }
    for thres,ms in sorted(marker_styles.items()):
        plot_details[str(thres)] = {
                    'c': 'black',
                    'marker': ms,
                    'fillstyle':'none',
                    'label': '>' + str(thres)+u'\u00B0C'
                }

    diagtools.add_legend_outside_right(
                plot_details, plt.gca(), column_width=0.175)

    plt.xlabel(x_label)
    plt.ylabel(y_label)
    if x == 'time':
        plt.title(get_long_name(y))
    else:
        plt.title(' '.join([get_long_name(x), 'by', get_long_name(y)]))

    image_extention = diagtools.get_image_format(cfg)

    path = diagtools.folder(cfg['plot_dir'])

    if ensemble_mean:
        ensemble_mean_txt = 'ensemble_mean'
    else:
        ensemble_mean_txt = 'all'

    path += '_'.join([x, y, markers, ensemble_mean_txt]) + image_extention
    if do_moving_average:
        path = path.replace(image_extention, '_21ma'+image_extention)
    print('saving figure:', path)
    if make_figure_here:
        plt.savefig(path)
        plt.close()
    else:
        return fig, ax


def make_bar_chart(cfg, data_dict, thresholds_dict, threshold = '2.0',
    year0 = None,
    do_legend=True,
    land_carbon = 'nbpgt',
    atmos='atmos_carbon',
    fig=None, ax=None):
    """
    Make a bar chart (of my favourite pies)
    """
    emissions = []
    remnant = []
    landcs = []
    fgco2gts = []
    experiments = []

    for (t_short, t_exp, t_ens), threshold_times in thresholds_dict.items():
        if t_short != 'tas': continue
        if t_ens != 'ensemble_mean': continue

        print("make_bar_chart", t_short, t_exp, t_ens)
        cumul_emissions = data_dict.get(('cumul_emissions', t_exp, t_ens), None) #dict
        atmos_carbon = data_dict.get((atmos,  t_exp, t_ens), None) #dict
        if cumul_emissions is None:  # Because not all sceanrios have emissions data.
           continue

        if atmos_carbon is None:  # Because not all sceanrios have emissions data.
           continue

        if land_carbon == 'nbpgt':
            landc_cumul = data_dict[('nbpgt_cumul', t_exp, t_ens)] # cube
        elif land_carbon == 'tls':
            landc_cumul = data_dict[('tls', t_exp, t_ens)] # dict
        fgco2gt_cumul = data_dict[('fgco2gt_cumul', t_exp, t_ens)] # cube
        fl_threshold = float(threshold)
        if fl_threshold > 1850.:
          
            e_xpoint = get_threshold_point(cumul_emissions, fl_threshold)
            a_xpoint = get_threshold_point(atmos_carbon, fl_threshold)
            n_xpoint = get_threshold_point(landc_cumul, fl_threshold)
            f_xpoint = get_threshold_point(fgco2gt_cumul, fl_threshold)
            if None in [a_xpoint, n_xpoint, f_xpoint]: continue

            emissions.append(cumul_emissions['cumul_emissions'][e_xpoint])
            remnant.append(atmos_carbon['atmos_carbon'][a_xpoint])
            if isinstance(landc_cumul, dict):
                landcs.append(landc_cumul[land_carbon][n_xpoint])
            else:
                landcs.append(landc_cumul.data[n_xpoint])
            fgco2gts.append(fgco2gt_cumul.data[f_xpoint])

            t_exp = t_exp.replace('historical-', '').upper()
            experiments.append(t_exp)
            continue 

        for thresh, time in threshold_times.items():
            #print("make_bar_chart", t_short, t_exp, t_ens, thresh, threshold, time)
            if float(threshold) != float(thresh):
                #print(threshold, '!=', thresh)
                continue
            if not time:
                print('time', 'isn t there' , time)
                continue
            #if threshold != thresh: continue
            #print("make_bar_chart",threshold, time)

            e_xpoint = get_threshold_point(cumul_emissions, time.year)
            a_xpoint = get_threshold_point(atmos_carbon, time.year)

            n_xpoint = get_threshold_point(landc_cumul, time.year)
            f_xpoint = get_threshold_point(fgco2gt_cumul, time.year)

            print("make_bar_chart",thresh, time, 'atmos', e_xpoint, 'land:', n_xpoint, 'ocean', f_xpoint)
            if year0:
                assert 0
                #e_baseline = get_threshold_point(cumul_emissions, year0)
                #n_baseline = get_threshold_point(landc_cumul, year0)
                #f_baseline = get_threshold_point(fgco2gt_cumul, year0)

               # emissions.append(cumul_emissions['cumul_emissions'][e_xpoint] - cumul_emissions['cumul_emissions'][e_baseline])
               # if isinstance(landc_cumul, dict):
               #     landcs.append(landc_cumul[land_carbon][n_xpoint] - landc_cumul[land_carbon][n_baseline])
               #     fgco2gts.append(fgco2gt_cumul.data[f_xpoint] - landc_cumul[land_carbon][f_baseline])
               # else:
               #     landcs.append(landc_cumul.data[n_xpoint] - landc_cumul.data[n_baseline])
               #     fgco2gts.append(fgco2gt_cumul.data[f_xpoint] - landc_cumul.data[f_baseline])
            else:
                print("make_bar_chart",thresh, time,'cumul_emissions', cumul_emissions['cumul_emissions'][e_xpoint], cumul_emissions['time'][e_xpoint])
                print("make_bar_chart",thresh, time,'land:          ', landc_cumul[land_carbon][n_xpoint], landc_cumul['time'][n_xpoint])
                print("make_bar_chart",thresh, time,'ocean:         ', fgco2gt_cumul.data[f_xpoint], fgco2gt_cumul.coord('time').points[f_xpoint])

                emissions.append(cumul_emissions['cumul_emissions'][e_xpoint])
                remnant.append(atmos_carbon['atmos_carbon'][a_xpoint])
                if isinstance(landc_cumul, dict):
                    landcs.append(landc_cumul[land_carbon][n_xpoint])
                else:
                    landcs.append(landc_cumul.data[n_xpoint])
                fgco2gts.append(fgco2gt_cumul.data[f_xpoint])

            t_exp = t_exp.replace('historical-', '').upper()
            experiments.append(t_exp)

    if not len(experiments):
        print("make_bar_chart",emissions, landcs, fgco2gts, remnant,  experiments)
        print("make_bar_chart",thresholds_dict.keys())
        print("make_bar_chart",'looking for:', threshold)
        assert 0

    if fig == None:
        fig, ax = plt.subplots()
        make_figure_here = True
    else:
        make_figure_here = False

    #experiments = [exp.replace('historical-', '').upper() for exp in experiments]
    if atmos=='atmos_carbon':
        emissions_diff = remnant 
    else:
        emissions_diff = [e - f - b for e,f,b in zip(emissions, fgco2gts, landcs )]
    emissions_bottoms = [f + b for f,b in zip(fgco2gts, landcs )]

    totals = [a + f + b for a,f,b in zip(remnant, fgco2gts, landcs )]

    for i, exp  in enumerate(experiments):
        print("Final values:",threshold, i, exp, totals[i], '=',emissions_diff[i],('or', remnant[i]), '+', landcs[i], '+', fgco2gts[i], '(a, l, o)')

    # Add bars:
    horizontal = True
    if horizontal:
        ax.barh(experiments, landcs, label='Land', color='mediumseagreen')
        ax.barh(experiments, fgco2gts, left = landcs,  label='Ocean', color='dodgerblue')
        ax.barh(experiments, emissions_diff, left = emissions_bottoms,  label='Atmos', color='silver')
        ax.set_ylabel('Scenarios')
    else:
        ax.barh(experiments, landcs, label='Land', color='green')
        ax.barh(experiments, fgco2gts, bottom = landcs,  label='Ocean', color='dodgerblue')
        ax.barh(experiments, emissions_diff, bottom = emissions_bottoms,  label='Atmos', color='grey')
        ax.set_xlabel('Scenarios')

    # Add percentages:
    add_pc_text = True
    if add_pc_text:
        def pc(a,b): return str("{0:.1f}%".format(100.*a/b))
        for e, exp in enumerate(experiments):
            print(e, exp, landcs[e], fgco2gts[e], emissions_diff[e])
            t = totals[e]
            ax.text(landcs[e]/2, e, pc(landcs[e], t),
                    color='#002200', #'darkgreen', 
                    fontsize=8 , # fontweight='bold',
                    verticalalignment='center',horizontalalignment='center')

            ax.text(landcs[e]+fgco2gts[e]/2., e, pc(fgco2gts[e], t),
                    color='darkblue', fontsize=8 , # fontweight='bold',
                    verticalalignment='center',horizontalalignment='center')

            ax.text(emissions_bottoms[e]+emissions_diff[e]/2., e, pc(emissions_diff[e],t ),
                    color='black', fontsize=8 , # fontweight='bold',
                    verticalalignment='center',horizontalalignment='center')

    if float(threshold) > 1850.:
        ax.set_title('Carbon Allocation at '+str(threshold))
    else:   
        ax.set_title('Carbon Allocation at '+str(threshold)+r'$\degree$'+' warming')
    
    if do_legend:
        ax.legend()

    if make_figure_here:
        image_extention = diagtools.get_image_format(cfg)
        path = diagtools.folder([cfg['plot_dir'], 'barcharts' ])
        path += '_'.join(['barchart'+str(threshold), land_carbon, atmos]) + image_extention
        plt.savefig(path)
        plt.close()
    else:
        return fig, ax




def zip_time(dict, key):
    """
    zip data_dict style dict into a time:value style dict.
    """
    out_dict = {t:d for t,d in zip(dict['time'], dict[key]) if t < 2100.}
    return out_dict

def unzip_time(dict):
    """
    zip data_dict style dict into a time:value style dict.
    """
    times = np.array(sorted(list(dict.keys())))
    data = np.array([dict[t] for t in times])
    return times, data


def make_cumulative_timeseries(cfg, data_dict,
      thresholds_dict,
      ssp='historical-ssp126',
      ensemble = 'ensemble_mean',
      plot_type = 'simple_ts',
      do_leg= True,
      fig = None, gs = None, ax= None,
):
    """
    Make a plot showing the time series of carbon allocation.
    """
    if None in [fig, ax]:
        fig = plt.figure()
        fig.set_size_inches(12, 6)
        gs = gridspec.GridSpec(1, 1,figure=fig )# width_ratios=[1,1], wspace=0.5, hspace=0.5)
        ax =  fig.add_subplot(gs[0, 0])
        save = True
    else:
        save = False
    plt.sca(ax)

   # load data.
    cube_keys = ['fgco2gt_cumul', 'nbpgt_cumul']
    colours = {'cumul_emissions': 'grey', 'fgco2gt_cumul':'blue', 'nbpgt_cumul':'orange', 'tls':'green', 'luegt':'red'}
    if ensemble == 'ensemble_mean':
        ensembles = ['ensemble_mean',]
    if ssp[:3] == 'ssp':
         exps = ['historical', ssp, '-'.join(['historical', ssp])]
    elif ssp == 'historical': exps = ['historical', ]

    data = {k:{} for k in colours.keys()}
    for ssp_it, ensemble, key in product(exps, ensembles, colours.keys()):
        print('load data', (key, ssp_it, ensemble))
        tmp_data = data_dict.get((key, ssp_it, ensemble), False)
        if not tmp_data: continue
        print('load data: found:', (key, ssp_it, ensemble))
        if key in cube_keys:
            tmp_data = {'time': diagtools.cube_time_to_float(tmp_data),
                             key: tmp_data.data}

        tmp_data = zip_time(tmp_data, key)
        data[key] = data[key] | tmp_data # combine two dicts (python 3.9 and later)
        print('load data', (key, ssp_it, ensemble),':', len(data[key].keys()))

    # add atmos_stock:
    tmp_times, tmp_dat = unzip_time(data['cumul_emissions'])
    for key in ['nbpgt_cumul', 'fgco2gt_cumul']:
        print('adding atmospheric stock', key)
        key_times, key_dat = unzip_time(data[key])
        tmp_dat = tmp_dat - key_dat

    data['atmos'] = zip_time({'time':tmp_times, 'atmos':tmp_dat}, 'atmos')
    colours['atmos'] = 'purple'

    thresholds = {}
    for ssp_it, ensemble in product(exps, ensembles, ):
        dicts = thresholds_dict.get(('tas', ssp_it, ensemble), False)
        if not dicts:continue
        thresholds= thresholds|dicts

    # plot simple time series:
    if plot_type == 'simple_ts':
        for key, dat in data.items():
            times, dat_list = unzip_time(dat)
            #if key == 'tls':
            #    times, dat = times[:-1], dat[:-1]
            plt.plot(times,
                dat_list,
                lw=2,
                color=colours[key],
                label = key)
        plt.axhline(y=0., c='k', ls='--')
        if do_leg: plt.legend()

    # plot simple time series:
    if plot_type == 'sink_source':
        for key, dat in data.items():
            times, dat_list = unzip_time(dat)
            if key in ['fgco2gt_cumul', 'nbpgt_cumul', 'tls', 'luegt']:
                dat_list = -1*dat_list
            plt.plot(times,
                dat_list,
                lw=2,
                color=colours[key],
                label = key)
        plt.axhline(y=0., c='k', ls='--')
        if do_leg: plt.legend()

    # plot simple time series:
    if plot_type == 'area':
        colours = {'cumul_emissions': 'grey', 'fgco2gt_cumul':'blue', 'nbpgt_cumul':'orange', 'tls':'green'}
        emt, emd = unzip_time(data['cumul_emissions'])
        lat, lad = unzip_time(data['tls'])
        ont, ond = unzip_time(data['fgco2gt_cumul'])
        lut, lud = unzip_time(data['luegt'])
        nbt, nbd = unzip_time(data['nbpgt_cumul'])
        att, atd = unzip_time(data['atmos'])

        # water:
        plt.plot(
            ont,
            -1.* ond,
            lw=2,
            color=colours['fgco2gt_cumul'],
            label = 'Air sea Flux of CO2')
        ax.fill_between(# zero and blue line
            ont,
            -1.* ond, # zero and blue line
            lw=2,
            color=colours['fgco2gt_cumul'])

        # land:
        plt.plot(
            lat,
            -1.*(lad + ond),
            lw=2,
            color=colours['tls'],
            label = 'True Land Sink')
        ax.fill_between( # blue line and land line
            lat,
            -1.* ond,
            -1.*(lad + ond),
            lw=2,
            color=colours['tls'])

        # atmos
        plt.plot(
            att,
            atd,
            lw=2,
            color=colours['cumul_emissions'],
            label = 'Atmosphere')
        ax.fill_between( # atmos anthro stock
            att,
            atd,
            lw=2,
            color=colours['cumul_emissions'])
        #ax.set_xlim([2010., 2100])
        ax.set_ylabel('Cumulative carbon, Pg')
        plt.axhline(y=0., c='k', ls='--')

    # plot simple time series:
    if plot_type in ['pc', 'triple'] :
        colours = {'cumul_emissions': 'grey', 'fgco2gt_cumul':'blue', 'nbpgt_cumul':'orange', 'tls':'green'}
        emt, emd = unzip_time(data['cumul_emissions'])
        lat, lad = unzip_time(data['tls'])
        ont, ond = unzip_time(data['fgco2gt_cumul'])
        lut, lud = unzip_time(data['luegt'])
        nbt, nbd = unzip_time(data['nbpgt_cumul'])
        att, atd = unzip_time(data['atmos'])

        total = ond + lad + atd
        water_land_line = (ond/total)*100.
        land_air_line = 100. *(ond + lad)/total

        water_land_line = np.ma.masked_invalid(water_land_line)
        land_air_line = np.ma.masked_invalid(land_air_line)
        # water:
        plt.plot(
            [], [],
            lw=6,
            color=colours['fgco2gt_cumul'],
            label = 'Air sea Flux of CO2')
        ax.fill_between(# zero and blue line
            att,
            0.* water_land_line, # zero and blue line
            water_land_line,
            color=colours['fgco2gt_cumul'])

        # land:
        plt.plot([],[],
            lw=6,
            color=colours['tls'],
            label = 'True Land Sink')
        ax.fill_between( # blue line and land line
            att,
            water_land_line,
            land_air_line,
            lw=2,
            color=colours['tls'])

        # atmos
        plt.plot([], [],
            lw=6,
            color=colours['cumul_emissions'],
            label = 'Atmosphere')
        ax.fill_between( # atmos anthro stock
            att,
            land_air_line,
            land_air_line*0. +100.,
            lw=2,
            color=colours['cumul_emissions'])

        #plt.axhline(y=0., c='k', ls='--')
        ax.set_ylabel('Percentage')
        #x.set_xlim([2010., 2100])
        ax.set_ylim([0., 100.])

    if ssp in ['historical', ]:
        if plot_type in ['pc', ]:
            plt.plot([1959.,1980., 2000., 2012.], [56., 56., 56., 56.,], c='k', ls=':', label = 'Raupach 2014' )
            plt.plot([1959.,1980., 2000., 2012.], [25., 25., 25., 25.,], c='navy', ls='-.', label = 'Watson 2020')

        else:
            plt.plot([], [], c='k', ls=':', label = 'Raupach 2014' )
            plt.plot([], [], c='navy', ls='-.', label = 'Watson 2020'  )

    if do_leg:plt.legend(fontsize='small')


    print(thresholds)
    for thres, dt in thresholds.items():
        if dt is None: continue
        print('adding threshold lineL', thres, dt)

        plt.axvline(x=float(dt.year)+0.5, c='k', ls=':' )
        x = float(dt.year) +0.02 *(np.max(ax.get_xlim()) - np.min(ax.get_xlim()))
        y = np.max(ax.get_ylim())- 0.11 * (np.max(ax.get_ylim()) - np.min(ax.get_ylim()))

        plt.text(x, y, 'GWT: ' +str(thres), ha='right', va='top', rotation=90) #fontsize=8, fontweight='bold',rotation=90)

    plt.title(ssp_title_dict.get(ssp, None))

    # save plot.
    if save:
        image_extention = diagtools.get_image_format(cfg)
        path = diagtools.folder([cfg['plot_dir'], 'allocation_timeseries'])
        path += '_'.join(['allocation_timeseries', plot_type, ssp]) + image_extention
        print('saving figure:', path)
        plt.savefig(path)
        plt.close()
    else: return fig, ax



def make_cumulative_timeseries_pair(cfg, data_dict,
      thresholds_dict,
      ssp='ssp126',
      ensemble = 'ensemble_mean',
    ):

    fig = plt.figure()
    fig.set_size_inches(6, 6)
    gs = gridspec.GridSpec(2, 1,figure=fig )# width_ratios=[1,1], wspace=0.5, hspace=0.5)
    ax1 =  fig.add_subplot(gs[0, 0])
    ax2 =  fig.add_subplot(gs[1, 0])

    fig, ax1 = make_cumulative_timeseries(cfg, data_dict,
        thresholds_dict,
        ssp=ssp,
        ensemble = ensemble,
        plot_type = 'area',
        fig = fig, ax= ax1,
    )
    fig, ax2 = make_cumulative_timeseries(cfg, data_dict,
        thresholds_dict,
        ssp=ssp,
        ensemble = ensemble,
        plot_type = 'pc',
        fig = fig, ax= ax2,
        do_leg=False,
    )
    if ssp == 'historical':
        ax1.set_xlim([1850., 2015.])
        ax2.set_xlim([1850., 2015.])
#        ax1.tick_params(labeltop=False, labelright=True)
#        ax2.tick_params(labeltop=False, labelright=True)

    else:
        ax1.set_xlim([2015., 2100.])
        ax2.set_xlim([2015., 2100.])
#        ax1.tick_params(labeltop=False, labelright=True)
#        ax2.tick_params(labeltop=False, labelright=True)
    ax1.grid(axis='y')
    ax2.grid(axis='y')

    image_extention = diagtools.get_image_format(cfg)
    path = diagtools.folder([cfg['plot_dir'], 'allocation_timeseries'])
    path += '_'.join(['allocation_timeseries', 'pair', ssp]) + image_extention
    print('saving figure:', path)
    plt.savefig(path)
    plt.close()




def make_cumulative_vs_threshold(cfg, data_dict, thresholds_dict, land_carbon = 'nbpgt',
    LHS_panes = [{'x':'cumul_emissions', 'y':'tas_norm'}, ],
    thresholds = ['4.0', '3.0', '2.0'],
):
    """
    Make a specific kind of figure.
    land_carbon can be nbpgt or tls
    """
    #standalone
    #for threshold in thresholds:
    #    make_bar_chart(cfg, data_dict, thresholds_dict, threshold = threshold, land_carbon = land_carbon, fig=None, ax=None)

    fig = plt.figure()
    if len(LHS_panes) ==0:
        fig.set_size_inches(7 , 6)
        gs = gridspec.GridSpec(3, 1, figure=fig, hspace=0.5)
    else:
        fig.set_size_inches(12, 6)
        gs = gridspec.GridSpec(3, 2,figure=fig, width_ratios=[1,1], wspace=0.5, hspace=0.5)

    # Get a big line graph on LHS.
    if len(LHS_panes) ==1:
        ax_ts =  fig.add_subplot(gs[:, 0])

        fig, ax = make_ts_figure(cfg, data_dict, thresholds_dict,
            x=LHS_panes[0]['x'],
            y=LHS_panes[0]['y'],
            markers='thresholds',
            draw_line=True,
            do_moving_average=False,
            ensemble_mean = True,
            fig=fig,
            ax = ax_ts,)

    if len(LHS_panes) ==3:
        for i, LHS_pane in enumerate(LHS_panes):
            ax_ts =  fig.add_subplot(gs[i, 0])
            fig, ax = make_ts_figure(cfg, data_dict, thresholds_dict,
                x=LHS_pane['x'],
                y=LHS_pane['y'],
                markers='thresholds',
                draw_line=True,
                do_moving_average=False,
                ensemble_mean = True,
                fig=fig,
                ax = ax_ts,)
            if LHS_pane['x'] == 'time':
                ax_ts.set_xlim([2000., 2100.])

    if len(LHS_panes) ==0:
        ax_4 =  fig.add_subplot(gs[0, 0])
        ax_3 =  fig.add_subplot(gs[1, 0])
        ax_2 =  fig.add_subplot(gs[2, 0])
    else:
        ax_4 =  fig.add_subplot(gs[0, 1])
        ax_3 =  fig.add_subplot(gs[1, 1])
        ax_2 =  fig.add_subplot(gs[2, 1])

    axes = [ax_4, ax_3, ax_2]
    # make_bar_chart(cfg, data_dict, thresholds_dict, threshold = '1.5', fig=None, ax=None)
    for ax, threshold in zip(axes, thresholds):
        make_bar_chart(cfg, data_dict, thresholds_dict, threshold = threshold, land_carbon = land_carbon,fig=fig, ax=ax, do_legend=False)

    ranges = []
    for ax in [ax_4, ax_3, ax_2]:
        plt.sca(ax)
        ranges.append(ax.get_xlim())

    for ax in [ax_4, ax_3, ax_2]:
        plt.sca(ax)
        ax.set_xlim([np.min(ranges), np.max(ranges)])

    image_extention = diagtools.get_image_format(cfg)
    path = diagtools.folder([cfg['plot_dir'], 'emissions_figures'])
    path += '_'.join(['emssions_figure', land_carbon, str(len(LHS_panes)), '_'.join(thresholds)]) + image_extention
    plt.savefig(path)
    plt.close()
    # 4 degree threshold data.
    # for each scenario at 4 degrees, we want:
    # total emissions at time_4
    # nbp and



def main(cfg):
    """
    Load the config file and some metadata, then make plots.

    Parameters
    ----------
    cfg: dict
        the opened global config dictionairy, passed by ESMValTool.

    """
    #jobss for tomoorrow:
    #    check to make sure that you're using the 'right areacella for land.
    #    do you even need the areacella for air? probably not, right?
    #    change the recipe to add the other ensemble members to the job.
    #    email the figues to other authors.



    # short_names = ['tas', 'tas_norm', 'nppgt', 'fgco2gt', 'rhgt', 'exchange']
    # short_names_x = ['time','tas', 'tas_norm','nppgt', 'fgco2gt', 'rhgt', 'exchange']
    # short_names_y = ['tas', 'tas_norm', 'nppgt',  'fgco2gt', 'rhgt', 'exchange']

    #jobtype = 'land'
    short_names, short_names_x, short_names_y = [], [], []
    #jobtype = 'debug'
    #jobtype = 'bulk'
    jobtype = 'cumulative_plot'

    if jobtype == 'cumulative_plot':
        short_names = ['tas', 'tas_norm',
                       'co2', 'emissions', 'cumul_emissions'
                       'nbp', 'nbpgt', 'nbpgt_cumul',
                       #'gpp', 'gppgt',
                       #'intpp',  'intppgt',
                       'fgco2','fgco2gt', 'fgco2gt_cumul',
                       'luegt', #  land-use emissions gt
                       'tls', #true land sink = nbp + land-use emissions
                       'atmos_carbon', # remant antrho carbon in atmosphere                       
                       ]

        short_names_x = short_names.copy()
        short_names_x.append('time')
        short_names_y = short_names.copy()

    if jobtype == 'marine':
        short_names = ['tas', 'tas_norm', 'co2',
                       'npp', 'nppgt', 'rh', 'rhgt', 'exchange',
                       #'inverse_exchange',
                       #'nppgt_norm','rhgt_norm','exchange_norm','fgco2gt_norm', 'intppgt_norm',
                       'intpp', 'fgco2', 'epc100', 'intdic', 'intpoc', 'fric', 'froc', 'frc',
                       'intppgt','fgco2gt', 'epc100gt', 'intdicgt', 'intpocgt', 'fricgt', 'frocgt','frcgt',
                       ]
        short_names_x = ['time', 'co2', 'tas_norm', 'fgco2gt', 'intdicgt']
        #'intpp', 'epc100', 'intdic', 'intpoc', 'fric', 'froc'] #'nppgt', 'fgco2gt', 'rhgt', 'exchange']
        #short_names_y = ['nppgt', 'nppgt_norm','rhgt_norm','exchange_norm','fgco2gt_norm', 'co2',]
        short_names_y = ['tas_norm', 'co2', 'intpp', 'fgco2', 'epc100', 'intdic', 'intpoc', 'fric', 'froc','frc', 'fgco2gt', 'intppgt','epc100gt', 'intdicgt', 'intpocgt', 'fricgt', 'frocgt', 'frcgt',]


    if jobtype == 'bulk':
        short_names = ['tas', 'tas_norm', 'co2', 'emissions', 'cumul_emissions'
                       'nbp', 'nbpgt', 'gpp', 'gppgt',
                       'intpp', 'fgco2', 'intppgt','fgco2gt',
                       'fgco2gt_cumul',
                       'nbpgt_cumul'
                       'luegt', #  land-use emissions gt
                       'tls', #true land sink = nbp + land-use emissions
                       'atmos_carbon', # remant antrho carbon in atmosphere
                       ]
        short_names_x = ['time', 'co2', 'tas', 'emissions','cumul_emissions', 'tas_norm', 'fgco2gt', 'nbpgt', 'fgco2gt_cumul','nbpgt_cumul', 'tls', 'luegt']
        short_names_y = short_names.copy()

    if jobtype == 'debug':
        short_names = [
                       'emissions', 'cumul_emissions',
                       #'tas',
                       #'nbp', 'nbpgt', 'nbpgt_cumul',
                       #'gpp', 'gppgt',
                       #'fgco2','fgco2gt', 'fgco2gt_cumul',
                       #'bgc', 'bgcgt',
                       #'luegt', #  land-use emissions gt
                       'tls', #true land sink = nbp + land-use emissions
                       #'atmos_carbon', # remant antrho carbon in atmosphere

                       ]
        short_names_x = ['cumul_emissions',]#'time', 'emissions', 'cumul_emissions',]#'cumul_emissions', 'gppgt'] #'time', ]#'co2', 'emissions', 'tas_norm', 'fgco2gt', 'nbpgt']
        short_names_y = ['tls', ]#s #['fgco2gt_cumul', 'nbpgt_cumul']


    if jobtype == 'land':
        short_names = ['tas', 'tas_norm', 'co2',
                       'npp', 'nppgt',
                       'rhgt', 'exchange',
                       'nppgt_norm','rhgt_norm','exchange_norm',
                       ]
        short_names_x = ['time', 'co2', 'tas', 'tas_norm',
                         'rhgt', 'exchange', 'rhgt','nppgt',]
        short_names_y = ['tas', 'tas_norm', 'co2',
                         'npp', 'nppgt',
                         'rh', 'rhgt',
                         'exchange',
                         'nppgt_norm','rhgt_norm','exchange_norm',]


    if jobtype == 'full':
        short_names = ['tas', 'tas_norm', 'co2',
                       'npp', 'nppgt', 'rhgt', 'exchange',
                       'nppgt_norm','rhgt_norm','exchange_norm','fgco2gt_norm', 'intppgt_norm',
                       'intpp', 'fgco2', 'epc100', 'intdic', 'intpoc', 'fric', 'froc',
                       'intppgt','fgco2gt', 'epc100gt', 'intdicgt', 'intpocgt', 'fricgt', 'frocgt',
                       ]
        short_names_x = ['time', 'co2', 'tas', 'tas_norm', 'fgco2gt',
                         'intpp', 'epc100', 'intdic', 'intpoc', 'fric', 'froc', 'nppgt', 'fgco2gt', 'rhgt', 'exchange']
        short_names_y = ['nppgt', 'nppgt_norm','rhgt_norm','exchange_norm','fgco2gt_norm', 'co2',
                         'intpp', 'fgco2', 'epc100', 'intdic', 'intpoc', 'fric', 'froc', 'fgco2gt', 'intppgt','epc100gt', 'intdicgt', 'intpocgt', 'fricgt', 'frocgt',]

     # ]'npp', 'nppgt', 'intpp', 'intppgt_norm', 'fgco2gt', 'rhgt', 'exchange', 'nppgt_norm','rhgt_norm','exchange_norm','fgco2gt_norm']

    pairs = []

    for do_ma in [True, ]:#False]:
        data_dict = load_timeseries(cfg, short_names)
        thresholds_dict = load_thresholds(cfg, data_dict)

        if jobtype == 'cumulative_plot':
            #make_cumulative_vs_threshold(cfg, data_dict, thresholds_dict, land_carbon = 'tls')
            #make_cumulative_vs_threshold(cfg, data_dict, thresholds_dict, land_carbon = 'nbpgt')
            #make_cumulative_timeseries(cfg, data_dict, thresholds_dict, ssp='historical-ssp585',)
            #make_cumulative_timeseries(cfg, data_dict, thresholds_dict, ssp='historical',)
            make_cumulative_vs_threshold(cfg, data_dict, thresholds_dict, land_carbon = 'tls', LHS_panes = {})
            make_cumulative_vs_threshold(cfg, data_dict, thresholds_dict, land_carbon = 'tls', LHS_panes = {}, thresholds=['2075', '2050', '2025'])

            assert 0
            plot_types = ['pair', 'pc', 'simple_ts', 'area', ]
            ssps = ['historical', 'ssp119', 'ssp126', 'ssp245', 'ssp370', 'ssp585']
            for pt, exp in product(plot_types, ssps):

                if pt == 'pair':
                   make_cumulative_timeseries_pair(cfg, data_dict,
                       thresholds_dict,
                       ssp=exp,
                       ensemble = 'ensemble_mean')
                   continue
                make_cumulative_timeseries(cfg, data_dict, thresholds_dict, ssp=exp, plot_type = pt)
            make_cumulative_vs_threshold(cfg, data_dict, thresholds_dict, land_carbon = 'tls', LHS_panes = {})
            assert 0

            LHS_panes = [
                {'x':'time', 'y':'cumul_emissions'},
                {'x':'time', 'y':'tls'},
                {'x':'time', 'y':'fgco2gt_cumul'},
                #{'x':'time', 'y':'tas_norm'},
                #{'x':'time', 'y':'cumul_emissions'},
                #{'x':'cumul_emissions', 'y':'tas_norm'},
            ]
            make_cumulative_vs_threshold(cfg, data_dict, thresholds_dict, land_carbon = 'tls', LHS_panes = LHS_panes)

        for (short_name, exp, ensemble),cube  in sorted(data_dict.items()):
            if do_ma and short_name not in ['co2', 'emissions', 'cumul_emissions', 'luegt', 'tls', 'atmos_carbon']:
                data_dict[(short_name, exp, ensemble)] = moving_average(cube, '21 years')

        print(short_names)

        for x in short_names_x:
            for y in short_names_y:
                if x == y: continue
                if (x, y) in pairs: continue
                print('main:', do_ma, x, y)
                make_ts_figure(cfg, data_dict, thresholds_dict, x=x, y=y,
                               markers='thresholds', do_moving_average=False,
                               ensemble_mean=True)
                if jobtype == 'debug': continue
                make_ts_figure(cfg, data_dict, thresholds_dict, x=x, y=y,
                               markers='thresholds', do_moving_average=True,
                               ensemble_mean=False)
                pairs.append((x, y))
                #pairs.append((y, x))

    logger.info('Success')



if __name__ == '__main__':
    with run_diagnostic() as config:
        main(config)