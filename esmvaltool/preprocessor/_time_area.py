"""Time operations on cubes

Allows for selecting data subsets using certain time bounds;
constructing seasonal and area averages.
"""
import iris
import iris.coord_categorisation
import numpy as np


def time_slice(cube, start_year, start_month, start_day, end_year, end_month,
               end_day):
    """Slice cube on time.

    Parameters
    ----------
        cube: iris.cube.Cube
            input cube.
        start_year: int
            start year
        start_month: int
            start month
        start_day: int
            start day
        end_year: int
            end year
        end_month: int
            end month
        end_day: int
            end day

    Returns
    -------
    iris.cube.Cube
        Sliced cube.

    """
    import datetime
    time_units = cube.coord('time').units
    if time_units.calendar == '360_day':
        if start_day > 30:
            start_day = 30
        if end_day > 30:
            end_day = 30
    start_date = datetime.datetime(
        int(start_year), int(start_month), int(start_day))
    end_date = datetime.datetime(int(end_year), int(end_month), int(end_day))

    t_1 = time_units.date2num(start_date)
    t_2 = time_units.date2num(end_date)
    # TODO replace the block below for when using iris 2.0
    # my_constraint = iris.Constraint(time=lambda t: (
    #     t_1 < time_units.date2num(t.point) < t_2))
    my_constraint = iris.Constraint(time=lambda t: (t_1 < t.point < t_2))
    return cube.extract(my_constraint)


def extract_season(cube, season):
    """
    Slice cube to get only the data belonging to a specific season.

    Parameters
    ----------
    cube: iris.cube.Cube
        Original data
    season: str
        Season to extract. Available: DJF, MAM, JJA, SON
    """
    iris.coord_categorisation.add_season(cube, 'time', name='clim_season')
    return cube.extract(iris.Constraint(clim_season=season.lower()))


def extract_month(cube, month):
    """
    Slice cube to get only the data belonging to a specific month.

    Parameters
    ----------
    cube: iris.cube.Cube
        Original data
    month: int
        Month to extract as a number from 1 to 12
    """
    return cube.extract(iris.Constraint(month_number=month))


def time_average(cube):
    """
    Compute time average.

    Get the time average over the entire cube. The average is weighted by the
    bounds of the time coordinate.

    Parameters
    ----------
        cube: iris.cube.Cube
            input cube.

    Returns
    -------
    iris.cube.Cube
        time averaged cube.
    """
    time = cube.coord('time')
    time_thickness = time.bounds[..., 1] - time.bounds[..., 0]

    # The weights need to match the dimensionality of the cube.
    slices = [None for i in cube.shape]
    coord_dim = cube.coord_dims('time')[0]
    slices[coord_dim] = slice(None)
    time_thickness = np.abs(time_thickness[tuple(slices)])
    ones = np.ones_like(cube.data)
    time_weights = time_thickness * ones

    return cube.collapsed('time', iris.analysis.MEAN, weights=time_weights)


# get the seasonal mean
def seasonal_mean(cube):
    """
    Function to compute seasonal means with MEAN

    Chunks time in 3-month periods and computes means over them;

    Arguments
    ---------
        cube: iris.cube.Cube
            input cube.

    Returns
    -------
    iris.cube.Cube
        Seasonal mean cube
    """
    iris.coord_categorisation.add_season(cube, 'time', name='clim_season')
    iris.coord_categorisation.add_season_year(cube, 'time', name='season_year')
    cube = cube.aggregated_by(['clim_season', 'season_year'],
                              iris.analysis.MEAN)

    def spans_three_months(time):
        """Check for three months"""
        return (time.bound[1] - time.bound[0]) == 2160

    three_months_bound = iris.Constraint(time=spans_three_months)
    return cube.extract(three_months_bound)
