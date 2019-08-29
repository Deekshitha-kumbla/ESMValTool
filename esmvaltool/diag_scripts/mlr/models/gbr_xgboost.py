"""Gradient Boosting Regression model (using :mod:`xgboost´)."""

import logging
import os

from xgboost import XGBRegressor

from esmvaltool.diag_scripts.mlr.models import MLRModel
from esmvaltool.diag_scripts.mlr.models.gbr import GBRModel

logger = logging.getLogger(os.path.basename(__file__))


@MLRModel.register_mlr_model('gbr_xgboost')
class XGBoostGBRModel(GBRModel):
    """Gradient Boosting Regression model (:mod:`xgboost` implementation).

    Note
    ----
    See :mod:`esmvaltool.diag_scripts.mlr.models`.

    """

    _CLF_TYPE = XGBRegressor

    def plot_training_progress(self, filename=None):
        """Plot training progress for training and (if possible) test data.

        Parameters
        ----------
        filename : str, optional (default: 'training_progress')
            Name of the plot file.

        """
        clf = self._clf.steps[-1][1].regressor_
        evals_result = clf.evals_result()
        train_score = evals_result['validation_0']['rmse']
        test_score = None
        if 'test' in self.data:
            test_score = evals_result['validation_1']['rmse']
        self._plot_training_progress(train_score, test_score, filename)

    def _update_fit_kwargs(self, fit_kwargs):
        """Add transformed training and test data as fit kwargs."""
        fit_kwargs = super()._update_fit_kwargs(fit_kwargs)
        final_step = f'{self._clf.steps[-1][0]}__'
        target_transformer_fit_kwargs = {}
        for (param_name, param_val) in fit_kwargs.items():
            if not param_name.startswith(final_step):
                continue
            target_transformer_fit_kwargs[param_name.replace(final_step,
                                                             '')] = param_val
        x_train = self.get_x_array('train')
        y_train = self.get_y_array('train')
        self._clf.fit_transformers_only(x_train, y_train, **fit_kwargs)
        self._clf.steps[-1][1].fit_transformer_only(
            y_train, **target_transformer_fit_kwargs)

        # Transform input data
        x_train = self._clf.transform_only(x_train)
        y_train = self._clf.transform_target_only(y_train)
        eval_set = [(x_train, y_train)]
        sample_weights = [self._get_sample_weights('train')]
        if 'test' in self.data:
            x_test = self._clf.transform_only(self.get_x_array('test'))
            y_test = self._clf.transform_target_only(self.get_y_array('test'))
            eval_set.append((x_test, y_test))
            sample_weights.append(self._get_sample_weights('test'))
        if self._get_sample_weights('all') is None:
            sample_weights = None

        # Update kwargs
        fit_kwargs.update({
            f'{self._clf.steps[-1][0]}__regressor__eval_metric':
            'rmse',
            f'{self._clf.steps[-1][0]}__regressor__eval_set':
            eval_set,
            f'{self._clf.steps[-1][0]}__regressor__sample_weight_eval_set':
            sample_weights,
        })
        logger.debug(
            "Updated keyword arguments of final regressor's fit() function "
            "with training and (if possible) test datasets for evaluation of "
            "prediction errors")
        return fit_kwargs
