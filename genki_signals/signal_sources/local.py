import os
import wave

from genki_signals.buffers import DataBuffer
from genki_signals.signal_sources.base import SignalSource, SamplerBase
import numpy as np


class MouseSignalSource(SignalSource):
    def __init__(self):
        import pynput

        self.mouse = pynput.mouse.Controller()

    def __call__(self, t):
        return np.array(self.mouse.position)


class KeyboardSignalSource(SignalSource):
    def __init__(self, keys):
        import pynput

        self.keys = keys
        self.listener = pynput.keyboard.Listener(
            on_press=self.on_press, on_release=self.on_release
        )
        self.is_pressing = None

    def on_press(self, key):
        key_name = (
            str(key).replace("'", "").split(".")[-1]
        )  # transforms keyboard.Key and keyboard.KeyCode to strings
        if key_name in self.is_pressing:
            self.is_pressing[key_name] = 1

    def on_release(self, key):
        key_name = (
            str(key).replace("'", "").split(".")[-1]
        )  # transforms keyboard.Key and keyboard.KeyCode to strings
        if key_name in self.is_pressing:
            self.is_pressing[key_name] = 0

    def start(self):
        self.is_pressing = {k: 0 for k in self.keys}
        self.listener.start()

    def stop(self):
        self.listener.stop()
        self.listener.join()

    def __call__(self, t):
        return {f"pressing_{key}": value for key, value in self.is_pressing.items()}

    def __repr__(self):
        return f"KeyboardSignalSource({self.keys})"


class CameraSignalSource(SignalSource):
    """
    A class to use a camera as a secondary SignalSource.
    The recorded frames are in RGB format and have shape (1, height, width, 3)
    """

    def __init__(self, camera_id=0, resolution=(640, 480)):
        super().__init__()
        import cv2

        self.cv = cv2

        self.camera_id = camera_id
        self.resolution = resolution

        self.cap = self.cv.VideoCapture(self.camera_id)
        self.last_frame = None
        self.signal_names = ["image"]

    def start(self):
        self.cap.open(self.camera_id)

    def stop(self):
        self.cap.release()

    def __call__(self, t):
        ret, frame = self.cap.read()
        if ret:
            frame = self.cv.resize(frame, self.resolution)
            self.last_frame = frame
            return {"image": frame}
        else:
            return {"image": self.last_frame}


class MicSignalSource(SamplerBase):
    """Primary data source to read data from microphone."""

    def __init__(self, chunk_size=1024):
        import pyaudio

        self.pa = pyaudio.PyAudio()
        self.mic_info = self.pa.get_default_input_device_info()
        self.sample_rate = int(self.mic_info["defaultSampleRate"])
        self.format = pyaudio.paInt16
        self.n_channels = (self.mic_info["maxInputChannels"])
        self.sample_width = (self.pa.get_sample_size(self.format))
        self.chunk_size = chunk_size
        self.stream = None
        self.buffer = DataBuffer(maxlen=None)
        self.is_active = False
        self.signal_names = ["audio"]
        self.wavefile = None

    def start(self):

        self.stream = self.pa.open(
            format=self.format,
            channels=self.mic_info["maxInputChannels"],
            rate=self.sample_rate,
            input=True,
            frames_per_buffer=self.chunk_size,
            stream_callback=self.receive,
        )
        self.stream.start_stream()
        self.is_active = True

    def stop(self):
        self.stream.stop_stream()
        self.stream.close()
        self.is_active = False

    def receive(self, in_data, frame_count, time_info, status):
        # TODO: Use the info from other params somehow (particularly time_info)
        from pyaudio import paContinue

        data = np.frombuffer(in_data, dtype=np.int16)
        self.buffer.extend({"audio": data})
        return in_data, paContinue

    def read(self):
        value = self.buffer.copy()
        self.buffer.clear()
        return value
