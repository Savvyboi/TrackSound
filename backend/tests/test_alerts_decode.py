"""SAME decoder on synthetic alerts: clean, noisy, under music, clock errors, codecs, false alarms."""
from __future__ import annotations

import subprocess

import numpy as np
import pytest
import soundfile as sf
from scipy.signal import resample_poly

from tracksound.sources.alerts import same, scan, synth

SR = 44100
HDR = "ZCZC-WXR-TOR-029095-029037-120001+0030-1281917-KEAX/NWS-"


def noise_for_snr(n: int, snr_db: float, amp: float, seed: int) -> np.ndarray:
    """White noise giving ``snr_db`` in a 3 kHz band against a sine burst of amplitude ``amp``."""
    sigma = np.sqrt(amp * amp / 2 / 10 ** (snr_db / 10) * (SR / 2) / 3000)
    return np.random.default_rng(seed).standard_normal(n).astype(np.float32) * sigma


def scan44(y: np.ndarray) -> scan.ScanResult:
    """Scan 44.1 kHz samples (decimated by 4 to the decoder's 11025 Hz)."""
    return scan.scan_array(resample_poly(y, 1, 4).astype(np.float32), same.FS)


def test_alert_under_music_across_a_block_boundary():
    x, info = synth.alert_waveform(HDR, sr=SR, attention="eas", seed=1)
    bed = synth.music_like(70.0, SR, seed=2)
    gain = np.sqrt(np.mean(bed ** 2) / np.mean(x[: int(0.5 * SR)] ** 2)) * 10 ** (-6 / 20)   # 6 dB under the music
    y = synth.mix(x * gain, bed + noise_for_snr(len(bed), 30, 0.1, 3), at_s=28.5, sr=SR)     # first burst straddles 30 s
    r = scan44(y)
    assert len(r.headers) == 1 and not r.undecoded
    h = r.headers[0]
    assert h["raw"] == HDR and h["repeats"] == 3 and h["p_correct"] > 0.9
    assert h["t"] == pytest.approx(28.5 + info["headers"][0], abs=0.005)
    assert h["fields"]["locations"] == ["029095", "029037", "120001"]
    assert (h["fields"]["jjj"], h["fields"]["hh"], h["fields"]["mm"]) == (128, 19, 17)
    assert h["attention"]["kind"] == "eas" and h["attention"]["t"] == pytest.approx(28.5 + info["attention"][0], abs=0.2)
    assert len(r.eoms) == 1 and r.eoms[0]["bursts"] == 3
    assert r.eoms[0]["t"] == pytest.approx(28.5 + info["eoms"][0], abs=0.005)


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_weak_alert_in_noise(seed):
    """+2 dB in a 3 kHz band: single bursts have bit errors; combining the three fixes them."""
    x, _ = synth.alert_waveform(HDR, sr=SR, attention="none", seed=seed)
    r = scan44(synth.mix(x * 0.1, noise_for_snr(len(x) + 4 * SR, 2.0, 0.1, seed + 100), 2.0, SR))
    assert [h["raw"] for h in r.headers] == [HDR]


def test_bit_clock_error_and_playback_speed():
    """An encoder rounding to 85 samples per bit at 44.1 kHz (-0.39 %) and 1 % fast playback."""
    for kw in ({"baud_error": 84.672 / 85 - 1}, {"speed": 1.01}):
        x, _ = synth.alert_waveform(HDR, sr=SR, attention="nws", seed=5, **kw)
        r = scan44(synth.mix(x * 0.1, noise_for_snr(len(x) + 4 * SR, 10.0, 0.1, 7), 2.0, SR))
        assert [h["raw"] for h in r.headers] == [HDR], kw
        assert r.headers[0]["attention"]["kind"] == "nws"


def test_eighth_bit_set_and_one_burst_damaged():
    """47 CFR 11.31(a)(1) lets bit 8 be 0 or 1; a loud click masks half of the middle burst."""
    bits = same.bits_of(bytes([0xAB] * 16) + bytes(c | 0x80 for c in HDR.encode()))
    burst = synth.afsk(bits, SR)
    gap = np.zeros(SR, np.float32)
    x = np.concatenate([burst, gap, burst, gap, burst, gap]) * 0.1
    y = synth.mix(x, noise_for_snr(len(x) + 4 * SR, 10.0, 0.1, 8), 2.0, SR)
    mid = int((2.0 + (len(burst) + SR) / SR) * SR)
    y[mid + len(burst) // 3: mid + 2 * len(burst) // 3] += np.random.default_rng(9).standard_normal(len(burst) // 3) * 0.5
    r = scan44(y)
    assert [h["raw"] for h in r.headers] == [HDR]
    assert r.headers[0]["repeats"] >= 2


@pytest.mark.parametrize("codec", [["-c:a", "libmp3lame", "-b:a", "64k"], ["-c:a", "aac", "-b:a", "48k"]],
                         ids=["mp3-64k", "aac-48k"])
def test_codec_round_trip(tmp_path, codec):
    x, info = synth.alert_waveform(HDR, sr=SR, attention="eas", seed=3)
    bed = synth.music_like(len(x) / SR + 6, SR, seed=4) * 0.3
    y = synth.mix(x * 0.1, bed + noise_for_snr(len(bed), 20.0, 0.1, 5), 3.0, SR)
    src, out = tmp_path / "src.wav", tmp_path / ("out.mp3" if "libmp3lame" in codec else "out.m4a")
    sf.write(src, y, SR)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), *codec, str(out)], check=True)
    r = scan.scan_file(out)
    assert [h["raw"] for h in r.headers] == [HDR]
    # MP3 and AAC encoders add a little delay; the burst time must still be within a few ms
    assert r.headers[0]["t"] == pytest.approx(3.0 + info["headers"][0], abs=0.06)
    assert len(r.eoms) == 1


def test_no_false_alarms_on_noise_music_and_speech():
    rng = np.random.default_rng(11)
    parts = [rng.standard_normal(100 * SR).astype(np.float32) * 0.1, synth.music_like(100, SR, seed=12),
             synth.speech_like(100, SR, rng)]
    r = scan44(np.concatenate(parts))
    assert r.headers == [] and r.eoms == [] and r.undecoded == []
    assert not [s for s in r.attention if s["kind"] == "eas"]
