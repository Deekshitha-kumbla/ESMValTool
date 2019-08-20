"""Base class for MLR models."""

import importlib
import logging
import os
from copy import deepcopy
from functools import partial
from inspect import getfullargspec
from pprint import pformat

import iris
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pathos.multiprocessing as mp
import seaborn as sns
from cf_units import Unit
from lime.lime_tabular import LimeTabularExplainer
from skater.core.explanations import Interpretation
from skater.model import InMemoryModel
from sklearn import metrics
from sklearn.compose import ColumnTransformer
from sklearn.decomposition import PCA
from sklearn.exceptions import NotFittedError
from sklearn.impute import SimpleImputer
from sklearn.inspection import plot_partial_dependence
from sklearn.model_selection import (GridSearchCV, LeaveOneOut,
                                     cross_val_score, train_test_split)
from sklearn.preprocessing import StandardScaler

from esmvaltool.diag_scripts import mlr
from esmvaltool.diag_scripts.shared import group_metadata, io, select_metadata

logger = logging.getLogger(os.path.basename(__file__))


class MLRModel():
    """Base class for MLR models.

    Note
    ----
    All datasets must have the attribute `var_type` which specifies this
    dataset. Possible values are `feature` (independent variables used for
    training/testing), `label` (dependent variables, y-axis) or
    `prediction_input` (independent variables used for prediction of dependent
    variables, usually observational data). All datasets can be converted to
    new units in the loading step by specifying the key `convert_units_to` in
    the respective dataset(s).

    Training data
    -------------
    All groups (specified in `group_datasets_by_attributes`, if desired) given
    for `label` must also be given for the `feature` datasets. Within these
    groups, all `feature` and `label` datasets must have the same shape, except
    the attribute `broadcast_from` is set to a list of suitable coordinate
    indices (must be done for each feature/label).

    Prediction data
    ---------------
    All `tags` specified for `prediction_input` datasets must also be given for
    the `feature` datasets (except `allow_missing_features` is set to `True`).
    Multiple predictions can be specified by `prediction_name`. Within these
    predictions, all `prediction_input` datasets must have the same shape,
    except the attribute `broadcast_from` is given. Errors in the prediction
    input data can be specified by `prediction_input_error`. If given, these
    errors are used to calculate errors in the final prediction using linear
    error propagation given by LIME. Additionally, "true" values for
    `prediction_input` can be specified with `prediction_reference` datasets
    (together with the respective `prediction_name`). This allows an evaluation
    of the performance of the MLR model by calculating residuals (predicted
    minus true values).

    Adding new MLR models
    ---------------------
    MLR models are subclasses of this base class. To add a new one, create a
    new file in :mod:`esmvaltool.diag_scripts.mlr.models` with a child class
    of this class decorated by the method `register_mlr_model`.

    Configuration options in recipe
    -------------------------------
    accept_only_scalar_data : bool, optional (default: False)
        Only accept scalar diagnostic data, if set to True
        'group_datasets_by_attributes should be given.
    allow_missing_features : bool, optional (default: False)
        Allow missing features in the training data.
    cache_intermediate_results : bool, optional (default: True)
        Cache the intermediate results of the pipeline's transformers.
    coords_as_features : list, optional
        If given, specify a list of coordinates which should be used as
        features.
    dtype : str, optional (default: 'float64')
        Internal data type which is used for all calculations, see
        <https://docs.scipy.org/doc/numpy/user/basics.types.html> for a list
        of allowed values.
    estimate_mlr_model_error : dict, optional
        Estimate (constant) squared MLR model error using RMSE (or different
        measured if desired). This error represents the uncertainty of the
        prediction caused by the MLR model itself and not by errors in the
        prediction input data (errors in that will be automatically considered
        by including datasets with `var_type` `'prediction_input_error'`. It
        is calculated by a (holdout) test data set (`type: test`) or by cross-
        validation from the training data (`type: cv'). The latter uses
        :mod:`sklearn.model_selection.cross_val_score` (see
        <https://scikit-learn.org/stable/modules/cross_validation.html>),
        additional keyword arguments can be passed via the `kwargs` key.
    fit_kwargs : dict, optional
        Optional keyword arguments for the pipeline's `fit()` function. These
        arguments have to be given for each step of the pipeline seperated by
        two underscores, i.e. `s__p` is the parameter `p` for step `s`.
    grid_search_cv_kwargs : dict, optional
        Keyword arguments for the grid search cross-validation, see
        <https://scikit-learn.org/stable/modules/generated/
        sklearn.model_selection.GridSearchCV.html>.
    grid_search_cv_param_grid : dict or list of dict, optional
        Parameters (keys) and ranges (values) for exhaustive parameter search
        using cross-validation. Have to be given for each step of the pipeline
        seperated by two underscores, i.e. `s__p` is the parameter `p` for step
        `s`.
    group_datasets_by_attributes : list of str, optional
        List of dataset attributes which are used to group input data for
        `features` and `labels`. For example, this is necessary if the MLR
        model should consider multiple climate models in the training phase. If
        this option is not given, specifying multiple datasets with identical
        `var_type` and `tag` entries results in an error. If given, all the
        input data is first grouped by the given attributes and then checked
        for uniqueness within this group. After that, all groups are stacked to
        form a single set of training data.
    imputation_strategy : str, optional (default: 'remove')
        Strategy for the imputation of missing values in the features. Must be
        one of `remove`, `mean`, `median`, `most_frequent` or `constant`.
    model_name : str, optional
        Human-readable name of the MLR model instance (e.g used for labels).
    n_jobs : int, optional (default: 1)
        Maximum number of jobs spawned by this class.
    parameters : dict, optional
        Parameters used for the whole pipeline. Have to be given for each step
        of the pipeline seperated by two underscores, i.e. `s__p` is the
        parameter `p` for step `s`.
    parameters_final_regressor : dict, optional
        Parameters used for the **final** regressor. If these parameters are
        updated using the function `self.update_parameters()`, the new names
        have to be given for each step of the pipeline seperated by two
        underscores, i.e. `s__p` is the parameter `p` for step `s`.
    pattern : str, optional
        Pattern matched against ancestor files. Ignored if datasets are given
        by `input_data` argument at class initialization.
    pca : bool, optional (default: False)
        Preprocess numerical input features using PCA. Parameters for this
        pipeline step can be given via the `parameters` key.
    predict_kwargs : dict, optional
        Optional keyword arguments for the regressor's `predict()` function.
    propagate_input_errors : bool, optional (default: True)
        Propagate errors from `prediction_input_error` datasets if possible.
    return_lime_importance : bool, optional (default: False)
        Return cube with feature importance given by LIME (Local Interpretable
        Model-agnostic Explanations) during prediction.
    seaborn_settings : dict, optional
        Options for seaborn's `set()` method (affects all plots), see
        <https://seaborn.pydata.org/generated/seaborn.set.html>.
    standardize_data : bool, optional (default: True)
        Linearly standardize numerical input data by removing mean and scaling
        to unit variance.
    test_size : float, optional (default: 0.25)
        If given, exclude the desired fraction of input data from training and
        use it as test data.

    """

    _CLF_TYPE = None
    _MODELS = {}
    _MODEL_TYPE = None

    @staticmethod
    def _load_mlr_models():
        """Load MLR models from :mod:`esmvaltool.diag_scripts.mlr.models`."""
        current_path = os.path.dirname(os.path.realpath(__file__))
        models_path = os.path.join(current_path)
        for (root, _, model_files) in os.walk(models_path):
            for model_file in model_files:
                rel_path = ('' if root == models_path else os.path.relpath(
                    root, models_path))
                module = os.path.join(rel_path,
                                      os.path.splitext(model_file)[0])
                try:
                    importlib.import_module(
                        'esmvaltool.diag_scripts.mlr.models.{}'.format(
                            module.replace(os.sep, '.')))
                except ImportError:
                    pass

    @classmethod
    def register_mlr_model(cls, model_type):
        """Add model (subclass of this class) to `_MODEL` dict (decorator)."""
        logger.debug("Found available MLR model '%s'", model_type)

        def decorator(subclass):
            """Decorate subclass."""
            subclass._MODEL_TYPE = model_type
            cls._MODELS[model_type] = subclass
            return subclass

        return decorator

    @classmethod
    def create(cls, model_type, *args, **kwargs):
        """Create desired MLR model subclass (factory method)."""
        cls._load_mlr_models()
        if not cls._MODELS:
            logger.warning(
                "No MLR models found, please add subclasses to 'esmvaltool."
                "diag_scripts.mlr.models' decorated by 'MLRModel."
                "register_mlr_model'")
            return cls(*args, **kwargs)
        default_model_type = list(cls._MODELS.keys())[0]
        if model_type not in cls._MODELS:
            logger.warning(
                "MLR model type '%s' not found in 'esmvaltool.diag_scripts."
                "mlr.models', using default model type '%s'", model_type,
                default_model_type)
            model_type = default_model_type
        logger.info("Created MLR model '%s' with final regressor %s",
                    model_type, cls._MODELS[model_type]._CLF_TYPE)
        return cls._MODELS[model_type](*args, **kwargs)

    def __init__(self, cfg, input_data=None, root_dir=None):
        """Initialize base class members.

        Parameters
        ----------
        cfg : dict
            Diagnostic script configuration.
        input_data : list of dict, optional
            List of datasets used as input. If not specified, these are
            automatically extracted from the `cfg` dictionary.
        root_dir : str, optional
            Root directory for output (subdirectory in `work_dir` and
            `plot_dir`).

        """
        self._cfg = deepcopy(cfg)
        self._clf = None
        self._data = {}
        self._data['pred'] = {}
        self._datasets = {}
        self._skater = {}
        self._classes = {}
        self._parameters = {}

        # Default settings
        self._cfg.setdefault('cache_intermediate_results', True)
        self._cfg.setdefault('dtype', 'float64')
        self._cfg.setdefault('estimate_mlr_model_error', {})
        self._cfg.setdefault('imputation_strategy', 'remove')
        self._cfg.setdefault('model_name', f'{self._CLF_TYPE} model')
        self._cfg.setdefault('n_jobs', 1)
        self._cfg.setdefault('parameters', {})
        self._cfg.setdefault('pca', False)
        self._cfg.setdefault('standardize_data', True)
        self._cfg.setdefault('test_size', 0.25)
        logger.info("Using imputation strategy '%s'",
                    self._cfg['imputation_strategy'])

        # Seaborn
        sns.set(**self._cfg.get('seaborn_settings', {}))

        # Adapt output directories
        if root_dir is None:
            root_dir = ''
        self._cfg['root_dir'] = root_dir
        self._cfg['mlr_work_dir'] = os.path.join(self._cfg['work_dir'],
                                                 root_dir)
        self._cfg['mlr_plot_dir'] = os.path.join(self._cfg['plot_dir'],
                                                 root_dir)
        if not os.path.exists(self._cfg['mlr_work_dir']):
            os.makedirs(self._cfg['mlr_work_dir'])
            logger.info("Created %s", self._cfg['mlr_work_dir'])
        if not os.path.exists(self._cfg['mlr_plot_dir']):
            os.makedirs(self._cfg['mlr_plot_dir'])
            logger.info("Created %s", self._cfg['mlr_plot_dir'])

        # Load datasets, classes and training data
        self._load_input_datasets(input_data=input_data)
        self._load_classes()
        self._load_data()

        # Create pipeline (with all preprocessor steps and final regressor)
        self._create_pipeline()
        if self._cfg['parameters']:
            logger.debug("Found parameter(s) in recipe: %s",
                         self._cfg['parameters'])
        self.update_parameters(**self._cfg['parameters'])

        # Log successful initialization
        logger.info("Initialized MLR model (using at most %i processes)",
                    self._cfg['n_jobs'])
        logger.debug("With parameters")
        logger.debug(pformat(self.parameters))

    @property
    def categorical_features(self):
        """Categorical features (read-only)."""
        return self.features[self._classes['features'].categorical]

    @property
    def data(self):
        """Input data of the model (read-only)."""
        return self._data

    @property
    def features(self):
        """Features of the model (read-only)."""
        return self._classes['features'].index.values

    @property
    def features_after_preprocessing(self):
        """Features after preprocessing (read-only)."""
        x_train = self.get_x_array('train')
        y_train = self.get_y_array('train')
        if not self._is_fitted():
            fit_kwargs = self._cfg.get('fit_kwargs', {})
            fit_kwargs = self._update_fit_kwargs(fit_kwargs)
            self._clf.fit_transformers_only(x_train, y_train, **fit_kwargs)
        x_trans = self._clf.transform_only(x_train)
        features = self.features
        if 'pca' in self._clf.named_steps:
            n_numerical_features = (x_trans.shape[1] -
                                    self.categorical_features.size)
            features = [
                f'Principal component {idx}'
                for idx in range(n_numerical_features)
            ]
            features.extend(self.categorical_features)
        else:
            if x_trans.shape[1] != self.features.size:
                logger.warning(
                    "Number of features decreased from %i to %i during "
                    "preprocessing for unknown reasons (PCA is not performed)",
                    self.features.size, x_trans.shape[1])
                features = [
                    f'Unknown feature {idx}' for idx in range(x_trans.shape[1])
                ]
        return np.array(features)

    @property
    def features_types(self):
        """Types of the features of the model (read-only)."""
        return self._classes['features'].types

    @property
    def features_units(self):
        """Units of the features of the model (read-only)."""
        return self._classes['features'].units

    @property
    def group_attributes(self):
        """Group attributes of the model (read-only)."""
        return self._classes['group_attributes']

    @property
    def label(self):
        """Label of the model (read-only)."""
        return self._classes['label'].index.values[0]

    @property
    def label_units(self):
        """Units of the label of the model (read-only)."""
        return self._classes['label'].units.values[0]

    @property
    def numerical_features(self):
        """Numerical features (read-only)."""
        return self.features[~self._classes['features'].categorical]

    @property
    def parameters(self):
        """Parameters of the final regressor (read-only)."""
        return self._parameters

    def export_prediction_data(self, filename=None):
        """Export all prediction data contained in `self._data`.

        Parameters
        ----------
        filename : str, optional (default: '{data_type}_{pred_name}.csv')
            Name of the exported files.

        """
        for pred_name in self.data['pred']:
            self._save_csv_file('pred', filename, pred_name=pred_name)

    def export_training_data(self, filename=None):
        """Export all training data contained in `self._data`.

        Parameters
        ----------
        filename : str, optional (default: '{data_type}.csv')
            Name of the exported files.

        """
        for data_type in ('all', 'train', 'test'):
            self._save_csv_file(data_type, filename)

    def fit(self):
        """Fit MLR model."""
        if not self._clf_is_valid(text='Fitting MLR model'):
            return
        logger.info(
            "Fitting MLR model with final regressor %s on %i training "
            "point(s)", self._CLF_TYPE, len(self.data['train'].index))
        fit_kwargs = self._cfg.get('fit_kwargs', {})
        if fit_kwargs:
            logger.info("Using keyword argument(s) %s for fit() function",
                        fit_kwargs)
        fit_kwargs = self._update_fit_kwargs(fit_kwargs)

        # Create MLR model with desired parameters and fit it
        self._clf.fit(self.data['train'].x, self.data['train'].y, **fit_kwargs)
        self._parameters = self._get_clf_parameters()
        logger.info("Successfully fitted MLR model on %i training point(s)",
                    len(self.data['train'].index))
        logger.debug("Pipeline steps:")
        logger.debug(pformat(list(self._clf.named_steps.keys())))
        logger.debug("Parameters:")
        logger.debug(pformat(self.parameters))

        # Interpretation
        self._load_skater_interpreters()

    def get_data_frame(self, data_type, impute_nans=False):
        """Return data frame of specfied type.

        Parameters
        ----------
        data_type : str
            Data type to be returned (one of `'all'`, `'train'` or `'test'`).
        impute_nans : bool, optional (default: False)
            Impute nans if desired.

        Returns
        -------
        pandas.DataFrame
            Desired data.

        Raises
        ------
        TypeError
            `data_type` is invalid or data does not exist (e.g. test data is
            not set).

        """
        allowed_types = ('all', 'train', 'test')
        if data_type not in allowed_types:
            raise TypeError(
                f"'{data_type}' is not an allowed type, specify one of "
                f"'{allowed_types}'")
        if data_type not in self.data:
            raise TypeError(f"No '{data_type}' data available")
        data_frame = self.data[data_type]
        if impute_nans:
            data_frame = self._impute_nans(data_frame)
        return data_frame

    def get_x_array(self, data_type, impute_nans=False):
        """Return x data of specific type as :mod:`numpy.array`.

        Parameters
        ----------
        data_type : str
            Data type to be returned (one of `'all'`, `'train'` or `'test'`).
        impute_nans : bool, optional (default: False)
            Impute nans if desired.

        Returns
        -------
        numpy.array
            Desired data.

        Raises
        ------
        TypeError
            `data_type` is invalid or data does not exist (e.g. test data is
            not set).

        """
        data_frame = self.get_data_frame(data_type, impute_nans=impute_nans)
        return data_frame.x.values

    def get_y_array(self, data_type, impute_nans=False):
        """Return y data of specific type as :mod:`numpy.array`.

        Parameters
        ----------
        data_type : str
            Data type to be returned (one of `'all'`, `'train'` or `'test'`).
        impute_nans : bool, optional (default: False)
            Impute nans if desired.

        Returns
        -------
        numpy.array
            Desired data.

        Raises
        ------
        TypeError
            `data_type` is invalid or data does not exist (e.g. test data is
            not set).

        """
        data_frame = self.get_data_frame(data_type, impute_nans=impute_nans)
        return data_frame.y.squeeze().values

    def grid_search_cv(self, param_grid=None, **kwargs):
        """Perform exhaustive parameter search using cross-validation.

        Parameters
        ----------
        param_grid : dict or list of dict, optional
            Parameter names (keys) and ranges (values) for the search. Have to
            be given for each step of the pipeline seperated by two
            underscores, i.e. `s__p` is the parameter `p` for step `s`.
            Overwrites default and recipe settings.
        **kwargs : keyword arguments, optional
            Additional options for the `GridSearchCV` class. See
            <https://scikit-learn.org/stable/modules/generated/
            sklearn.model_selection.GridSearchCV.html>. Overwrites default and
            recipe settings.

        """
        if not self._clf_is_valid(text='GridSearchCV'):
            return
        parameter_grid = self._cfg.get('grid_search_cv_param_grid', {})
        if param_grid is not None:
            parameter_grid = param_grid
        if not parameter_grid:
            logger.warning(
                "Cannot perform exhaustive grid search, no parameter grid "
                "given (neither in recipe nor in grid_search_cv() function)")
            return
        logger.info(
            "Performing exhaustive grid search cross-validation with final "
            "regressor %s and parameter grid %s on %i training points",
            self._CLF_TYPE, parameter_grid, len(self.data['train'].index))

        # Get keyword arguments
        verbosity = self._get_verbosity_parameters(GridSearchCV)
        cv_kwargs = {
            'n_jobs': self._cfg['n_jobs'],
            **verbosity,
        }
        cv_kwargs.update(self._cfg.get('grid_search_cv_kwargs', {}))
        cv_kwargs.update(kwargs)
        logger.info("Using keyword argument(s) %s for GridSearchCV class",
                    cv_kwargs)
        if isinstance(cv_kwargs.get('cv'), str):
            if cv_kwargs['cv'].lower() == 'loo':
                cv_kwargs['cv'] = LeaveOneOut()
        fit_kwargs = self._cfg.get('fit_kwargs', {})
        if fit_kwargs:
            logger.info("Using keyword argument(s) %s for fit() function",
                        fit_kwargs)
        fit_kwargs = self._update_fit_kwargs(fit_kwargs)

        # Create and fit GridSearchCV instance
        clf = GridSearchCV(self._clf, parameter_grid, **cv_kwargs)
        clf.fit(self.data['train'].x, self.data['train'].y, **fit_kwargs)

        # Try to find best estimator
        if hasattr(clf, 'best_estimator_'):
            self._clf = clf.best_estimator_
        elif hasattr(clf, 'best_params_'):
            self.update_parameters(**clf.best_params_)
            self._clf.fit(self.data['train'].x, self.data['train'].y,
                          **fit_kwargs)
        else:
            raise ValueError(
                "GridSearchCV not successful, cannot determine best estimator "
                "(neither using 'best_estimator_' nor 'best_params_'), "
                "adapt 'grid_search_cv_kwargs' accordingly (see "
                "<https://scikit-learn.org/stable/modules/generated/"
                "sklearn.model_selection.GridSearchCV.html> for more help)")
        self._parameters = self._get_clf_parameters()
        logger.info(
            "Exhaustive grid search successful, found best parameter(s) %s",
            clf.best_params_)
        logger.debug("CV results:")
        logger.debug(pformat(clf.cv_results_))
        logger.info("Successfully fitted MLR model on %i training point(s)",
                    len(self.data['train'].index))
        logger.debug("Pipeline steps:")
        logger.debug(pformat(list(self._clf.named_steps.keys())))
        logger.debug("Parameters:")
        logger.debug(pformat(self.parameters))

        # Interpretation
        self._load_skater_interpreters()

    def plot_feature_importance(self, filename=None):
        """Plot feature importance.

        Parameters
        ----------
        filename : str, optional (default: 'feature_importance_{method}')
            Name of the plot file.

        """
        if not self._is_ready_for_plotting():
            return
        logger.info("Plotting feature importance")
        if filename is None:
            filename = 'feature_importance_{method}'
        progressbar = True if self._cfg['log_level'] == 'debug' else False

        # Plot
        for method in ('model-scoring', 'prediction-variance'):
            logger.debug("Plotting feature importance for method '%s'", method)
            (_, axes) = (self._skater['global_interpreter'].feature_importance.
                         plot_feature_importance(self._skater['model'],
                                                 method=method,
                                                 n_jobs=self._cfg['n_jobs'],
                                                 progressbar=progressbar))
            axes.set_title(f"Variable Importance ({self._cfg['model_name']})")
            axes.set_xlabel('Relative Importance')
            new_filename = (filename.format(method=method) + '.' +
                            self._cfg['output_file_type'])
            new_path = os.path.join(self._cfg['mlr_plot_dir'], new_filename)
            plt.savefig(new_path, orientation='landscape', bbox_inches='tight')
            logger.info("Wrote %s", new_path)
            plt.close()

    def plot_lime(self, index=0, data_type='test', filename=None):
        """Plot LIME explanations for specific input.

        Note
        ----
        LIME = Local Interpretable Model-agnostic Explanations.

        Parameters
        ----------
        filename : str, optional (default: 'lime')
            Name of the plot file.

        """
        if not self._is_ready_for_plotting():
            return
        logger.info("Plotting LIME")
        if data_type not in self.data:
            logger.warning("Cannot plot LIME, got invalid data type '%s'",
                           data_type)
            return
        if index >= len(self.data[data_type].index):
            logger.warning(
                "Cannot plot LIME, index %i is out of range for '%s' data",
                index, data_type)
            return
        if filename is None:
            filename = 'lime'
        new_filename_plot = filename + '.' + self._cfg['output_file_type']
        new_filename_html = filename + '.html'
        plot_path = os.path.join(self._cfg['mlr_plot_dir'], new_filename_plot)
        html_path = os.path.join(self._cfg['mlr_plot_dir'], new_filename_html)

        # LIME
        explainer = self._skater['local_interpreter'].explain_instance(
            self.get_x_array(data_type)[index], self._clf.predict)
        logger.debug("Local feature importance at index %i of '%s' data",
                     index, data_type)
        logger.debug(pformat(explainer.as_list()))

        # Html
        pred_dtype = self._get_prediction_dtype()
        if pred_dtype == 'float64':
            explainer.save_to_file(html_path)
            logger.info("Wrote %s", html_path)
        else:
            logger.warning(
                "Saving LIME output in HTML format is only supported for "
                "regressors which save predictions as dtype 'float64', "
                "%s writes '%s'", self._CLF_TYPE, pred_dtype)

        # Plot
        explainer.as_pyplot_figure()
        plt.savefig(plot_path, orientation='landscape', bbox_inches='tight')
        logger.info("Wrote %s", plot_path)
        plt.close()

    def plot_pairplots(self, filename=None):
        """Plot pairplots for features and labels.

        Parameters
        ----------
        filename : str, optional (default: 'pairplot_{data_type}')
            Name of the plot file.

        """
        if not self._is_ready_for_plotting():
            return
        logger.info("Plotting pairplots")
        if filename is None:
            filename = 'pairplot_{data_type}'

        # Plot pairplots for all data types
        for data_type in ('all', 'train', 'test'):
            if data_type not in self.data:
                continue
            data_frame = self._impute_nans(self.data[data_type])
            sns.pairplot(data_frame)
            new_filename = (filename.format(data_type=data_type) + '.' +
                            self._cfg['output_file_type'])
            new_path = os.path.join(self._cfg['mlr_plot_dir'], new_filename)
            plt.savefig(new_path, orientation='landscape', bbox_inches='tight')
            logger.info("Wrote %s", new_path)
            plt.close()

    def plot_partial_dependences(self, filename=None):
        """Plot partial dependences for every feature.

        Parameters
        ----------
        filename : str, optional (default: 'partial_dependece_{feature}')
            Name of the plot file.

        """
        if not self._is_ready_for_plotting():
            return
        logger.info("Plotting partial dependences")
        if filename is None:
            filename = 'partial_dependece_{feature}'

        # Plot for every feature
        x_train = self.get_x_array('train', impute_nans=True)
        verbosity = self._get_verbosity_parameters(plot_partial_dependence)
        for feature_name in self.features:
            logger.debug("Plotting partial dependence of '%s'", feature_name)
            plot_partial_dependence(
                self._clf,
                x_train,
                features=[feature_name],
                feature_names=self.features,
                **verbosity,
            )
            plt.title(f"Partial dependence ({self._cfg['model_name']})")
            plt.xlabel(f'{feature_name} / {self.features_units[feature_name]}')
            plt.ylabel(f'Partial dependence on {self.label}')
            new_filename = (filename.format(feature=feature_name) + '.' +
                            self._cfg['output_file_type'])
            new_path = os.path.join(self._cfg['mlr_plot_dir'], new_filename)
            plt.savefig(new_path, orientation='landscape', bbox_inches='tight')
            logger.info("Wrote %s", new_path)
            plt.close()

    def plot_scatterplots(self, filename=None):
        """Plot scatterplots label vs. feature for every feature.

        Parameters
        ----------
        filename : str, optional (default: 'scatterplot_{feature}')
            Name of the plot file.

        """
        if not self._is_ready_for_plotting():
            return
        logger.info("Plotting scatterplots")
        if filename is None:
            filename = 'scatterplot_{feature}'

        # Plot scatterplot for every feature
        for feature in self.features:
            logger.debug("Plotting scatterplot of '%s'", feature)
            (_, axes) = plt.subplots()
            if self._cfg.get('accept_only_scalar_data'):
                for (g_idx, group_attr) in enumerate(self.group_attributes):
                    axes.scatter(self.data['all'].x.loc[g_idx, feature],
                                 self.data['all'].y.iloc[g_idx, 0],
                                 label=group_attr)
                for (pred_name, pred) in self.data['pred'].items():
                    axes.axvline(pred.x.loc[0, feature],
                                 linestyle='--',
                                 color='black',
                                 label=('Observation'
                                        if pred_name is None else pred_name))
                legend = axes.legend(loc='center left',
                                     ncol=2,
                                     bbox_to_anchor=[1.05, 0.5],
                                     borderaxespad=0.0)
            else:
                axes.plot(self.data['all'].x.loc[:, feature],
                          self.get_y_array('all'), '.')
                legend = None
            axes.set_title(feature)
            axes.set_xlabel(f'{feature} / {self.features_units[feature]}')
            axes.set_ylabel(f'{self.label} / {self.label_units}')
            new_path = os.path.join(
                self._cfg['mlr_plot_dir'],
                filename.format(feature=feature) + '.' +
                self._cfg['output_file_type'])
            plt.savefig(new_path,
                        orientation='landscape',
                        bbox_inches='tight',
                        additional_artists=[legend])
            logger.info("Wrote %s", new_path)
            plt.close()

    def predict(self, **kwargs):
        """Perform prediction using the MLR model(s) and write netcdf.

        Parameters
        ----------
        **kwargs : keyword arguments, optional
            Additional options for the `self._clf.predict()` function.
            Overwrites default and recipe settings.

        """
        if not self._is_fitted():
            logger.warning(
                "Prediction not possible, MLR model is not fitted yet")
            return
        logger.info("Started prediction")
        predict_kwargs = dict(self._cfg.get('predict_kwargs', {}))
        predict_kwargs.update(kwargs)
        if 'return_var' in predict_kwargs and 'return_cov' in predict_kwargs:
            logger.warning(
                "Found 'return_var' and 'return_cov' in prediction keyword "
                "arguments, but returning both is not possible. Returning "
                "only variance")
            predict_kwargs.pop('return_cov')
        if predict_kwargs:
            logger.info(
                "Using additional keyword argument(s) %s for predict() "
                "function", predict_kwargs)

        # Iterate over different predictions
        for pred_name in self._datasets['prediction_input']:
            logger.info("Predicting '%s'", self._get_name(pred_name))

            # Prediction
            (x_pred, x_err, y_ref,
             x_cube) = self._extract_prediction_input(pred_name)
            pred_dict = self._get_prediction_dict(x_pred, x_err, y_ref,
                                                  **predict_kwargs)

            # Save data in class member
            y_pred = pd.DataFrame(pred_dict[None],
                                  columns=[self.label],
                                  dtype=self._cfg['dtype'])
            self._data['pred'][pred_name] = pd.concat([x_pred, y_pred],
                                                      axis=1,
                                                      keys=['x', 'y'])

            # Save prediction cubes
            self._save_prediction_cubes(pred_dict, pred_name, x_cube)

    def print_correlation_matrices(self):
        """Print correlation matrices for all datasets."""
        if not self._is_fitted():
            logger.warning(
                "Printing correlation matrices not possible, MLR model is not "
                "fitted yet")
            return
        for data_type in ('all', 'train', 'test'):
            if data_type not in self.data:
                continue
            logger.info("Correlation matrix for %s data:\n%s", data_type,
                        self.data[data_type].corr())

    def print_regression_metrics(self):
        """Print all available regression metrics for training data."""
        if not self._is_fitted():
            logger.warning(
                "Printing regression metrics not possible, MLR model is not "
                "fitted yet")
            return
        regression_metrics = [
            'explained_variance_score',
            'mean_absolute_error',
            'mean_squared_error',
            'median_absolute_error',
            'r2_score',
        ]
        for data_type in ('all', 'train', 'test'):
            if data_type not in self.data:
                continue
            logger.info("Evaluating regression metrics for %s data", data_type)
            x_data = self.get_x_array(data_type)
            y_true = self.get_y_array(data_type)
            y_pred = self._clf.predict(x_data)
            y_norm = np.std(y_true)
            for metric in regression_metrics:
                metric_function = getattr(metrics, metric)
                value = metric_function(y_true, y_pred)
                if 'squared' in metric:
                    value = np.sqrt(value)
                    metric = f'root_{metric}'
                if metric.endswith('_error'):
                    value /= y_norm
                    metric = f'{metric} (normalized by std)'
                logger.info("%s: %s", metric, value)

    def update_parameters(self, **params):
        """Update parameters of the whole pipeline.

        Parameters
        ----------
        **params : keyword arguments, optional
            Paramaters for the pipeline which should be updated.

        Note
        ----
        Parameter names have to be given for each step of the pipeline
        seperated by two underscores, i.e. `s__p` is the parameter `p` for
        step `s`.

        """
        if not self._clf_is_valid(text='Updating parameters of MLR model'):
            return
        allowed_params = self._get_clf_parameters()
        new_params = {}
        for (key, val) in params.items():
            if key in allowed_params:
                new_params[key] = val
            else:
                logger.warning(
                    "'%s' is not a valid parameter for the pipeline", key)
        self._clf.set_params(**new_params)
        self._parameters = self._get_clf_parameters()
        if new_params:
            logger.info("Updated pipeline with parameters %s", new_params)

    def _check_cube_dimensions(self, cube, ref_cube, text=None):
        """Check shape and coordinates of a given cube."""
        msg = '' if text is None else f' for {text}'
        if self._cfg.get('accept_only_scalar_data'):
            allowed_shapes = [(), (1, )]
            if cube.shape not in allowed_shapes:
                raise ValueError(
                    f"Expected only cubes with shapes {allowed_shapes} when "
                    f"option 'accept_only_scalar_data' is set to 'True', got "
                    f"{cube.shape}{msg}")
        else:
            if ref_cube is None:
                return
            if cube.shape != ref_cube.shape:
                raise ValueError(
                    f"Expected cubes with shapes {ref_cube.shape}{msg}, got "
                    f"{cube.shape}. Consider regridding, pre-selecting data "
                    f"at class initialization using the argument 'input_data' "
                    f"or the options 'broadcast_from' or 'group_datasets_by_"
                    f"attributes'")
            cube_coords = cube.coords(dim_coords=True)
            ref_coords = ref_cube.coords(dim_coords=True)
            cube_coords_str = [
                f'{coord.name()}, shape {coord.shape}' for coord in cube_coords
            ]
            ref_coords_str = [
                f'{coord.name()}, shape {coord.shape}' for coord in ref_coords
            ]
            if cube_coords_str != ref_coords_str:
                logger.warning(
                    "Cube coordinates differ, expected %s%s, got %s. Check "
                    "input cubes", ref_coords_str, msg, cube_coords_str)
                return
            for (idx, cube_coord) in enumerate(cube_coords):
                ref_coord = ref_coords[idx]
                if not np.allclose(cube_coord.points, ref_coord.points):
                    logger.warning(
                        "'%s' coordinate for different cubes does not "
                        "match, got %s%s, expected %s (values differ by "
                        "more than allowed tolerance, check input cubes)",
                        cube_coord.name(), cube_coord.points, msg,
                        ref_coord.points)

    def _check_dataset(self, datasets, var_type, tag, text=None):
        """Check if datasets exist and are valid."""
        datasets = select_metadata(datasets, tag=tag, var_type=var_type)
        msg = '' if text is None else text
        if not datasets:
            if var_type == 'prediction_input_error':
                return None
            if var_type == 'prediction_reference':
                return None
            if var_type == 'label':
                raise ValueError(f"Label '{tag}'{msg} not found")
            if not self._cfg.get('allow_missing_features'):
                raise ValueError(
                    f"{var_type} '{tag}'{msg} not found, use 'allow_missing_"
                    f"features' to ignore this")
            logger.info(
                "Ignored missing %s '%s'%s since 'allow_missing_features' is "
                "set to 'True'", var_type, tag, msg)
            return None
        if len(datasets) > 1:
            raise ValueError(
                f"{var_type} '{tag}'{msg} not unique, consider the use of the "
                f"argument 'input_data' at class initialization to pre-select "
                f"datasets or specify suitable attributes to group datasets "
                f"with the option 'group_datasets_by_attributes'")
        if var_type == 'label':
            units = self.label_units
        else:
            units = self.features_units[tag]
        if units != Unit(datasets[0]['units']):
            raise ValueError(
                f"Expected units '{units}' for {var_type} '{tag}'{msg}, got "
                f"'{datasets[0]['units']}'")
        return datasets[0]

    def _clf_is_valid(self, text=None):
        """Check if valid regressor type is given."""
        msg = '' if text is None else f'{text} not possible: '
        if self._CLF_TYPE is None:
            logger.warning(
                "%sNo MLR model specified, please use factory function "
                "'MLRModel.create()' to initialize this class or populate the "
                "module 'esmvaltool.diag_scripts.mlr.models' if necessary",
                msg)
            return False
        return True

    def _create_pipeline(self):
        """Create pipeline with correct settings."""
        if not self._clf_is_valid(text='Creating pipeline'):
            return
        steps = []
        numerical_features_idx = [
            int(np.where(self.features == tag)[0][0])
            for tag in self.numerical_features
        ]

        # DataFrame to numpy converter
        steps.append(('pandas_to_numpy_converter',
                      ColumnTransformer([], remainder='passthrough')))

        # Imputer
        if self._cfg['imputation_strategy'] != 'remove':
            verbosity = self._get_verbosity_parameters(SimpleImputer)
            imputer = SimpleImputer(
                strategy=self._cfg['imputation_strategy'],
                **verbosity,
            )
            steps.append(('imputer', imputer))

        # Scaler for numerical features
        if self._cfg['standardize_data']:
            x_scaler = ColumnTransformer(
                [('', StandardScaler(), numerical_features_idx)],
                remainder='passthrough',
            )
            steps.append(('x_scaler', x_scaler))

        # PCA for numerical features
        if self._cfg['pca']:
            pca = ColumnTransformer(
                [('', PCA(), numerical_features_idx)],
                remainder='passthrough',
            )
            steps.append(('pca', pca))

        # Final regressor
        final_parameters = self._load_final_parameters()
        final_regressor = self._CLF_TYPE(**final_parameters)

        # Transformer for labels if desired (if not, add pd to np converter)
        if self._cfg['standardize_data']:
            y_scaler = StandardScaler()
        else:
            y_scaler = StandardScaler(with_mean=False, with_std=False)
        transformed_target_regressor = mlr.AdvancedTransformedTargetRegressor(
            transformer=y_scaler, regressor=final_regressor)
        steps.append(('final', transformed_target_regressor))

        # Final pipeline
        if self._cfg['cache_intermediate_results']:
            if self._cfg['n_jobs'] is None or self._cfg['n_jobs'] == 1:
                memory = self._cfg['mlr_work_dir']
            else:
                logger.debug(
                    "Caching intermediate results of Pipeline is not "
                    "supported for multiple processes (using at most %i "
                    "processes)", self._cfg['n_jobs'])
                memory = None
        else:
            memory = None
        self._clf = mlr.AdvancedPipeline(steps, memory=memory)
        logger.debug("Created pipeline with steps %s",
                     list(self._clf.named_steps.keys()))

    def _estimate_mlr_model_error(self, target_length):
        """Estimate squared error of MLR model (using CV or test data)."""
        cfg = deepcopy(self._cfg['estimate_mlr_model_error'])
        cfg.setdefault('type', 'cv')
        cfg.setdefault('kwargs', {})
        cfg['kwargs'].setdefault('cv', 5)
        cfg['kwargs'].setdefault('scoring', 'neg_mean_squared_error')
        logger.debug("Estimating squared error of MLR model using %s", cfg)

        # Check type
        err_type = cfg['type']
        allowed_types = ('test', 'cv')
        if err_type not in allowed_types:
            default = 'cv'
            logger.warning(
                "Got invalid type '%s' for MLR model error estimation, "
                "expected one of %s, defaulting to '%s'", err_type,
                allowed_types, default)
            err_type = default

        # Test data set
        if err_type == 'test':
            if 'test' in self.data:
                y_pred = self._clf.predict(self.get_x_array('test'))
                error = metrics.mean_squared_error(self.get_y_array('test'),
                                                   y_pred)
            else:
                logger.warning(
                    "Cannot estimate squared MLR model error using 'type: "
                    "test', no test data set given (use 'test_size' option), "
                    "using cross-validation instead")
                err_type = 'cv'

        # CV
        if err_type == 'cv':
            error = cross_val_score(self._clf, self.get_x_array('train'),
                                    self.get_y_array('train'), **cfg['kwargs'])
            error = np.mean(error)
            if cfg['kwargs']['scoring'].startswith('neg_'):
                error = -error
            if 'squared' not in cfg['kwargs']['scoring']:
                error *= error

        # Get correct dtype
        error_array = np.full(target_length, error, dtype=self._cfg['dtype'])
        units = mlr.units_power(self.label_units, 2)
        logger.info("Estimated squared MLR model error by %s %s using %s data",
                    error, units, err_type)
        return (error_array, err_type)

    def _extract_features_and_labels(self):
        """Extract feature and label data points from training data."""
        (x_data, _) = self._extract_x_data(self._datasets['feature'],
                                           'feature')
        y_data = self._extract_y_data(self._datasets['label'], 'label')

        # Check number of input points
        if len(x_data.index) != len(y_data.index):
            raise ValueError(
                "Sizes of features and labels do not match, got {:d} point(s) "
                "for the features and {:d} point(s) for the label".format(
                    len(x_data.index), len(y_data.index)))
        logger.info("Found %i raw input data point(s) with data type '%s'",
                    len(y_data.index), self._cfg['dtype'])

        # Remove missing values in labels
        (x_data, y_data) = self._remove_missing_labels(x_data, y_data)

        # Remove missing values in features (if desired)
        (x_data, y_data) = self._remove_missing_features(x_data, y_data)

        return (x_data, y_data)

    def _extract_prediction_input(self, prediction_name):
        """Extract prediction input data points for `prediction_name`."""
        (x_pred, x_cube) = self._extract_x_data(
            self._datasets['prediction_input'][prediction_name],
            'prediction_input')
        logger.info(
            "Found %i raw prediction input data point(s) with data type '%s'",
            len(x_pred.index), self._cfg['dtype'])

        # Prediction reference
        if prediction_name not in self._datasets['prediction_reference']:
            y_ref = None
            logger.debug(
                "No prediction reference for prediction '%s' available",
                self._get_name(prediction_name))
        else:
            y_ref = self._extract_y_data(
                self._datasets['prediction_reference'][prediction_name],
                'prediction_reference')
            if y_ref is not None:
                if len(x_pred.index) != len(y_ref.index):
                    raise ValueError(
                        "Sizes of prediction input and prediction output do "
                        "not match, got {:d} point(s) for the prediction "
                        "input and {:d} point(s) for the prediction "
                        "output".format(len(x_pred.index), len(y_ref.index)))
                else:
                    logger.info(
                        "Found %i raw prediction output data point(s) with "
                        "data type '%s'", len(y_ref.index), self._cfg['dtype'])

        # Error
        if prediction_name not in self._datasets['prediction_input_error']:
            x_err = None
            logger.debug(
                "Propagating prediction input errors for prediction '%s' not "
                "possible, no 'prediction_input_error' datasets given",
                self._get_name(prediction_name))
        else:
            (x_err, _) = self._extract_x_data(
                self._datasets['prediction_input_error'][prediction_name],
                'prediction_input_error')
            if len(x_pred.index) != len(x_err.index):
                raise ValueError(
                    "Sizes of prediction input and prediction input error do "
                    "not match, got {:d} point(s) for the prediction input "
                    "and {:d} point(s) for the prediction input errors".format(
                        len(x_pred.index), len(x_err.index)))
            logger.info(
                "Found %i raw prediction input error data point(s) with data "
                "type '%s'", len(x_err.index), self._cfg['dtype'])

        # Assign correct mask to cube
        mask = x_pred.isnull().any(axis=1).values.reshape(x_cube.shape)
        x_cube.data = np.ma.array(x_cube.data, mask=mask)

        # Remove missing values if necessary
        (x_pred, x_err,
         y_ref) = self._remove_missing_pred_input(x_pred, x_err, y_ref)

        return (x_pred, x_err, y_ref, x_cube)

    def _extract_x_data(self, datasets, var_type):
        """Extract required x data of type `var_type` from `datasets`."""
        allowed_types = ('feature', 'prediction_input',
                         'prediction_input_error')
        if var_type not in allowed_types:
            raise ValueError(
                f"Excepted one of '{allowed_types}' for 'var_type', got "
                f"'{var_type}'")
        x_data = pd.DataFrame(columns=self.features, dtype=self._cfg['dtype'])
        x_cube = None

        # Iterate over datasets
        datasets = select_metadata(datasets, var_type=var_type)
        if var_type == 'feature':
            groups = self.group_attributes
        else:
            groups = [None]
        for group_attr in groups:
            group_datasets = select_metadata(datasets,
                                             group_attribute=group_attr)
            if group_attr is not None:
                logger.info("Loading '%s' data of '%s'", var_type, group_attr)
            msg = '' if group_attr is None else f" for '{group_attr}'"
            if not group_datasets:
                raise ValueError(f"No '{var_type}' data{msg} found")
            (group_data,
             x_cube) = self._get_x_data_for_group(group_datasets, var_type,
                                                  group_attr)
            x_data = x_data.append(group_data, ignore_index=True)

        return (x_data, x_cube)

    def _extract_y_data(self, datasets, var_type):
        """Extract required y data of type `var_type` from `datasets`."""
        allowed_types = ('label', 'prediction_reference')
        if var_type not in allowed_types:
            raise ValueError(
                f"Excepted one of '{allowed_types}' for 'var_type', got "
                f"'{var_type}'")
        y_data = pd.DataFrame(columns=[self.label], dtype=self._cfg['dtype'])

        # Iterate over datasets
        datasets = select_metadata(datasets, var_type=var_type)
        if var_type == 'label':
            groups = self.group_attributes
        else:
            groups = [None]
        for group_attr in groups:
            if group_attr is not None:
                logger.info("Loading '%s' data of '%s'", var_type, group_attr)
            msg = '' if group_attr is None else f" for '{group_attr}'"
            group_datasets = select_metadata(datasets,
                                             group_attribute=group_attr)
            dataset = self._check_dataset(group_datasets, var_type, self.label,
                                          msg)
            if dataset is None:
                return None
            cube = self._load_cube(dataset)
            text = f"{var_type} '{self.label}'{msg}"
            self._check_cube_dimensions(cube, None, text)
            cube_data = pd.DataFrame(self._get_cube_data(cube),
                                     columns=[self.label],
                                     dtype=self._cfg['dtype'])
            y_data = y_data.append(cube_data, ignore_index=True)
        return y_data

    def _get_ancestor_datasets(self):
        """Get ancestor datasets."""
        pattern = self._cfg.get('pattern')
        if pattern is not None:
            logger.debug("Matching ancestor files against pattern %s", pattern)
        datasets = io.netcdf_to_metadata(self._cfg, pattern=pattern)
        if not datasets:
            logger.debug("Skipping loading ancestor datasets, no files found")
            return []
        logger.debug("Found ancestor file(s):")
        logger.debug(pformat([d['filename'] for d in datasets]))

        # Check MLR attributes
        valid_datasets = []
        for dataset in datasets:
            if mlr.datasets_have_mlr_attributes([dataset], log_level='info'):
                valid_datasets.append(dataset)
            else:
                logger.info("Skipping ancestor file %s", dataset['filename'])
        return valid_datasets

    def _get_broadcasted_cube(self, dataset, ref_cube, text=None):
        """Get broadcasted cube."""
        msg = '' if text is None else text
        target_shape = ref_cube.shape
        cube_to_broadcast = self._load_cube(dataset)
        data_to_broadcast = np.ma.filled(cube_to_broadcast.data, np.nan)
        try:
            new_axis_pos = np.delete(np.arange(len(target_shape)),
                                     dataset['broadcast_from'])
        except IndexError:
            raise IndexError(
                "Broadcasting to shape {} failed{}, index out of bounds".
                format(target_shape, msg))
        logger.info("Broadcasting %s from %s to %s", msg,
                    data_to_broadcast.shape, target_shape)
        for idx in new_axis_pos:
            data_to_broadcast = np.expand_dims(data_to_broadcast, idx)
        data_to_broadcast = np.broadcast_to(data_to_broadcast, target_shape)
        new_cube = ref_cube.copy(np.ma.masked_invalid(data_to_broadcast))
        for idx in dataset['broadcast_from']:
            new_coord = new_cube.coord(dimensions=idx)
            new_coord.points = cube_to_broadcast.coord(new_coord).points
        logger.debug("Added broadcasted %s", msg)
        return new_cube

    def _get_clf_parameters(self, deep=True):
        """Get parameters of pipeline."""
        return self._clf.get_params(deep=deep)

    def _get_features(self):
        """Extract all features from the `prediction_input` datasets."""
        logger.debug("Extracting features from 'prediction_input' datasets")
        pred_name = list(self._datasets['prediction_input'].keys())[0]
        datasets = self._datasets['prediction_input'][pred_name]
        msg = f"for prediction '{self._get_name(pred_name)}'"
        (units,
         types) = self._get_features_of_datasets(datasets, 'prediction_input',
                                                 msg)

        # Mark categorical variables
        categorical = {feature: False for feature in types}
        for tag in self._cfg.get('categorical_features', []):
            if tag in categorical:
                logger.debug("Treating '%s' as categorical feature", tag)
                categorical[tag] = True
            else:
                logger.warning(
                    "Cannot treat '%s' as categorical variable, "
                    "feature not found", tag)

        # Check if features were found
        if not units:
            raise ValueError(
                f"No features for 'prediction_input' data{msg} found")

        # Check for wrong options
        if self._cfg.get('accept_only_scalar_data'):
            if 'broadcasted' in types.values():
                raise TypeError(
                    "The use of 'broadcast_from' is not possible if "
                    "'accept_only_scalar_data' is given")
            if 'coordinate' in types.values():
                raise TypeError(
                    "The use of 'coords_as_features' is not possible if "
                    "'accept_only_scalar_data' is given")

        # Convert to DataFrame and sort it
        units = pd.DataFrame.from_dict(units,
                                       orient='index',
                                       columns=['units'])
        types = pd.DataFrame.from_dict(types,
                                       orient='index',
                                       columns=['types'])
        categorical = pd.DataFrame.from_dict(categorical,
                                             orient='index',
                                             columns=['categorical'])
        features = pd.concat([units, types, categorical], axis=1).sort_index()

        # Return features
        logger.info(
            "Found %i feature(s) (defined in 'prediction_input' data%s)",
            len(features.index), msg)
        for feature in features.index:
            logger.debug("'%s' with units '%s' and type '%s'", feature,
                         features.units.loc[feature],
                         features.types.loc[feature])
        return features

    def _get_features_of_datasets(self, datasets, var_type, msg):
        """Extract all features (with units and types) of given datasets."""
        units = {}
        types = {}
        cube = None
        ref_cube = None
        for (tag, datasets_) in group_metadata(datasets, 'tag').items():
            dataset = datasets_[0]
            cube = self._load_cube(dataset)
            if 'broadcast_from' not in dataset:

                ref_cube = cube
            units[tag] = Unit(dataset['units'])
            if 'broadcast_from' in dataset:
                types[tag] = 'broadcasted'
            else:
                types[tag] = 'regular'

        # Check if reference cube was given
        if ref_cube is None:
            if cube is None:
                raise ValueError(
                    f"Expected at least one '{var_type}' dataset{msg}")
            else:
                raise ValueError(
                    f"Expected at least one '{var_type}' dataset{msg} without "
                    f"the option 'broadcast_from'")

        # Coordinate features
        for coord_name in self._cfg.get('coords_as_features', []):
            try:
                coord = ref_cube.coord(coord_name)
            except iris.exceptions.CoordinateNotFoundError:
                raise iris.exceptions.CoordinateNotFoundError(
                    f"Coordinate '{coord_name}' given in 'coords_as_features' "
                    f"not found in '{var_type}' data{msg}")
            units[coord_name] = coord.units
            types[coord_name] = 'coordinate'

        return (units, types)

    def _get_group_attributes(self):
        """Get all group attributes from `label` datasets."""
        logger.debug("Extracting group attributes from 'label' datasets")
        grouped_datasets = group_metadata(self._datasets['label'],
                                          'group_attribute',
                                          sort=True)
        group_attributes = list(grouped_datasets.keys())
        if group_attributes == [None]:
            logger.debug("No group attributes given")
        else:
            logger.info(
                "Found %i group attribute(s) (defined in 'label' data)",
                len(group_attributes))
            logger.debug(pformat(group_attributes))
        return np.array(group_attributes)

    def _get_label(self):
        """Extract label from training data."""
        logger.debug("Extracting label from training datasets")
        grouped_datasets = group_metadata(self._datasets['label'], 'tag')
        labels = list(grouped_datasets.keys())
        if len(labels) > 1:
            raise ValueError(f"Expected unique label tag, got {labels}")
        units = Unit(self._datasets['label'][0]['units'])
        logger.info(
            "Found label '%s' with units '%s' (defined in 'label' "
            "data)", labels[0], units)
        label = pd.DataFrame.from_dict({labels[0]: units},
                                       orient='index',
                                       columns=['units'])
        return label

    def _get_lime_feature_importance(self, x_pred):
        """Get most important feature given by LIME."""
        logger.info(
            "Calculating global feature importance using LIME (this may take "
            "a while...)")
        x_pred = self._impute_nans(x_pred)

        # Most important feature for single input
        def _most_important_feature(x_single_pred, interpreter, predict_fn):
            """Get most important feature for single input."""
            explainer = interpreter.explain_instance(x_single_pred,
                                                     predict_fn,
                                                     num_features=1)
            return explainer.as_map()[1][0][0]

        # Apply on whole input (using multiple processes)
        _most_important_feature = partial(
            _most_important_feature,
            interpreter=self._skater['local_interpreter'],
            predict_fn=self._clf.predict,
        )
        pool = mp.ProcessPool(processes=self._cfg['n_jobs'])
        return np.array(pool.map(_most_important_feature, x_pred.values),
                        dtype=self._cfg['dtype'])

    def _get_prediction_dict(self, x_pred, x_err, y_ref, **kwargs):
        """Get prediction output in a dictionary."""
        logger.info("Predicting %i point(s)", len(x_pred.index))
        y_preds = self._clf.predict(x_pred.values, **kwargs)
        pred_dict = self._prediction_to_dict(y_preds, **kwargs)

        # Estimate error of MLR model itself
        if self._cfg['estimate_mlr_model_error']:
            (pred_error,
             err_type) = self._estimate_mlr_model_error(len(x_pred.index))
            pred_dict[f"squared_mlr_model_error_estim_{err_type}"] = pred_error

        # Propagate prediction input errors if possible
        if self._cfg.get('propagate_input_errors', True) and x_err is not None:
            pred_dict['squared_propagated_input_error'] = (
                self._propagate_input_errors(x_pred, x_err))

        # LIME feature importance
        if self._cfg.get('return_lime_importance'):
            pred_dict['lime'] = self._get_lime_feature_importance(x_pred)

        # Calculate residuals relative to reference if possible
        if y_ref is not None:
            y_ref = y_ref.values
            if y_ref.ndim == 2 and y_ref.shape[1] == 1:
                y_ref = np.squeeze(y_ref, axis=1)
            pred_dict['residual'] = self._get_residuals(pred_dict[None], y_ref)

        # Return dictionary
        for pred_type in pred_dict:
            if pred_type is not None:
                logger.debug("Found additional prediction type '%s'",
                             pred_type)
        logger.info(
            "Successfully created prediction array(s) with %i point(s)",
            pred_dict[None].size)
        return pred_dict

    def _get_prediction_dtype(self):
        """Get `dtype` of the output of `predict()` of the final regressor."""
        x_data = self.get_x_array('all')[0].reshape(1, -1)
        y_pred = self._clf.predict(x_data)
        return y_pred.dtype

    def _get_prediction_properties(self):
        """Get important properties of prediction input."""
        properties = {}
        for attr in ('dataset', 'exp', 'project', 'start_year', 'end_year'):
            attrs = list(group_metadata(self._datasets['label'], attr).keys())
            properties[attr] = attrs[0]
            if len(attrs) > 1:
                if attr == 'start_year':
                    properties[attr] = min(attrs)
                elif attr == 'end_year':
                    properties[attr] = max(attrs)
                else:
                    properties[attr] = '|'.join(attrs)
                logger.debug(
                    "Attribute '%s' of label data is not unique, got values "
                    "%s, using '%s' for prediction cubes", attr, attrs,
                    properties[attr])
        return properties

    def _get_reference_cube(self, datasets, var_type, text=None):
        """Get reference cube for `datasets`."""
        msg = '' if text is None else text
        regular_features = self.features[self.features_types == 'regular']

        for tag in regular_features:
            dataset = self._check_dataset(datasets, var_type, tag, msg)
            if dataset is not None:
                ref_cube = self._load_cube(dataset)
                logger.debug(
                    "For var_type '%s'%s, use reference cube with tag '%s'",
                    var_type, msg, tag)
                logger.debug(ref_cube.summary(shorten=True))
                return ref_cube
        raise ValueError(f"No {var_type} data{msg} without the option "
                         f"'broadcast_from' found")

    def _get_verbosity_parameters(self, function, boolean=False):
        """Get verbosity parameters for class initialization."""
        verbosity_params = {
            'silent': {
                'debug': False,
                'info': False,
                'default': True,
            },
            'verbose': {
                'debug': 1,
                'info': 0,
                'default': 0,
            },
            'verbosity': {
                'debug': 2,
                'info': 1,
                'default': 0,
            },
        }
        parameters = {}
        for (param, log_levels) in verbosity_params.items():
            if param in getfullargspec(function).args:
                parameters[param] = log_levels.get(self._cfg['log_level'],
                                                   log_levels['default'])
                if boolean:
                    parameters[param] = bool(parameters[param])
                logger.debug("Set verbosity parameter '%s' of %s to '%s'",
                             param, str(function), parameters[param])
        return parameters

    def _get_x_data_for_group(self, datasets, var_type, group_attr=None):
        """Get x data for a group of datasets."""
        msg = '' if group_attr is None else f" for '{group_attr}'"
        ref_cube = self._get_reference_cube(datasets, var_type, msg)
        group_data = pd.DataFrame(columns=self.features,
                                  dtype=self._cfg['dtype'])

        # Iterate over all features
        for tag in self.features:
            if self.features_types[tag] != 'coordinate':
                dataset = self._check_dataset(datasets, var_type, tag, msg)

                # No dataset found
                if dataset is None:
                    if var_type == 'prediction_input_error':
                        logger.debug(
                            "Prediction input error of '%s'%s not available, "
                            "setting it to 0.0", tag, msg)
                        new_data = 0.0
                    else:
                        new_data = np.nan

                # Found exactly one dataset
                else:
                    text = f"{var_type} '{tag}'{msg}"

                    # Broadcast if necessary
                    if 'broadcast_from' in dataset:
                        cube = self._get_broadcasted_cube(
                            dataset, ref_cube, text)
                    else:
                        cube = self._load_cube(dataset)
                    self._check_cube_dimensions(cube, ref_cube, text)

                    # Do not accept errors for categorical features
                    if (var_type == 'prediction_input_error'
                            and tag in self.categorical_features):
                        logger.warning(
                            "Specifying prediction input error for "
                            "categorical feature '%s'%s is not possible, "
                            "setting it to 0.0", tag, msg)
                        new_data = np.full(cube.shape, 0.0).ravel()
                    else:
                        new_data = self._get_cube_data(cube)

            # Load coordinate feature data
            else:
                new_data = self._get_coordinate_data(ref_cube, var_type, tag,
                                                     msg)

            # Save data
            group_data[tag] = new_data

        # Return data and reference cube
        return (group_data, ref_cube)

    def _group_by_attributes(self, datasets):
        """Group datasets by specified attributes."""
        attributes = self._cfg.get('group_datasets_by_attributes', [])
        if not attributes:
            if self._cfg.get('accept_only_scalar_data'):
                attributes = ['dataset']
                logger.warning("Automatically set 'group_datasets_by_'"
                               "attributes' to ['dataset'] because 'accept_"
                               "only_scalar_data' is given")
            else:
                for dataset in datasets:
                    dataset['group_attribute'] = None
                return datasets
        for dataset in datasets:
            dataset['group_attribute'] = mlr.create_alias(dataset, attributes)
        logger.info("Grouped feature and label datasets by %s", attributes)
        return datasets

    def _impute_nans(self, data_frame, copy=True):
        """Impute all nans of a `data_frame`."""
        if copy:
            data_frame = data_frame.copy()
        if 'imputer' in self._clf.named_steps:
            transform = self._clf.named_steps['imputer'].transform
            if 'x' in data_frame.columns:
                data_frame.x.values[:] = transform(data_frame.x.values)
            else:
                data_frame.values[:] = transform(data_frame.values)
        return data_frame

    def _is_fitted(self):
        """Check if the MLR models are fitted."""
        if self._clf is None:
            return False
        x_dummy = np.ones((1, self.features.size), dtype=self._cfg['dtype'])
        try:
            self._clf.predict(x_dummy)
        except NotFittedError:
            return False
        return True

    def _is_ready_for_plotting(self):
        """Check if the class is ready for plotting."""
        if not self._is_fitted():
            logger.warning(
                "Plotting not possible, MLR model is not fitted yet")
            return False
        if not self._cfg['write_plots']:
            logger.debug("Plotting not possible, 'write_plots' is set to "
                         "'False' in user configuration file")
            return False
        return True

    def _load_classes(self):
        """Populate self._classes and check for errors."""
        self._classes['group_attributes'] = self._get_group_attributes()
        self._classes['features'] = self._get_features()
        self._classes['label'] = self._get_label()

    def _load_cube(self, dataset):
        """Load iris cube, check data type and convert units if desired."""
        logger.debug("Loading %s", dataset['filename'])
        cube = iris.load_cube(dataset['filename'])

        # Check dtype
        if not np.issubdtype(cube.dtype, np.number):
            raise TypeError(
                f"Data type of cube loaded from '{dataset['filename']}' is "
                f"'{cube.dtype}', the moment only numeric data is supported")

        # Convert dtypes
        cube.data = cube.core_data().astype(self._cfg['dtype'],
                                            casting='same_kind')
        for coord in cube.coords():
            try:
                coord.points = coord.points.astype(self._cfg['dtype'],
                                                   casting='same_kind')
            except TypeError:
                logger.debug(
                    "Cannot convert dtype of coordinate array '%s' from '%s' "
                    "to '%s'", coord.name(), coord.points.dtype,
                    self._cfg['dtype'])

        # Convert and check units
        if dataset.get('convert_units_to'):
            self._convert_units_in_cube(cube, dataset['convert_units_to'])
        if not cube.units == Unit(dataset['units']):
            raise ValueError(
                f"Units of cube '{dataset['filename']}' for "
                f"{dataset['var_type']} '{dataset['tag']}' differ from units "
                f"given in dataset list (retrieved from ancestors or "
                f"metadata.yml), got '{cube.units}' in cube and "
                f"'{dataset['units']}' in dataset list")
        return cube

    def _load_data(self):
        """Load train/test data (features/labels)."""
        (x_all, y_all) = self._extract_features_and_labels()
        self._data['all'] = pd.concat([x_all, y_all], axis=1, keys=['x', 'y'])
        if len(y_all.index) < 2:
            raise ValueError(
                f"Need at least 2 data points for MLR training, got only "
                f"{len(y_all.index)}")
        logger.info("Loaded %i input data point(s)", len(y_all.index))

        # Split train/test data if desired
        test_size = self._cfg['test_size']
        if test_size:
            (self._data['train'], self._data['test']) = self._train_test_split(
                x_all, y_all, test_size)
            for data_type in ('train', 'test'):
                if len(self.data[data_type].index) < 2:
                    raise ValueError(
                        f"Need at least 2 datasets for '{data_type}' data, "
                        f"got {len(self.data[data_type].index)}")
            logger.info(
                "Using %i%% of the input data as test data (%i point(s))",
                int(test_size * 100), len(self.data['test'].index))
            logger.info("%i point(s) remain(s) for training",
                        len(self.data['train'].index))
        else:
            self._data['train'] = self.data['all'].copy()
            logger.info("Using all %i input data point(s) for training",
                        len(y_all.index))

    def _load_final_parameters(self):
        """Load parameters for final regressor from recipe."""
        parameters = self._cfg.get('parameters_final_regressor', {})
        logger.debug("Found parameter(s) for final regressor in recipe: %s",
                     parameters)
        verbosity_params = self._get_verbosity_parameters(self._CLF_TYPE)
        for (param, verbosity) in verbosity_params.items():
            parameters.setdefault(param, verbosity)
        return parameters

    def _load_input_datasets(self, input_data=None):
        """Load input datasets (including ancestors)."""
        if input_data is None:
            logger.debug("Loading input data from 'cfg' argument")
            input_datasets = deepcopy(list(self._cfg['input_data'].values()))
            input_datasets.extend(self._get_ancestor_datasets())
        else:
            logger.debug("Loading input data from 'input_data' argument")
            input_datasets = deepcopy(input_data)
        mlr.datasets_have_mlr_attributes(input_datasets,
                                         log_level='warning',
                                         mode='only_var_type')

        # Training datasets
        feature_datasets = select_metadata(input_datasets, var_type='feature')
        label_datasets = select_metadata(input_datasets, var_type='label')

        # Prediction datasets
        pred_in_datasets = select_metadata(input_datasets,
                                           var_type='prediction_input')
        pred_in_err_datasets = select_metadata(
            input_datasets, var_type='prediction_input_error')
        pred_ref_datasets = select_metadata(input_datasets,
                                            var_type='prediction_reference')

        # Check datasets
        msg = ("At least one '{}' dataset does not have necessary MLR "
               "attributes")
        datasets_to_check = {
            'feature': feature_datasets,
            'label': label_datasets,
            'prediction_input': pred_in_datasets,
            'prediction_input_error': pred_in_err_datasets,
            'prediction_reference': pred_ref_datasets,
        }
        for (label, datasets) in datasets_to_check.items():
            if not mlr.datasets_have_mlr_attributes(datasets,
                                                    log_level='error'):
                raise ValueError(msg.format(label))

        # Check if data was found
        if not feature_datasets:
            raise ValueError("No 'feature' data found")
        if not label_datasets:
            raise ValueError("No 'label' data found")
        if not pred_in_datasets:
            raise ValueError("No 'prediction_input' data found")

        # Convert units
        self._convert_units_in_metadata(feature_datasets)
        self._convert_units_in_metadata(label_datasets)
        self._convert_units_in_metadata(pred_in_datasets)
        self._convert_units_in_metadata(pred_in_err_datasets)
        self._convert_units_in_metadata(pred_ref_datasets)

        # Save datasets
        logger.info(
            "Found %i 'feature' dataset(s), %i 'label' dataset(s), %i "
            "'prediction_input' dataset(s), %i 'prediction_input_error' "
            "dataset(s) and %i 'prediction_reference' datasets(s)",
            len(feature_datasets), len(label_datasets), len(pred_in_datasets),
            len(pred_in_err_datasets), len(pred_ref_datasets))
        labeled_datasets = {
            'Feature': feature_datasets,
            'Label': label_datasets,
            'Prediction input': pred_in_datasets,
            'Prediction input error': pred_in_err_datasets,
            'Prediction output': pred_ref_datasets,
        }
        for (msg, datasets) in labeled_datasets.items():
            logger.debug("%s datasets:", msg)
            logger.debug(pformat([d['filename'] for d in datasets]))
        self._datasets['feature'] = self._group_by_attributes(feature_datasets)
        self._datasets['label'] = self._group_by_attributes(label_datasets)
        self._datasets['prediction_input'] = self._group_prediction_datasets(
            pred_in_datasets)
        self._datasets['prediction_input_error'] = (
            self._group_prediction_datasets(pred_in_err_datasets))
        self._datasets['prediction_reference'] = (
            self._group_prediction_datasets(pred_ref_datasets))

    def _load_skater_interpreters(self):
        """Load :mod:`skater` interpretation modules."""
        x_train = self.get_x_array('train', impute_nans=True)
        y_train = self.get_y_array('train', impute_nans=True)

        # Global interpreter
        self._skater['global_interpreter'] = Interpretation(
            x_train, training_labels=y_train, feature_names=self.features)
        logger.debug("Loaded global skater interpreter with new training data")

        # Local interpreter (LIME)
        verbosity = self._get_verbosity_parameters(LimeTabularExplainer,
                                                   boolean=True)
        for param in verbosity:
            verbosity[param] = False
        categorical_features_idx = [
            int(np.where(self.features == tag)[0][0])
            for tag in self.categorical_features
        ]
        self._skater['local_interpreter'] = LimeTabularExplainer(
            x_train,
            mode='regression',
            training_labels=y_train,
            feature_names=self.features,
            categorical_features=categorical_features_idx,
            class_names=[self.label],
            discretize_continuous=False,
            sample_around_instance=True,
            **verbosity,
        )
        logger.debug("Loaded LIME explainer with new training data")

        # Model
        example_size = min(y_train.size, 20)
        self._skater['model'] = InMemoryModel(
            self._clf.predict,
            feature_names=self.features,
            examples=x_train[:example_size],
            model_type='regressor',
        )
        logger.debug("Loaded skater model with new regressor")

    def _mask_prediction_array(self, y_pred, ref_cube):
        """Apply mask of reference cube to prediction array."""
        mask = np.ma.getmaskarray(ref_cube.data).ravel()
        if y_pred.ndim == 1 and y_pred.shape[0] != mask.shape[0]:
            new_y_pred = np.empty(mask.shape[0], dtype=self._cfg['dtype'])
            new_y_pred[mask] = np.nan
            new_y_pred[~mask] = y_pred
        else:
            new_y_pred = y_pred
        return np.ma.masked_invalid(new_y_pred)

    def _prediction_to_dict(self, pred_out, **kwargs):
        """Convert output of `clf.predict()` to `dict`."""
        if not isinstance(pred_out, (list, tuple)):
            pred_out = [pred_out]
        idx_to_name = {0: None}
        if 'return_var' in kwargs:
            idx_to_name[1] = 'var'
        elif 'return_cov' in kwargs:
            idx_to_name[1] = 'cov'
        pred_dict = {}
        for (idx, pred) in enumerate(pred_out):
            pred = pred.astype(self._cfg['dtype'], casting='same_kind')
            if pred.ndim == 2 and pred.shape[1] == 1:
                logger.warning(
                    "Prediction output is 2D and length of second axis is 1, "
                    "squeezing second axis")
                pred = np.squeeze(pred, axis=1)
            pred_dict[idx_to_name.get(idx, idx)] = pred
        return pred_dict

    def _pred_type_to_metadata(self, pred_type, cube):
        """Get correct :mod:`iris.cube.CubeMetadata` of prediction cube."""
        var_name = cube.var_name
        long_name = cube.long_name
        units = cube.units
        attributes = cube.attributes
        suffix = '' if pred_type is None else f'_{pred_type}'
        if pred_type is None:
            attributes['var_type'] = 'prediction_output'
        elif isinstance(pred_type, int):
            var_name += '_{:d}'.format(pred_type)
            long_name += ' {:d}'.format(pred_type)
            logger.warning("Got unknown prediction type with index %i",
                           pred_type)
            attributes['var_type'] = 'prediction_output_misc'
        elif pred_type == 'var':
            var_name += suffix
            long_name += ' (variance)'
            units = mlr.units_power(cube.units, 2)
            attributes['var_type'] = 'prediction_output_error'
        elif pred_type == 'cov':
            var_name += suffix
            long_name += ' (covariance)'
            units = mlr.units_power(cube.units, 2)
            attributes['var_type'] = 'prediction_output_error'
        elif 'squared_mlr_model_error_estim' in pred_type:
            var_name += suffix
            long_name += (' (squared MLR model error estimation using {})'.
                          format('cross-validation' if 'cv' in
                                 pred_type else 'holdout test data set'))
            units = mlr.units_power(cube.units, 2)
            attributes['var_type'] = 'prediction_output_error'
        elif pred_type == 'squared_propagated_input_error':
            var_name += suffix
            long_name += (' (squared propagated error of prediction input '
                          'estimated by LIME)')
            units = mlr.units_power(cube.units, 2)
            attributes['var_type'] = 'prediction_output_error'
        elif pred_type == 'lime':
            var_name = 'lime_feature_importance'
            long_name = (f'Most important feature for predicting {self.label} '
                         f'given by LIME')
            units = Unit('no_unit')
            attributes['features'] = pformat(dict(enumerate(self.features)))
            attributes['var_type'] = 'prediction_output_misc'
        elif pred_type == 'residual':
            var_name += suffix
            long_name += ' (residual)'
            attributes['residual'] = 'predicted minus true values'
            attributes['var_type'] = 'prediction_residual'
        else:
            logger.warning(
                "Got unknown prediction type '%s', setting correct attributes "
                "not possible", pred_type)
            attributes['var_type'] = 'prediction_output_misc'
        return iris.cube.CubeMetadata(
            standard_name=cube.standard_name,
            long_name=long_name,
            var_name=var_name,
            units=units,
            attributes=attributes,
            cell_methods=cube.cell_methods,
        )

    def _propagate_input_errors(self, x_pred, x_err):
        """Propagate errors from prediction input."""
        logger.info(
            "Propagating prediction input errors using LIME (this may take a "
            "while...)")
        x_pred = self._impute_nans(x_pred)

        # Propagated error for single input
        def _propagated_error(x_single_pred, x_single_err, interpreter,
                              predict_fn, features, categorical_features):
            """Get propagated prediction input error for single input."""
            exp = interpreter.explain_instance(x_single_pred, predict_fn)
            x_single_err = np.nan_to_num(x_single_err)
            x_err_scaled = x_single_err / interpreter.scaler.scale_
            squared_error = 0.0
            for (idx, coef) in exp.local_exp[1]:
                if features[idx] in categorical_features:
                    continue
                squared_error += (x_err_scaled[idx] * coef)**2
            return squared_error

        # Apply on whole input (using multiple processes)
        _propagated_error = partial(
            _propagated_error,
            interpreter=self._skater['local_interpreter'],
            predict_fn=self._clf.predict,
            features=self.features,
            categorical_features=self.categorical_features,
        )
        pool = mp.ProcessPool(processes=self._cfg['n_jobs'])
        return np.array(pool.map(_propagated_error, x_pred.values,
                                 x_err.values),
                        dtype=self._cfg['dtype'])

    def _remove_missing_features(self, x_data, y_data):
        """Remove missing values in the features data (if desired)."""
        if self._cfg['imputation_strategy'] != 'remove':
            return (x_data, y_data)
        mask = x_data.isnull().any(axis=1).values
        x_data = x_data[~mask].reset_index(drop=True)
        y_data = y_data[~mask].reset_index(drop=True)
        diff = mask.shape[0] - len(y_data.index)
        if diff:
            msg = ('Removed %i training point(s) where features were '
                   'missing')
            if self._cfg.get('accept_only_scalar_data'):
                removed_groups = self.group_attributes[mask]
                msg += f' ({removed_groups})'
                self._classes['group_attributes'] = (
                    self.group_attributes[~mask])
            logger.info(msg, diff)
        return (x_data, y_data)

    def _remove_missing_pred_input(self, x_pred, x_err=None, y_ref=None):
        """Remove missing values in the prediction input data."""
        if self._cfg['imputation_strategy'] != 'remove':
            return (x_pred, x_err, y_ref)
        mask = x_pred.isnull().any(axis=1).values
        x_pred = x_pred[~mask].reset_index(drop=True)
        if x_err is not None:
            x_err = x_err[~mask].reset_index(drop=True)
        if y_ref is not None:
            y_ref = y_ref[~mask].reset_index(drop=True)
        diff = mask.shape[0] - len(x_pred.index)
        if diff:
            logger.info(
                "Removed %i prediction input point(s) where features were "
                "missing'", diff)
        return (x_pred, x_err, y_ref)

    def _save_prediction_cubes(self, pred_dict, pred_name, x_cube):
        """Save (multi-dimensional) prediction output."""
        logger.debug("Creating output cubes")
        for (pred_type, y_pred) in pred_dict.items():
            y_pred = self._mask_prediction_array(y_pred, x_cube)
            if y_pred.size == np.prod(x_cube.shape, dtype=np.int):
                pred_cube = x_cube.copy(y_pred.reshape(x_cube.shape))
            else:
                dim_coords = []
                for (dim_idx, dim_size) in enumerate(y_pred.shape):
                    dim_coords.append((iris.coords.DimCoord(
                        np.arange(dim_size, dtype=np.float64),
                        long_name=f'MLR prediction index {dim_idx}',
                        var_name=f'idx_{dim_idx}'), dim_idx))
                pred_cube = iris.cube.Cube(y_pred,
                                           dim_coords_and_dims=dim_coords)
            new_path = self._set_prediction_cube_attributes(
                pred_cube, pred_type, pred_name=pred_name)
            io.iris_save(pred_cube, new_path)

    def _save_csv_file(self, data_type, filename, pred_name=None):
        """Save CSV file."""
        if data_type not in self.data:
            return
        if data_type == 'pred':
            csv_data = self.data[data_type][pred_name]
        else:
            csv_data = self.data[data_type]

        # Filename and path
        if filename is None:
            if data_type == 'pred':
                filename = '{data_type}_{pred_name}.csv'
                format_kwargs = {
                    'data_type': data_type,
                    'pred_name': self._get_name(pred_name),
                }
            else:
                filename = '{data_type}.csv'
                format_kwargs = {'data_type': data_type}
        filename = filename.format(**format_kwargs)
        path = os.path.join(self._cfg['mlr_work_dir'], filename)

        # Save file
        csv_data.to_csv(path, na_rep='nan')
        logger.info("Wrote %s", path)

    def _set_prediction_cube_attributes(self, cube, pred_type, pred_name=None):
        """Set the attributes of the prediction cube."""
        cube.attributes = {
            'description': 'MLR model prediction',
            'mlr_model_name': self._cfg['model_name'],
            'mlr_model_type': self._MODEL_TYPE,
            'final_regressor': str(self._CLF_TYPE),
            'prediction_name': self._get_name(pred_name),
            'tag': self.label,
        }
        cube.attributes.update(self._get_prediction_properties())
        for (key, val) in self.parameters.items():
            cube.attributes[key] = str(val)
        label_cube = self._load_cube(self._datasets['label'][0])
        for attr in ('standard_name', 'var_name', 'long_name', 'units'):
            setattr(cube, attr, getattr(label_cube, attr))

        # Modify cube metadata depending on prediction type
        cube.metadata = self._pred_type_to_metadata(pred_type, cube)

        # Get new path
        suffix = '' if pred_type is None else f'_{pred_type}'
        pred_str = f'_{self._get_name(pred_name)}'
        root_str = ('' if self._cfg['root_dir'] == '' else
                    f"_for_{self._cfg['root_dir']}")
        filename = (f'{self._MODEL_TYPE}_{self.label}_prediction{suffix}'
                    f'{pred_str}{root_str}.nc')
        new_path = os.path.join(self._cfg['mlr_work_dir'], filename)
        cube.attributes['filename'] = new_path
        return new_path

    def _train_test_split(self, x_data, y_data, test_size):
        """Split data into training and test data."""
        (x_train, x_test, y_train,
         y_test) = train_test_split(x_data.values,
                                    y_data.values,
                                    test_size=test_size)
        x_train = pd.DataFrame(x_train, columns=self.features)
        x_test = pd.DataFrame(x_test, columns=self.features)
        y_train = pd.DataFrame(y_train, columns=[self.label])
        y_test = pd.DataFrame(y_test, columns=[self.label])
        train = pd.concat([x_train, y_train], axis=1, keys=['x', 'y'])
        test = pd.concat([x_test, y_test], axis=1, keys=['x', 'y'])
        return (train, test)

    def _update_fit_kwargs(self, fit_kwargs):
        """Check and update fit kwargs."""
        new_fit_kwargs = {}
        for (param_name, param_val) in fit_kwargs.items():
            step = param_name.split('__')[0]
            if step in self._clf.named_steps:
                new_fit_kwargs[param_name] = param_val
            else:
                logger.warning("Got invalid parameter for fit function: '%s'",
                               param_name)
        return new_fit_kwargs

    @staticmethod
    def _convert_units_in_cube(cube, new_units, power=None, text=None):
        """Convert units of cube if possible."""
        msg = '' if text is None else f' of {text}'
        if isinstance(new_units, str):
            new_units = Unit(new_units)
        if power:
            logger.debug("Raising target units of cube '%s' by power of %i",
                         cube.summary(shorten=True), power)
            new_units = mlr.units_power(new_units, power)
        logger.debug("Converting units%s from '%s' to '%s'", msg, cube.units,
                     new_units)
        try:
            cube.convert_units(new_units)
        except ValueError:
            logger.warning("Units conversion%s from '%s' to '%s' failed", msg,
                           cube.units, new_units)

    @staticmethod
    def _convert_units_in_metadata(datasets):
        """Convert units of datasets if desired."""
        for dataset in datasets:
            if not dataset.get('convert_units_to'):
                continue
            units_from = Unit(dataset['units'])
            units_to = Unit(dataset['convert_units_to'])
            try:
                units_from.convert(0.0, units_to)
            except ValueError:
                logger.warning(
                    "Cannot convert units of %s '%s' from '%s' to '%s'",
                    dataset['var_type'], dataset['tag'], units_from, units_to)
                dataset.pop('convert_units_to')
            else:
                dataset['units'] = dataset['convert_units_to']

    @staticmethod
    def _get_coordinate_data(ref_cube, var_type, tag, text=None):
        """Get coordinate variable `ref_cube` which can be used as x data."""
        msg = '' if text is None else text
        if var_type == 'prediction_input_error':
            logger.debug(
                "Prediction input error of coordinate feature '%s'%s is set "
                "to 0.0", tag, msg)
            return 0.0
        try:
            coord = ref_cube.coord(tag)
        except iris.exceptions.CoordinateNotFoundError:
            raise iris.exceptions.CoordinateNotFoundError(
                f"Coordinate '{tag}' given in 'coords_as_features' not found "
                f"in reference cube for '{var_type}'{msg}")
        coord_array = np.ma.filled(coord.points, np.nan)
        coord_dims = ref_cube.coord_dims(coord)
        if coord_dims == ():
            logger.warning(
                "Coordinate '%s' is scalar, including it as feature does not "
                "add any information to the model (array is constant)", tag)
        else:
            new_axis_pos = np.delete(np.arange(ref_cube.ndim), coord_dims)
            for idx in new_axis_pos:
                coord_array = np.expand_dims(coord_array, idx)
        coord_array = np.broadcast_to(coord_array, ref_cube.shape)
        logger.debug("Added coordinate %s '%s'%s", var_type, tag, msg)
        return coord_array.ravel()

    @staticmethod
    def _get_cube_data(cube):
        """Get data from cube."""
        cube_data = np.ma.filled(cube.data, np.nan)
        return cube_data.ravel()

    @staticmethod
    def _get_name(string):
        """Convert `None` to `str` if necessary."""
        return 'unnamed' if string is None else string

    @staticmethod
    def _get_residuals(y_pred, y_true):
        """Calculate residuals (predicted minus true values)."""
        logger.info("Calculating residuals")
        return y_pred - y_true

    @staticmethod
    def _group_prediction_datasets(datasets):
        """Group prediction datasets (use `prediction_name` key)."""
        for dataset in datasets:
            dataset['group_attribute'] = None
        return group_metadata(datasets, 'prediction_name')

    @staticmethod
    def _remove_missing_labels(x_data, y_data):
        """Remove missing values in the label data."""
        mask = y_data.isnull().values
        new_x_data = x_data[~mask].reset_index(drop=True)
        new_y_data = y_data[~mask].reset_index(drop=True)
        diff = len(y_data.index) - len(new_y_data.index)
        if diff:
            logger.info(
                "Removed %i training point(s) where labels were missing", diff)
        return (new_x_data, new_y_data)
