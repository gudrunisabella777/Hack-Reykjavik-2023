from __future__ import annotations

import logging
from collections import deque
from pathlib import Path
from typing import Callable

import joblib
import numpy as np
import pandas as pd
import torch
from more_itertools import zip_equal

from genki_signals.buffers import PandasBuffer, NumpyBuffer
from genki_signals.is_touching_models import StateGruInferenceOnly
from genki_signals.signals.windowed import upsample
from genki_signals.signals.base import Signal


logger = logging.getLogger(__name__)


class Inference(Signal):
    """
    Run real-time inference using a torch model
    """

    def __init__(self, model, input_signals, stateful, name, init_state=None):
        self.name = name
        self.model = model
        self.input_names = input_signals
        self.stateful = stateful
        self.state = init_state

    def __call__(self, *inputs):
        to_concat = []
        for col_data in inputs:
            if col_data.ndim == 1:
                to_concat.append(col_data[:, None])
            else:
                to_concat.append(col_data)
        arr = np.concatenate(to_concat).T
        tns = torch.from_numpy(arr).float()
        if self.stateful:
            inferred_tns, self.state = self.model.inference(tns, self.state)
        else:
            inferred_tns = self.model.inference(tns)
        return inferred_tns.numpy().T


def _model_path(path: Path) -> Path:
    return path / "model.joblib"


def _config_path(path: Path) -> Path:
    return path / "config.json"


def load_model(base_path: Path | str) -> tuple:
    """
    loads the model.joblib file from the given base_path in the hierarchy:

    base_path
    |-- model.joblib
    |-- config.json

    returns a tuple of (pipeline, classifier, config)
    """
    path = Path(base_path)
    return joblib.load(_model_path(path))


class WindowedModel(Signal):
    def __init__(
            self,
            win_size: int,
            hop_length: int,
            lookback_length: int,
            output_names: list[str],
            preprocessing: Callable[[pd.DataFrame], np.ndarray],
            predict: Callable[[np.ndarray], np.ndarray],
    ):
        """WindowedModel is a signal that gets predictions from a model.

        It consists of three buffers:
            'data buffer': gathers data until it reaches the window size,
                then that data is preprocessed and fed to the lookback buffer

            'lookback buffer': gathers preprocessed data until it reaches the
                lookback length, then that data is fed to the classifier, the predictions
                upsampled and fed to the prediction buffer

            'prediction buffer': contains the final predictions from the model

        Args:
            win_size: the window size of the data buffer
            hop_length: how many samples we shift the window by each time
            lookback_length: how many windows we look back to get the input to the classifier
            output_names: the names of the outputs of the classifier
            preprocessing: a function which prepares the raw data for the classifier
            predict: the predict method of the given classifier

        Returns:
            pandas.DataFrame: The probabilities of each class and the predicted class, i.e. the one
            with the highest probability.
        """

        self.name = "windowed_model"

        self.win_size = win_size
        self.hop_length = hop_length
        self.lookback_length = lookback_length
        self.output_names = output_names

        self.data_buffer = PandasBuffer(maxlen=None, cols=None)
        self.lookback_buffer = NumpyBuffer(maxlen=None, n_cols=None)
        self.prediction_buffer = deque([0.0] * self.win_size, maxlen=None)

        self.preprocessing = preprocessing
        self.predict = predict

    @classmethod
    def from_sklearn_model(cls, model_path: str):
        """Load a model with sklearn pipeline and classifier"""
        pipeline, clf, config = load_model(model_path)

        if hasattr(clf, "verbose"):
            clf.verbose = 0

        return cls(
            win_size=config["win_size"],
            hop_length=config["hop_length"],
            lookback_length=config["lookback_length"],
            output_names=config["output_names"],
            preprocessing=lambda x: pipeline.fit_transform([x]),
            predict=lambda x: clf.predict_proba(x.reshape(1, -1)),
        )

    @classmethod
    def from_torch_model(cls, model_path: str):
        """Load a model with sklearn pipeline and torch classifier"""
        pipeline, clf, config = load_model(model_path)
        import torch

        if hasattr(clf, "verbose"):
            clf.verbose = 0

        return cls(
            win_size=config["win_size"],
            hop_length=config["hop_length"],
            lookback_length=config["lookback_length"],
            output_names=config["output_names"],
            preprocessing=lambda x: pipeline.transform([x]),
            predict=lambda x: clf.predict(torch.from_numpy(x)).detach().numpy(),
        )

    def __call__(self, data):
        """
        Feeds the raw input data to the aforementioned buffers. All predictions are 0 until
        the lookback buffer has enough data.
        """
        self.data_buffer.extend(data)
        while len(self.data_buffer) >= self.win_size:
            data_cur = self.data_buffer.view(self.win_size)
            data_cur = self.preprocessing(data_cur)
            self.lookback_buffer.extend(data_cur)
            self.data_buffer.popleft(self.hop_length)

        while len(self.lookback_buffer) >= self.lookback_length:
            x = self.lookback_buffer.view(
                self.lookback_length
            )  # (lookback_length, num_features)
            self.lookback_buffer.popleft(1)
            y = self.predict(x)  # (1, num_classes)
            y_upsampled = [
                              y[0]
                          ] * self.hop_length  # one prediction per window, upsample to each sample
            self.prediction_buffer.extend(y_upsampled)

        preds_cur = [
            self.prediction_buffer.popleft()
            if self.prediction_buffer
            else [0] * len(self.output_names)
            for _ in range(len(data))
        ]
        # need the if statement since predictions are only made after the lookback buffer is big enough

        if len(self.prediction_buffer) + len(self.data_buffer) > self.win_size:
            logger.warning(
                "Predictions out of sync, prediction_buffer contains old outputs"
            )

        y_pred = np.argmax(preds_cur, axis=1)
        return pd.DataFrame(
            data=np.c_[preds_cur, y_pred], columns=self.output_names + ["y_pred"]
        )


