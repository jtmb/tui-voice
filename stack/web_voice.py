"""Local Whisper/Kokoro engine for tui-voice (installed by setup_stack.py).

Runs in the stack's service virtualenv. No network, no cloud: audio arrives as
base64 WAV, text leaves as base64 WAV, and nothing is written to disk.
"""
import base64
import io
import os
from pathlib import Path
import sys
import threading
import wave

import numpy as np

VOICE_DIR = Path(os.environ.get('TUI_VOICE_VOICE_DIR') or
                 Path(__file__).resolve().parent.parent / 'voice')
MODEL_DIR = Path(os.environ.get('TUI_VOICE_ASR_MODEL') or
                 (VOICE_DIR / 'models/whisper-base.en'))
KOKORO_MODEL = Path(os.environ.get('TUI_VOICE_TTS_MODEL') or
                    (VOICE_DIR / 'models/kokoro-v1.0.onnx'))
if not KOKORO_MODEL.is_absolute():
    KOKORO_MODEL = VOICE_DIR / 'models' / KOKORO_MODEL
VOICES_BIN = VOICE_DIR / 'models/voices-v1.0.bin'
MAX_WAV = 8 * 1024 * 1024
MAX_SPEECH = 8 * 1024 * 1024
MAX_TEXT = 1200
ENGINE = 'Whisper base.en / faster-whisper / CPU int8 (local)'

sys.path.insert(0, str(VOICE_DIR))


class WebVoice:
    """Minimal cold-path engine: one loaded model per process, serialized use."""

    def __init__(self):
        self.lock = threading.Lock()
        self._asr = None
        self._tts = None
        self._voices = None

    def selected(self):
        from voices import PRESETS
        return 'heart' if 'heart' in PRESETS else next(iter(PRESETS))

    def catalog(self):
        from voices import PRESETS
        return {'selected': self.selected(),
                'voices': [{'id': key, 'name': value['name'],
                            'description': value['description']}
                           for key, value in PRESETS.items()],
                'note': 'Local voices on this machine · nothing is uploaded.'}

    def select(self, identifier):
        from voices import PRESETS
        if identifier not in PRESETS:
            raise ValueError('Unknown local voice')
        return self.catalog()

    def _whisper(self):
        if self._asr is None:
            from faster_whisper import WhisperModel
            self._asr = WhisperModel(str(MODEL_DIR), device='cpu', compute_type='int8')
        return self._asr

    def _kokoro(self):
        if self._tts is None:
            from kokoro_onnx import Kokoro
            self._tts = Kokoro(str(KOKORO_MODEL), str(VOICES_BIN))
        return self._tts

    def transcribe(self, encoded):
        if not isinstance(encoded, str) or not encoded:
            raise ValueError('Record a short message first')
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            raise ValueError('The recorded audio was not valid') from None
        if not 0 < len(raw) <= MAX_WAV:
            raise ValueError('That recording is too large; keep dictation under 30 seconds')
        if not raw.startswith(b'RIFF') or raw[8:12] != b'WAVE':
            raise ValueError('Expected a PCM WAV recording')
        if not self.lock.acquire(blocking=False):
            raise ValueError('Voice is busy; wait for the current turn to finish')
        try:
            segments, _info = self._whisper().transcribe(
                io.BytesIO(raw), language='en', beam_size=5, temperature=0,
                condition_on_previous_text=False, word_timestamps=True,
                vad_filter=True, vad_parameters={'min_silence_duration_ms': 500})
            words, texts = [], []
            for segment in segments:
                texts.append(segment.text)
                for word in (segment.words or ()):
                    if word.probability is not None:
                        words.append(float(word.probability))
        finally:
            self.lock.release()
        return {'text': ' '.join(''.join(texts).split())[:6000],
                'confidence': round(sum(words) / len(words), 4) if words else 0.0,
                'confidence_kind': 'Whisper mean word probability; heuristic, '
                                   'not calibrated accuracy',
                'engine': ENGINE,
                'raw_audio_retained': False}

    def speak(self, text, identifier=None):
        from voices import PRESETS
        text = (text or '').strip()
        if not text or len(text) > MAX_TEXT:
            raise ValueError(f'Speak 1..{MAX_TEXT} characters per chunk')
        identifier = identifier or self.selected()
        preset = PRESETS.get(identifier)
        if preset is None:
            raise ValueError('Unknown local voice')
        if not self.lock.acquire(blocking=False):
            raise ValueError('Voice is busy; wait for the current turn to finish')
        try:
            samples, rate = self._kokoro().create(
                text, voice=preset['model'], speed=preset.get('speed', 1.0),
                lang=preset.get('lang', 'en-us'))
            samples = np.asarray(samples)
            if samples.ndim != 1 or samples.size == 0 or not np.isfinite(samples).all():
                raise ValueError('Kokoro returned empty, non-finite, or non-mono audio')
            pcm = (np.clip(samples, -.98, .98) * 32767).astype('<i2').tobytes()
        finally:
            self.lock.release()
        output = io.BytesIO()
        with wave.open(output, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(int(rate))
            wav.writeframes(pcm)
        data = output.getvalue()
        if not data or len(data) > MAX_SPEECH:
            raise ValueError('Speech output outside finite budget')
        return {'wav': base64.b64encode(data).decode('ascii'), 'mime': 'audio/wav',
                'voice': identifier, 'name': preset['name'], 'text': text}
