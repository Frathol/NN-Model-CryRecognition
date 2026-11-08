import numpy as np
from scipy.fftpack import dct

SR = 8000
WIN_SAMPLES = 8000
HOP_SAMPLES = 4000
N_FFT = 256
WIN_LENGTH = 200
HOP_LENGTH = 80
N_MELS = 40
FMIN = 50.0
FMAX = 4000.0
FLOOR_DB = -80.0
SILENCE_DBFS = -50.0

def _hz_to_mel(hz):
    return 2595.0 * np.log10(1.0 + hz / 700.0)

def _mel_to_hz(mel):
    return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)

def _create_mel_filterbank():
    mel_points = np.linspace(_hz_to_mel(FMIN), _hz_to_mel(FMAX), N_MELS + 2)
    hz_points = _mel_to_hz(mel_points)
    bin_points = np.floor((N_FFT + 1) * hz_points / SR).astype(int)
    fbank = np.zeros((N_MELS, N_FFT // 2 + 1), dtype=np.float32)
    for m in range(1, N_MELS + 1):
        f_m_minus, f_m, f_m_plus = bin_points[m - 1], bin_points[m], bin_points[m + 1]
        for k in range(f_m_minus, f_m):
            fbank[m - 1, k] = (k - f_m_minus) / max(1, (f_m - f_m_minus))
        for k in range(f_m, f_m_plus):
            fbank[m - 1, k] = (f_m_plus - k) / max(1, (f_m_plus - f_m))
    return fbank, hz_points[1:-1]

MEL_FBANK, MEL_CENTER_HZ = _create_mel_filterbank()
HANNING_WIN = np.hanning(WIN_LENGTH).astype(np.float32)

def rms_dbfs(w8k):
    w = np.atleast_2d(w8k).astype(np.float32)
    rms = np.sqrt(np.mean(w ** 2, axis=-1) + 1e-12)
    return 20.0 * np.log10(rms)

def _frame_signal(w):
    w = np.atleast_2d(w).astype(np.float32)
    num_frames = 1 + (w.shape[1] - WIN_LENGTH) // HOP_LENGTH
    shape = (w.shape[0], num_frames, WIN_LENGTH)
    strides = (w.strides[0], w.strides[1] * HOP_LENGTH, w.strides[1])
    return np.lib.stride_tricks.as_strided(w, shape=shape, strides=strides)

def logmel(w8k):
    frames = _frame_signal(w8k) * HANNING_WIN
    spec = np.fft.rfft(frames, n=N_FFT, axis=-1)
    power_spec = (np.abs(spec) ** 2) / N_FFT
    mel_energy = np.matmul(power_spec, MEL_FBANK.T)
    log_mel = 10.0 * np.log10(np.maximum(mel_energy, 1e-10))
    return np.maximum(log_mel, FLOOR_DB).astype(np.float32)

def extract_baselines(w8k):
    frames = _frame_signal(w8k) * HANNING_WIN
    ste = np.sum(frames ** 2, axis=-1)
    spec_mag = np.abs(np.fft.rfft(frames, n=N_FFT, axis=-1)) + 1e-10
    freqs = np.linspace(0, SR / 2.0, N_FFT // 2 + 1, dtype=np.float32)

    centroid = np.sum(spec_mag * freqs, axis=-1) / np.sum(spec_mag, axis=-1)
    bw = np.sqrt(np.sum(((freqs - centroid[..., None]) ** 2) * spec_mag, axis=-1) / np.sum(spec_mag, axis=-1))

    fahmi_feat = np.stack([
        np.min(ste, axis=-1), np.max(ste, axis=-1), np.mean(ste, axis=-1), np.std(ste, axis=-1),
        np.mean(centroid, axis=-1), np.std(centroid, axis=-1),
        np.mean(bw, axis=-1), np.std(bw, axis=-1)
    ], axis=-1).astype(np.float32)

    lm = logmel(w8k)
    mfcc = dct(lm, type=2, axis=-1, norm='ortho')[..., :13]
    mfcc_feat = np.concatenate([np.mean(mfcc, axis=1), np.std(mfcc, axis=1)], axis=-1).astype(np.float32)
    return fahmi_feat, mfcc_feat
