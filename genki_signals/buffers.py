from abc import ABC, abstractmethod
from collections.abc import MutableMapping
import re

import numpy as np
import pandas as pd
import bqplot as bq


def _slice(data, length, end=True):
    if end:
        return {k: v[-length:] for k, v in data.items()}
    else:
        return {k: v[:length] for k, v in data.items()}


class DataBuffer(MutableMapping):
    # TODO: Write tests for this, and performance tests, replace all buffers and ArrayFrame
    def __init__(self, max_size=None, data=None):
        if max_size is not None and max_size < 1:
            raise ValueError("Length of buffer has to be at least 1")
        self.max_size = max_size
        self.data = data if data is not None else {}
        self.charts = []
        if self.max_size is not None and len(self) > max_size:
            self.data = _slice(self.data, self.max_size, end=True)

    def __len__(self):
        if self.data == {}:
            return 0
        return len(next(iter(self.data.values())))

    def __getitem__(self, k):
        if k not in self.keys():
            m = re.match(r"(.+)_(\d+)", k)
            if m is not None:
                key, index = m.groups()
                return self.data[key][:, int(index)]
            else:
                raise KeyError(f"Key {k} not found in {self.keys()}")
        return self.data[k]

    def __setitem__(self, key, value):
        assert len(value) == len(self), f"Length of value must be the same as the buffer, {len(value)=} != {len(self)=}"
        self.data[key] = value

    def __delitem__(self, v):
        del self.data[v]

    def __iter__(self):
        return iter(self.data)

    def _init_cols_if_needed(self, data):
        if len(self) == 0:
            self.data = {k: np.empty((0, *v.shape[1:])) for k, v in data.items()}

    def _validate(self, data):
        assert set(self.keys()) == set(data.keys()), f"Data keys must be the same, {self.keys()=} != {data.keys()=}"
        try:
            len_added = len(next(iter(data.values())))
        except StopIteration as e:
            print(data)
            raise e
        assert all(len(v) == len_added for v in data.values()), f"Data values must have same length, {data.values()}"

    def _concat(self, data_list):
        return {k: np.concatenate([d[k] for d in data_list]) for k in self.keys()}

    def extend(self, data):
        """Appends data to the buffer and pops off and slices s.t. the length matches maxlen"""
        if len(data) == 0:
            return
        self._init_cols_if_needed(data)
        self._validate(data)
        self.data = self._concat([self.data, data])
        if self.max_size is not None:
            self.data = _slice(self.data, self.max_size, end=True)
        self._update_charts()

    def append(self, pt):
        pts = {k: np.array([v]) for k, v in pt.items()}
        self.extend(pts)

    def plot(self, key, plot_type="chart", **kwargs):
        if plot_type == "chart":
            return self._plot_line_chart(key, **kwargs)
        elif plot_type == "spectrogram":
            return self._plot_spectrogram(key, **kwargs)
        elif plot_type == "trace2D":
            return self._plot_trace(key, **kwargs)

    def _update_charts(self):
        for chart in self.charts:
            if chart["type"] == "line":
                self._update_line_chart(chart)
            elif chart["type"] == "spectrogram":
                self._update_spectrogram(chart)
            elif chart["type"] == "trace2D":
                self._update_trace(chart)

    def _plot_line_chart(self, key, x_key=None):
        xs = bq.LinearScale()
        ys = bq.LinearScale()
        xax = bq.Axis(scale=xs, label="t")
        yax = bq.Axis(scale=ys, orientation="vertical", label=key)
        line = bq.Lines(x=np.arange(len(self[key])), y=self[key].T, scales={"x": xs, "y": ys})
        fig = bq.Figure(marks=[line], axes=[xax, yax])
        chart_obj = {
            "type": "line",
            "key": key,
            "line": line,
            "x_key": x_key
        }
        # Call _update_line_chart first, if there is an error
        # we won't add the chart to self.charts
        self._update_line_chart(chart_obj)
        self.charts.append(chart_obj)
        return fig

    def _update_line_chart(self, chart):
        key = chart["key"]
        line = chart["line"]
        x_key = chart["x_key"]
        if x_key is None:
            line.x = np.arange(len(self[key]))
        else:
            line.x = self[x_key]
        line.y = self[key].T

    def _plot_spectrogram(self, key, **kwargs):
        xs = bq.LinearScale()
        ys = bq.LinearScale()
        xax = bq.Axis(scale=xs, label="Hz")
        yax = bq.Axis(scale=ys, orientation="vertical", label="db")
        # data is assumed to be complex-valued array of shape (t, f)
        # We only use the latest point, and simply plot the magnitude
        line = bq.Lines(x=[], y=[], scales={"x": xs, "y": ys})
        chart_obj = {
            "type": "spectrogram",
            "key": key,
            "line": line,
            "sample_rate": kwargs.get("sample_rate", 1),  # Is there a sensible default?
            "window_size": kwargs.get("window_size", 1)  # Is there a sensible default?
        }
        fig = bq.Figure(marks=[line], axes=[xax, yax])
        # Call _update_spectrogram first, if there is an error
        # we won't add the chart to self.charts
        self._update_spectrogram(chart_obj)
        self.charts.append(chart_obj)
        return fig

    def _update_spectrogram(self, chart):
        key = chart["key"]
        line = chart["line"]
        sample_rate = chart["sample_rate"]
        window_size = chart["window_size"]
        data = self[key][-1]
        omega = np.fft.rfftfreq(window_size, 1 / sample_rate)
        line.x = omega
        line.y = 10 * np.log10(np.maximum(np.abs(data), 1e-20))

    def _plot_trace(self, key):
        xs = bq.LinearScale()
        ys = bq.LinearScale()
        xax = bq.Axis(scale=xs, label="x")
        yax = bq.Axis(scale=ys, orientation="vertical", label="y")
        line = bq.Lines(x=[], y=[], scales={"x": xs, "y": ys})
        fig = bq.Figure(marks=[line], axes=[xax, yax])
        chart_obj = {
            "type": "trace2D",
            "key": key,
            "line": line
        }
        # Call _update_line_chart first, if there is an error
        # we won't add the chart to self.charts
        self._update_trace(chart_obj)
        self.charts.append(chart_obj)
        return fig

    def _update_trace(self, chart):
        key = chart["key"]
        line = chart["line"]
        line.x = self[key][:, 0]
        line.y = -self[key][:, 1]

    def keys(self):
        return self.data.keys()

    def clear(self):
        self.data = {}

    def __copy__(self):
        return DataBuffer(self.max_size, self.data.copy())

    def copy(self):
        return self.__copy__()

    @classmethod
    def from_dataframe(cls, df, max_size=None):
        return cls(max_size=max_size, data={k: df[k].values for k in df.columns})


