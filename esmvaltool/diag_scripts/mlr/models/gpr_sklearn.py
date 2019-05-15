"""Gaussian Process Regression model (using :mod:`sklearn`)."""

import logging
import os

from sklearn.gaussian_process import GaussianProcessRegressor

from esmvaltool.diag_scripts.mlr.models import MLRModel

logger = logging.getLogger(os.path.basename(__file__))


@MLRModel.register_mlr_model('gpr_sklearn')
class SklearnGPRModel(MLRModel):
    """Gaussian Process Regression model (:mod:`sklearn` implementation).

    Note
    ----
    See :mod:`esmvaltool.diag_scripts.mlr.models`.

    """

    _CLF_TYPE = GaussianProcessRegressor

    def print_kernel_info(self):
        """Print information of the fitted kernel of the GPR model."""
        if not self._is_fitted():
            logger.error("Printing kernel not possible because the model is "
                         "not fitted yet, call fit() first")
            return
        kernel = self._clf.steps[-1][1].regressor_.kernel_
        logger.info("Fitted kernel: %s", kernel)
        logger.info("All fitted log-hyperparameters:")
        for (idx, hyper_param) in enumerate(kernel.hyperparameters):
            logger.info("%s: %s", hyper_param, kernel.theta[idx])