class WindowedModelTorch(Signal):
    """Runs `StateGruInferenceOnly` in real time

    Note that this returns multiple outputs as a dictionary, the actual predication and the probabilites
    for each class as returned by the model
    """

    def __init__(self, ckpt_path: str | Path):
        self.name = "is_touching_torch"
        self.input_names = ["acc", "gyro"]

        self.model = StateGruInferenceOnly.load_from_checkpoint(ckpt_path)
        self.model.freeze()

        self.win_size = self.model.win_size
        self.hop_length = self.model.hop_len
        self.output_names = [f"{self.name}_{o}" for o in self.model.classes]
        self.outputs = [self.name] + self.output_names

        self.data_buffer = NumpyBuffer(maxlen=None, n_cols=None)
        self.prediction_buffer = NumpyBuffer(maxlen=None, n_cols=len(self.output_names))
        self.prediction_buffer.extend(np.zeros((self.win_size, len(self.output_names))))

    def __call__(self, acc, gyro):
        data = np.concatenate([acc, gyro], axis=-1)
        self.data_buffer.extend(data)

        while len(self.data_buffer) >= self.win_size:
            data_cur = self.data_buffer.view(self.win_size)
            self.data_buffer.popleft(self.hop_length)

            x = torch.as_tensor(data_cur).float().transpose(1, 0).unsqueeze(0)
            y = self.model.predict(x)

            # TODO(robert): See if we can just run this on a low frequency using the new system capabilites?
            y_upsampled = upsample(
                y.numpy(), self.hop_length
            )  # one prediction per window, upsample to each sample
            self.prediction_buffer.extend(y_upsampled)

        preds_cur = self.prediction_buffer.popleft(len(data))
        preds_cur_dict = dict(zip_equal(self.output_names, preds_cur.T))

        out = {self.name: np.argmax(preds_cur, axis=1), **preds_cur_dict}
        assert len(out) == len(
            self.outputs
        ), "Expected the number of current outputs and the defined outputs to be eq."
        return out

__all__ = [
    "Inference",
    "WindowedModel",
    "WindowedModelTorch",
    ]
