#!/usr/bin/env python

"""
;#############################################################################
; diagnostic selector
; Author: Benjamin Mueller (LMU Munich, GER)
; Multiple projects
;#############################################################################
; Description
;    Produces various diagnostics based on model data and a reference data set for:
;    - soil moisture
;
; Required diag_script_info attributes (diagnostics specific)
;    request: list of names of requested sub-diagnostics
;              (minimal: None; returns all available request names)
;
; Optional diag_script_info attributes (diagnostic specific)
;    none
;
; Required variable_info attributes (variable specific)
;    none
;
; Optional variable_info attributes (variable specific)
;    none
;
; Caveats
;
; Modification history
;    20190603-A_muel_bn: port to version 2
;
;#############################################################################
"""
# Basic Python packages
import logging
import os

from esmvaltool.diag_scripts.shared import run_diagnostic
from pprint import pprint
from auxiliary.collection_basic import ecv_handler

logger = logging.getLogger(os.path.basename(__file__))


def main(cfg):
    logger.info('>>>>>>>> diagnostic selector is running! <<<<<<<<<<<<')
    
#    logger.info([(ci["dataset"],ci["reference_dataset"]) for _,ci in cfg['input_data'].items()])

    logger.info("Preparing diagnostic")
    Diag = ecv_handler()
    Diag.set_info(cfg = cfg)
    
    logger.info("Reading data")
    Diag.read()
    logger.info("Running diagnostic")
    Diag.run()
    
    logger.info("Thank you for the patiance!")

    logger.info('>>>>>>>> ENDED SUCCESSFULLY!! <<<<<<<<<<<<')


if __name__ == '__main__':

    with run_diagnostic() as config:
        main(config)
