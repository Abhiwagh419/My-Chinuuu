from __future__ import annotations

import tempfile
from pathlib import Path

from .errors import ToolError


class OfflineVoiceEngine:
    """Optional offline speech engine. It never starts continuous microphone capture."""

    def __init__(self, model_path: Path) -> None:
        self.model_path = model_path.resolve()
        self._whisper_model = None

    @staticmethod
    def _asr_dependencies():
        try:
            import sounddevice as sd  # type: ignore[import-not-found]
            import soundfile as sf  # type: ignore[import-not-found]
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ToolError(
                "Offline speech recognition dependencies are missing. Install with: python -m pip install -e '.[voice]'."
            ) from exc
        return sd, sf, WhisperModel

    @staticmethod
    def _tts_dependency():
        try:
            import pyttsx3  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ToolError(
                "Offline text-to-speech requires pyttsx3. Install with: python -m pip install -e '.[voice]'."
            ) from exc
        return pyttsx3

    def record_and_transcribe(self, seconds: int = 6, sample_rate: int = 16000) -> str:
        if type(seconds) is not int or not 2 <= seconds <= 20:
            raise ToolError("Push-to-talk recording duration must be 2–20 seconds.")
        if not self.model_path.is_dir():
            raise ToolError(
                f"Local Whisper model directory was not found: {self.model_path}. "
                "Place a downloaded faster-whisper model there; Chinuuu will not download a model automatically."
            )
        sd, sf, WhisperModel = self._asr_dependencies()
        try:
            audio = sd.rec(int(seconds * sample_rate), samplerate=sample_rate, channels=1, dtype="float32")
            sd.wait()
            with tempfile.TemporaryDirectory(prefix="chinuuu_voice_") as temp_dir:
                wav_path = Path(temp_dir) / "input.wav"
                sf.write(str(wav_path), audio, sample_rate)
                if self._whisper_model is None:
                    self._whisper_model = WhisperModel(
                        str(self.model_path), device="cpu", compute_type="int8", local_files_only=True
                    )
                segments, _ = self._whisper_model.transcribe(str(wav_path), beam_size=1, vad_filter=True)
                text = " ".join(segment.text.strip() for segment in segments).strip()
        except Exception as exc:
            raise ToolError(f"Offline speech recognition failed: {exc}") from exc
        if not text:
            raise ToolError("No recognizable speech was detected in the recording.")
        return text

    @staticmethod
    def speak(text: str) -> None:
        if not isinstance(text, str) or not text.strip():
            return
        try:
            pyttsx3 = OfflineVoiceEngine._tts_dependency()
            engine = pyttsx3.init()
            engine.setProperty("rate", 175)
            engine.say(text[:5000])
            engine.runAndWait()
            engine.stop()
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"Offline text-to-speech failed: {exc}") from exc
