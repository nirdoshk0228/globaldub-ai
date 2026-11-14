import numpy as np
import soundfile as sf

from globaldub import audio
from globaldub.separation import separate

SR = audio.SR


def test_center_channel_separation_removes_centred_voice(tmp_path):
    t = np.arange(SR * 3) / SR
    voice = 0.4 * np.sin(2 * np.pi * 220 * t)                    # panned centre (same in L and R)
    music = 0.2 * np.sin(2 * np.pi * 523 * t)                    # panned wide (opposite phase)
    stereo = np.stack([voice + music, voice - music], axis=1).astype(np.float32)
    src = tmp_path / "mix.wav"
    sf.write(str(src), stereo, SR)
    stems = separate(src, tmp_path / "out", "center")
    assert stems is not None and stems.method == "center"
    bed = audio.load(stems.bed)
    spec = np.abs(np.fft.rfft(bed))
    freqs = np.fft.rfftfreq(len(bed), 1 / SR)
    assert spec[np.argmin(abs(freqs - 523))] > 20 * spec[np.argmin(abs(freqs - 220))]


def test_mono_audio_has_nothing_to_separate(tmp_path):
    src = tmp_path / "mono.wav"
    sf.write(str(src), (0.3 * np.sin(2 * np.pi * 200 * np.arange(SR * 2) / SR)).astype(np.float32), SR)
    assert separate(src, tmp_path / "out", "center") is None


def test_bed_is_ducked_under_speech_and_dialogue_level_matched(tmp_path):
    clip = tmp_path / "line.wav"
    sf.write(str(clip), (0.05 * np.sin(2 * np.pi * 300 * np.arange(SR) / SR)).astype(np.float32), SR)
    bed = tmp_path / "bed.wav"
    sf.write(str(bed), np.full(SR * 6, 0.05, dtype=np.float32), SR)
    out = audio.assemble([(2.0, str(clip))], 5.0, tmp_path / "mix.wav", bed=bed, duck_db=6, dialogue_rms=0.12)
    x = audio.load(out)
    quiet = x[int(0.5 * SR):int(1.0 * SR)]                     # only bed
    speech = x[int(2.3 * SR):int(2.7 * SR)]                    # voice + ducked bed
    assert abs(float(np.mean(quiet)) - 0.05) < 0.005           # bed untouched away from speech
    assert float(np.mean(speech)) < 0.035                      # bed ~6 dB down under speech
    assert 0.10 < audio.active_rms(speech - np.mean(speech)) < 0.14   # voice raised to the target loudness


def test_short_bed_is_looped(tmp_path):
    b = audio.fit_bed(np.ones(SR, dtype=np.float32), SR * 10)
    assert len(b) == SR * 10 and b[SR * 5] == 1.0


def _voice(f0, seconds, syllables=True):
    t = np.arange(int(seconds * SR)) / SR
    ph = 2 * np.pi * np.cumsum(f0(t)) / SR
    x = sum((0.5 / k) * np.sin(k * ph) for k in range(1, 6))
    if syllables:
        x = x * np.clip(np.sin(2 * np.pi * 4.5 * t), 0, 1) ** 0.5
    return x.astype(np.float32)


def test_pitch_gives_gender_splits_speakers_and_spots_singing():
    from globaldub import prosody
    from globaldub.schemas import Episode, Segment

    male = lambda t: 120 + 25 * np.sin(2 * np.pi * 1.3 * t)          # noqa: E731
    female = lambda t: 215 + 30 * np.sin(2 * np.pi * 1.1 * t)        # noqa: E731
    sung = lambda t: np.repeat([196.0, 220.0, 246.9, 220.0], int(0.5 * SR))[: len(t)]  # noqa: E731  high male singer
    parts = [_voice(male, 2), _voice(female, 2), _voice(male, 2), _voice(female, 2), _voice(sung, 2, syllables=False)]
    gap = np.zeros(int(0.5 * SR), dtype=np.float32)
    wav = np.concatenate([np.concatenate([p, gap]) for p in parts])
    segs = [Segment(id=f"s{i:03d}", start=i * 2.5, end=i * 2.5 + 2, speaker="Speaker 1", text="कुछ शब्द यहाँ हैं")
            for i in range(5)]
    ep = Episode(id="x", title="t", segments=segs, target_langs=["en-US"])
    info = prosody.apply(ep, wav)
    assert info["split"] and info["songs"] == 1
    assert [s.speaker for s in ep.segments[:4]] == ["Speaker 1", "Speaker 2", "Speaker 1", "Speaker 2"]
    assert ep.glossary.character("Speaker 1").gender == "male"
    assert ep.glossary.character("Speaker 2").gender == "female"
    assert ep.segments[4].is_song and not ep.segments[0].is_song
