from abc import ABC, abstractmethod

import numpy as np
import pandas as pd


class Buffer(ABC):
    """deque-like buffer for pandas dataframes and numpy arrays"""

    def __init__(self, maxlen, cols):
        if maxlen is not None and maxlen < 1:
            raise ValueError("Length of buffer has to be at least 1")
        self.maxlen = maxlen
        self.cols = cols
        self._data = self._empty()

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
    def __init__(self, maxlen, n_cols):
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
