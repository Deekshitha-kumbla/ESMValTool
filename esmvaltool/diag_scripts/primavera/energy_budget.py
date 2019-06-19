import os
import logging
import calendar

import numpy as np
import matplotlib
matplotlib.use('Agg')  # noqa
import matplotlib.pyplot as plt
from matplotlib import colors
import matplotlib.font_manager
from matplotlib.offsetbox import TextArea, VPacker, AnnotationBbox

from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont

from itertools import cycle

import iris
import iris.cube
import iris.analysis
import iris.util
from iris.analysis import SUM
from iris.coords import AuxCoord
import iris.coord_categorisation
from iris.cube import CubeList
import iris.quickplot as qplt

import esmvaltool.diag_scripts.shared
import esmvaltool.diag_scripts.shared.names as n

logger = logging.getLogger(os.path.basename(__file__))

class EnergyBudget(object):
    def __init__(self, config):
        self.cfg = config
        self.filenames = esmvaltool.diag_scripts.shared.Datasets(self.cfg)
        self.template = self.cfg.get('plot_template')

    def compute(self):
        print(self.cfg)
        data = self.load()
        for dataset in data['rsdt'].keys():
            #shortwave
            data['up_sw_reflected_surf'][dataset] = data['rsds'][dataset] - data['rsns'][dataset]
            data['up_sw_reflected_surf'][dataset].long_name = 'Upward Shortwave Reflected Surface'

            data['sw_refl_clouds'][dataset] =data['rsut'][dataset] - data['up_sw_reflected_surf'][dataset]
            data['sw_refl_clouds'][dataset].long_name = 'Shortwave Reflected Clouds'

            data['sw_abs_atm'][dataset] = data['rsdt'][dataset] - data['sw_refl_clouds'][dataset] - data['rsds'][dataset]
            data['sw_abs_atm'][dataset].long_name = 'Shortwave Absorbed Atmosphere'

            #longwave
            data['up_lw_emitted_surf'][dataset] = data['rlds'][dataset] - data['rlns'][dataset]
            data['up_lw_emitted_surf'][dataset].long_name = 'Upward Longwave Emitted Surface'

            #net
            data['net_surf_rad'][dataset] = data['rsns'][dataset] + data['rlns'][dataset]
            data['net_surf_rad'][dataset].long_name = 'Net Surface Radiation'

            #surface fluxes
            data['rad_adsorbed_surface'][dataset] = data['net_surf_rad'][dataset] - data['hfss'][dataset] - data['hfls'][dataset]
            data['rad_adsorbed_surface'][dataset].long_name = 'Radiation Adsorbed Surface'

            data['rad_net_toa'][dataset] = data['rsdt'][dataset] - data['rsut'][dataset] - data['rlut'][dataset]
            data['rad_net_toa'][dataset].long_name = 'Radiation Net TOA'

            data['bowen_ratio'][dataset]= data['hfss'][dataset] / data['hfls'][dataset]
            data['bowen_ratio'][dataset].long_name = 'Bowen Ratio'
        self.plot(data)

    def load(self):
        data = {}
        data['rsdt'] = {}
        data['rsut'] = {}
        data['rsds'] = {}
        data['rsns'] = {}
        data['rlut'] = {}
        data['rlds'] = {}
        data['rlns'] = {}
        data['hfss'] = {}
        data['hfls'] = {}
        data['up_sw_reflected_surf'] = {}
        data['sw_refl_clouds'] = {}
        data['sw_abs_atm'] = {}
        data['up_lw_emitted_surf'] = {}
        data['net_surf_rad'] = {}
        data['rad_adsorbed_surface'] = {}
        data['rad_net_toa'] = {}
        data['bowen_ratio'] = {}
        for filename in self.filenames:
            dataset = self.filenames.get_info(n.DATASET, filename)
            short_name = self.filenames.get_info(n.SHORT_NAME, filename)
            cube = iris.load_cube(filename)
            data[short_name][dataset] = cube

        return data

    def plot(self, data):
        fig, ax = plt.subplots()
        print(self.template)
        img = Image.open(self.template).convert("RGBA")
        draw = ImageDraw.Draw(img)
        plt.imshow(img)
        pos = {}
        pos['rsdt'] = (511,170)
        pos['rsut'] = (15, 176)
        pos['rsds'] = (None, None)
        pos['rsns'] = (349, 632)
        pos['rlut'] = (916, 130)
        pos['rlds'] = (1052, 833)
        pos['rlns'] = (None, None)
        pos['hfss'] = (524, 632)
        pos['hfls'] = (656, 632)
        pos['up_sw_reflected_surf'] = (105, 632)
        pos['sw_refl_clouds'] = (300, 361)
        pos['sw_abs_atm'] = (538, 635)
        pos['up_lw_emitted_surf'] = (825, 833)
        pos['net_surf_rad'] = (None, None)
        pos['rad_adsorbed_surface'] = (None, None)
        pos['rad_net_toa'] = (350, 62)
        pos['bowen_ratio'] = (None, None)
        for i, var in enumerate(data):
            if None in pos[var]:
                continue
            text = []
            for dataset in data['rsdt'].keys():
                text.append(TextArea(('{:}: {:.2f}'.format(dataset, data[var][dataset].data)), textprops=dict(size=4)))
            texts_vbox = VPacker(children=text,pad=0,sep=1)
            ann = AnnotationBbox(texts_vbox, pos[var], xycoords=ax.transData, box_alignment=(0,.5),bboxprops = dict(facecolor='white',boxstyle='round',color='white',alpha=0.9))
            ax.add_artist(ann)
            plt.axis('off')
        print('Saving')
        fig.savefig('/home/users/sloosvel/output.png', format='png', dpi=1000)
