import iris
import iris.analysis.cartography
import iris.coord_categorisation
import numpy as np


def stats(x):
    if isinstance(x, int):
        print('single number, you numpty. Max/min/mean = ', x)
    elif isinstance(x, str):
        print('cant stat a string!')
    elif isinstance(x, list) or isinstance(x, np.ndarray):
        print('max = ', np.max(x))
        print('min = ', np.min(x))
        print('mean = ', np.mean(x))
        print('st-dev = ', np.std(x))
    else:
        print('what have you given me??')


def months_appended(x):
    mn2 = x.coord("time")
    m2 = mn2.units.num2date(mn2.points)
    m3 = len(m2)
    time = np.linspace(0, m3, m3)

    return time


def load_cubelist_to_cube(filename=None, load_file=True, cube=None):
    if load_file:
        cube = iris.load(filename)
    if isinstance(cube, iris.cube.CubeList):
        for ijk in np.arange(0, int(len(cube))):
            for key in list(cube[ijk].attributes.keys()):
                del cube[ijk].attributes[key]
            if ijk > 0:
                cube[ijk].coord("time").convert_units(
                    cube[0].coord("time").units)
    cube = cube.concatenate_cube()

    return cube


def area_avg(x, return_cube=None):
    # If the cube does not have bounds, add bounds
    if not x.coord('latitude').has_bounds():
        x.coord('latitude').guess_bounds()
    if not x.coord('longitude').has_bounds():
        x.coord('longitude').guess_bounds()
    # Get the area weights using the same cube
    area = iris.analysis.cartography.area_weights(x, normalize=False)
    # Now collapse the lat and lon to find a global mean over time
    x2 = x.collapsed(['latitude', 'longitude'],
                     iris.analysis.MEAN,
                     weights=area)

    if return_cube:
        return x2
    else:
        return x2.data
