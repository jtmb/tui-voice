#!/usr/bin/env python3
"""Measure local Kokoro latency with profiling disabled in the timed region.

Both CPU and GPU default to the same pinned FP32 artifact for a fair comparison.
GPU preparation already synthesizes a verification phrase, so its first timed
request must not be reported as cold. This tool neither plays nor saves speech.
"""
import argparse
import base64
import importlib.metadata
import io
import json
import os
from pathlib import Path
import statistics
import sys
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tts_worker as worker

DEFAULT_TEXT = 'This is a local Kokoro speech performance check.'
PACKAGES = ('onnxruntime', 'onnxruntime-ep-migraphx', 'migraphx', 'migraphx-libs',
            'rocm', 'rocm-sdk-core', 'rocm-sdk-libraries', 'rocm-sdk-device-gfx1100',
            'kokoro-onnx', 'numpy')


def audio_seconds(result):
    data = base64.b64decode(result['wav'], validate=True)
    with wave.open(io.BytesIO(data), 'rb') as audio:
        duration = audio.getnframes() / audio.getframerate()
    if duration <= 0:
        raise ValueError('Benchmark returned empty audio')
    return duration


def benchmark(provider, model, text, voice, threads, samples):
    # Configuration belongs to this command's process, never the user's shell.
    os.environ['TUI_VOICE_TTS_PROVIDER'] = provider
    os.environ['TUI_VOICE_TTS_MODEL'] = model
    started = time.perf_counter()
    engine = worker.prepare_engine(threads=threads, warmup=False)
    prepared = time.perf_counter() - started
    metadata = engine.describe()
    worker.validate_backend(metadata)
    if metadata.get('profiling_active') is not False:
        raise RuntimeError('Cannot benchmark until session profiling has stopped')
    timings, durations = [], []
    for _ in range(samples + 1):
        started = time.perf_counter()
        result = engine.speak(text, voice)
        elapsed = time.perf_counter() - started
        timings.append(elapsed)
        durations.append(audio_seconds(result))
    warm = timings[1:]
    warm_durations = durations[1:]
    preset = engine.voices[voice]
    versions = {}
    for name in PACKAGES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    return {
        'ok': True, **metadata,
        'python': sys.version.split()[0], 'packages': versions,
        'text': text, 'voice': voice, 'voice_model': preset['model'],
        'speed': preset['speed'], 'language': preset['lang'], 'threads': threads,
        'profiling_during_measurement': False,
        'preparation_s': prepared,
        'preparation_includes_gpu_verification': provider == 'migraphx',
        'prior_synthesis_in_preparation': provider == 'migraphx',
        'first_measured_synthesis_s': timings[0],
        'first_measured_audio_seconds': durations[0],
        'warm_samples_s': warm, 'warm_audio_seconds': warm_durations,
        'warm_median_s': statistics.median(warm),
        'warm_min_s': min(warm), 'warm_max_s': max(warm),
        'warm_median_real_time_factor': statistics.median(
            elapsed / duration for elapsed, duration in zip(warm, warm_durations)),
        'measurement_scope': 'Local synthesis including phonemization and WAV encoding; no playback or IPC',
        'note': ('Compare warmed medians using identical model SHA-256, text, voice, '
                 'speed, and threads. GPU preparation includes mandatory verification '
                 'and compilation; the first measured GPU request is not cold.'),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', choices=('cpu', 'migraphx'), required=True)
    parser.add_argument('--model', default='kokoro-v1.0.onnx',
                        help='Pinned model filename or absolute path; FP32 for both providers')
    parser.add_argument('--text', default=DEFAULT_TEXT)
    parser.add_argument('--voice', choices=('heart', 'velvet', 'english', 'portuguese'), default='heart')
    parser.add_argument('--threads', type=int, default=worker.DEFAULT_THREADS)
    parser.add_argument('--samples', type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.threads <= 256:
        parser.error('--threads must be 1..256')
    if not 5 <= args.samples <= 100:
        parser.error('--samples must be 5..100')
    if not args.text.strip() or len(args.text.strip()) > worker.MAX_TEXT:
        parser.error('Text must fit one nonempty worker chunk')
    try:
        result = benchmark(args.provider, args.model, args.text.strip(),
                           args.voice, args.threads, args.samples)
    except Exception as error:
        print(json.dumps({'ok': False, 'error': str(error)}))
        return 1
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