class Buffer(ABC):
    """deque-like buffer for pandas dataframes and numpy arrays"""

    def __init__(self, maxlen, cols):
        if maxlen is not None and maxlen < 1:
            raise ValueError("Length of buffer has to be at least 1")
        self.maxlen = maxlen
        self.cols = cols
        self._data = self._empty()
        self.chart_lines = []

    @abstractmethod
    def _init_cols_if_needed(self, data):
        # If columns are set at runtime
        pass

    @abstractmethod
    def _empty(self):
        """Returns an empty element"""
        pass

    @abstractmethod
    def _validate(self, data):
        """Validate the shape of the elements"""
        pass

    @abstractmethod
    def _concat(self, data_list):
        """How to concatenate multiple elements"""
        pass

    @abstractmethod
    def _slice(self, data, n, end=False):
        """How to slice the elements, e.g. for a numpy array x[:n]"""
        pass

    def __len__(self):
        return len(self._data)

    def view(self, n=None):
        """View the buffer"""
        return self._data if n is None else self._slice(self._data, n)

    def extend(self, data):
        """Appends data to the buffer and pops off and slices s.t. the length matches maxlen"""
        self._init_cols_if_needed(data)
        self._validate(data)
        self._data = self._concat([self._data, data])

        if self.maxlen is None:
            return

        self._data = self._slice(self._data, self.maxlen, end=True)

    def popleft(self, n):
        """Pops n elements from the left of the buffer.

        If the user asks for more elements than there are on the buffer it will return all of the buffer except if
        the buffer is empty it raises an error
        """
        if len(self._data) == 0:
            raise IndexError("Pop from empty buffer")
        data_out = self._slice(self._data, n)
        n_to_keep = len(self._data) - n
        self._data = self._slice(self._data, n_to_keep, end=True) if n_to_keep > 0 else self._empty()
        return data_out

    def popleft_all(self):
        return self.popleft(len(self))

    def __repr__(self):
        return f"{self.__class__.__name__}({self.maxlen, self.cols})"


class PandasBuffer(Buffer):
    def _empty(self):
        return pd.DataFrame()

    def _init_cols_if_needed(self, data):
        if self.cols is None:
            self.cols = data.columns

    def _validate(self, data):
        assert set(data.columns) == set(self.cols), f"Expected the same columns. Got {data.columns=} and {self.cols}"

    def _slice(self, data, n, end=False):
        return data.iloc[-n:] if end else data.iloc[:n]

    def _concat(self, data_list):
        return pd.concat(data_list, axis=0)


class NumpyBuffer(Buffer):
    def __init__(self, maxlen, n_cols=None):
        if isinstance(n_cols, int):
            n_cols = (n_cols,)
        self.cols = n_cols
        super().__init__(maxlen, n_cols)

    def _init_cols_if_needed(self, data):
        if self.cols is None:
            self.cols = data.shape[1:]

    def _empty(self):
        if self.cols is None:
            return np.empty((0, 0))
        return np.empty((0, *self.cols))

    def _validate(self, data):
        assert data.shape[1:] == self.cols, "Expected a fixed number of cols to be able to concatenate"

    def _slice(self, data, n, end=False):
        return data[-n:] if end else data[:n]

    def _concat(self, data_list):
        return np.concatenate([d for d in data_list if d.size != 0], axis=0)


class NumpyBufferResample(NumpyBuffer):
    """A numpy buffer that picks out (end emits) every n sample"""

    def __init__(self, maxlen, n_cols, every_n_samples):
        super().__init__(maxlen, n_cols)
        self._every_n_samples = every_n_samples
        self._n_seen = 0

    def extend(self, data):
        """Appends downsampled data to the buffer and pops off and slices s.t. the length matches maxlen"""
        self._init_cols_if_needed(data)
        self._validate(data)

        emit_data = self._every_n_samples < (self._n_seen + len(data)) or self._n_seen == 0
        if emit_data:
            start_point = (self._every_n_samples - self._n_seen) % self._every_n_samples
            d_out = data[start_point :: self._every_n_samples]
            self._data = self._concat([self._data, d_out])
        self._n_seen = (self._n_seen + len(data)) % self._every_n_samples

        if self.maxlen is None:
            return

        self._data = self._slice(self._data, self.maxlen, end=True)