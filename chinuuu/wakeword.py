from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Callable

from .errors import ToolError


class WakeWordListener:
    """Optional local wake-word detector using a user-supplied ONNX model.

    Microphone capture is continuous only while this object is explicitly started.
    No model is bundled or downloaded by Chinuuu.
    """

    def __init__(self, model_path: Path, threshold: float = 0.55) -> None:
        self.model_path = model_path.resolve()
        self.threshold = max(0.1, min(float(threshold), 0.99))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._stream = None

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, on_detected: Callable[[str, float], None]) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self.model_path.is_file() or self.model_path.suffix.lower() != ".onnx":
            raise ToolError(
                f"A local ONNX wake-word model is required: {self.model_path}. "
                "Chinuuu does not download wake-word models automatically."
            )
        try:
            import numpy as np  # type: ignore[import-not-found]
            import sounddevice as sd  # type: ignore[import-not-found]
            from openwakeword.model import Model  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ToolError("Wake-word detection requires openwakeword, sounddevice and numpy. Install the voice extra.") from exc

        self._stop.clear()
        frames: queue.Queue = queue.Queue(maxsize=20)
        try:
            model = Model(wakeword_models=[str(self.model_path)], inference_framework="onnx")
        except Exception as exc:
            raise ToolError(f"Could not load the local wake-word model: {exc}") from exc

        def audio_callback(indata, frame_count, time_info, status):
            if status:
                return
            chunk = (indata[:, 0] * 32767).astype(np.int16, copy=True)
            try:
                frames.put_nowait(chunk)
            except queue.Full:
                # Drop the oldest frame rather than blocking the audio callback.
                try:
                    frames.get_nowait()
                    frames.put_nowait(chunk)
                except queue.Empty:
                    pass

        try:
            self._stream = sd.InputStream(samplerate=16000, channels=1, dtype="float32", blocksize=1280, callback=audio_callback)
            self._stream.start()
        except Exception as exc:
            raise ToolError(f"Could not start the microphone stream: {exc}") from exc

        def worker():
            last_detection = 0.0
            try:
                while not self._stop.is_set():
                    try:
                        chunk = frames.get(timeout=0.25)
                    except queue.Empty:
                        continue
                    scores = model.predict(chunk)
                    if not scores:
                        continue
                    label, score = max(scores.items(), key=lambda item: float(item[1]))
                    confidence = float(score)
                    if confidence >= self.threshold and time.monotonic() - last_detection >= 3.0:
                        last_detection = time.monotonic()
                        try:
                            on_detected(str(label), confidence)
                        except Exception:
                            pass
            finally:
                self.stop()

        self._thread = threading.Thread(target=worker, name="ChinuuuWakeWord", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        # Keep a live thread reference if inference has not returned yet. This
        # prevents a second microphone stream from starting during a slow stop.
        if thread is None or not thread.is_alive() or thread is threading.current_thread():
            self._thread = None
