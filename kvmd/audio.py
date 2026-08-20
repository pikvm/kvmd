# ========================================================================== #
#                                                                            #
#    KVMD - The main PiKVM daemon.                                           #
#                                                                            #
#    Copyright (C) 2018-2024  Maxim Devaev <mdevaev@gmail.com>               #
#                                                                            #
#    This program is free software: you can redistribute it and/or modify    #
#    it under the terms of the GNU General Public License as published by    #
#    the Free Software Foundation, either version 3 of the License, or       #
#    (at your option) any later version.                                     #
#                                                                            #
#    This program is distributed in the hope that it will be useful,         #
#    but WITHOUT ANY WARRANTY; without even the implied warranty of          #
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the           #
#    GNU General Public License for more details.                            #
#                                                                            #
#    You should have received a copy of the GNU General Public License       #
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.  #
#                                                                            #
# ========================================================================== #


import errno
import ctypes
import ctypes.util
import functools

from typing import Any
from typing import Sequence

from ctypes import c_int
from ctypes import c_uint
from ctypes import c_long
from ctypes import c_ulong
from ctypes import c_int16
from ctypes import c_char_p
from ctypes import c_void_p
from ctypes import POINTER


# =====
class AudioError(Exception):
    pass


# =====
_SND_PCM_STREAM_PLAYBACK = 0
_SND_PCM_STREAM_CAPTURE = 1
_SND_PCM_NONBLOCK = 1
_SND_PCM_FORMAT_S16_LE = 2
_SND_PCM_ACCESS_RW_INTERLEAVED = 3

_OPUS_MAX_FRAME_MS = 120  # The longest possible Opus frame
_OPUS_MAX_PACKET = 1275 * 3 + 3  # The longest possible Opus packet

_OPUS_APPLICATION_AUDIO = 2049
_OPUS_SET_BITRATE_REQUEST = 4002
_OPUS_SET_MAX_BANDWIDTH_REQUEST = 4004
_OPUS_SET_SIGNAL_REQUEST = 4024
_OPUS_BANDWIDTH_FULLBAND = 1105
_OPUS_SIGNAL_MUSIC = 3002

_SPEEX_QUALITY_DESKTOP = 5


def _load_lib(name: str, funcs: Sequence[tuple[str, Any, tuple]]) -> ctypes.CDLL:
    path = ctypes.util.find_library(name)
    if not path:
        raise AudioError(f"Where is lib{name}?")
    try:
        lib = ctypes.CDLL(path)
    except Exception as ex:
        raise AudioError(f"Can't load lib{name}: {type(ex).__name__}: {ex}")
    for (func_name, restype, argtypes) in funcs:
        func = getattr(lib, func_name, None)
        if func is None:
            raise AudioError(f"Where is lib{name}.{func_name}?")
        setattr(func, "restype", restype)
        setattr(func, "argtypes", list(argtypes))
    return lib


@functools.cache
def _load_libasound() -> ctypes.CDLL:
    return _load_lib("asound", [
        ("snd_pcm_open",       c_int,    (POINTER(c_void_p), c_char_p, c_int, c_int)),
        ("snd_pcm_set_params", c_int,    (c_void_p, c_int, c_int, c_uint, c_uint, c_int, c_uint)),
        ("snd_pcm_writei",     c_long,   (c_void_p, c_char_p, c_ulong)),
        ("snd_pcm_readi",      c_long,   (c_void_p, c_void_p, c_ulong)),
        ("snd_pcm_recover",    c_int,    (c_void_p, c_int, c_int)),
        ("snd_pcm_wait",       c_int,    (c_void_p, c_int)),
        ("snd_pcm_close",      c_int,    (c_void_p,)),
        ("snd_strerror",       c_char_p, (c_int,)),

        # The capture device requires the low-level params API: snd_pcm_set_params()
        # asks for a small buffer, which the I2S driver can't provide
        ("snd_pcm_hw_params_malloc",        c_int, (POINTER(c_void_p),)),
        ("snd_pcm_hw_params_free",          None,  (c_void_p,)),
        ("snd_pcm_hw_params_any",           c_int, (c_void_p, c_void_p)),
        ("snd_pcm_hw_params_set_access",    c_int, (c_void_p, c_void_p, c_int)),
        ("snd_pcm_hw_params_set_format",    c_int, (c_void_p, c_void_p, c_int)),
        ("snd_pcm_hw_params_set_channels",  c_int, (c_void_p, c_void_p, c_uint)),
        ("snd_pcm_hw_params_set_rate_near", c_int, (c_void_p, c_void_p, POINTER(c_uint), c_void_p)),
        ("snd_pcm_hw_params_set_period_size_near", c_int, (c_void_p, c_void_p, POINTER(c_ulong), c_void_p)),
        ("snd_pcm_hw_params_set_buffer_size_near", c_int, (c_void_p, c_void_p, POINTER(c_ulong))),
        ("snd_pcm_hw_params_get_period_size", c_int, (c_void_p, POINTER(c_ulong), c_void_p)),
        ("snd_pcm_hw_params_get_buffer_size", c_int, (c_void_p, POINTER(c_ulong))),
        ("snd_pcm_hw_params",               c_int, (c_void_p, c_void_p)),
        ("snd_pcm_avail_update",            c_long, (c_void_p,)),
    ])


@functools.cache
def _load_libopus() -> ctypes.CDLL:
    return _load_lib("opus", [
        ("opus_decoder_create",  c_void_p, (c_int, c_int, POINTER(c_int))),
        ("opus_decoder_destroy", None,     (c_void_p,)),
        ("opus_decode",          c_int,    (c_void_p, c_char_p, c_int, POINTER(c_int16), c_int, c_int)),
        ("opus_encoder_create",  c_void_p, (c_int, c_int, c_int, POINTER(c_int))),
        ("opus_encoder_destroy", None,     (c_void_p,)),
        # It's a variadic function, the value is passed by the default conversion rules
        ("opus_encoder_ctl",     c_int,    (c_void_p, c_int)),
        ("opus_encode",          c_int,    (c_void_p, c_char_p, c_int, c_char_p, c_int)),
        ("opus_strerror",        c_char_p, (c_int,)),
    ])


@functools.cache
def _load_libspeexdsp() -> ctypes.CDLL:
    return _load_lib("speexdsp", [
        ("speex_resampler_init",                    c_void_p, (c_uint, c_uint, c_uint, c_int, POINTER(c_int))),
        ("speex_resampler_destroy",                 None,     (c_void_p,)),
        ("speex_resampler_process_interleaved_int", c_int,    (c_void_p, c_void_p, POINTER(c_uint), c_void_p, POINTER(c_uint))),
        ("speex_resampler_strerror",                c_char_p, (c_int,)),
    ])


def _alsa_error(lib: ctypes.CDLL, err: int, msg: str) -> AudioError:
    reason = lib.snd_strerror(err)
    return AudioError(f"{msg}: {reason.decode(errors='replace') if reason else err}")


def _opus_error(lib: ctypes.CDLL, err: int, msg: str) -> AudioError:
    reason = lib.opus_strerror(err)
    return AudioError(f"{msg}: {reason.decode(errors='replace') if reason else err}")


def _speex_error(lib: ctypes.CDLL, err: int, msg: str) -> AudioError:
    reason = lib.speex_resampler_strerror(err)
    return AudioError(f"{msg}: {reason.decode(errors='replace') if reason else err}")


# =====
class PcmPlayback:
    # A thin wrapper around the ALSA playback device.
    # All the methods are blocking and should be called from a thread.

    # The device is opened in the non-blocking mode: if the USB host stops reading the gadget,
    # a blocking write would hang forever and the session could never be stopped
    __WAIT_MS = 100
    __MAX_STALLS = 2

    def __init__(self, device: str, hz: int, channels: int, latency: float) -> None:
        self.__device = device
        self.__hz = hz
        self.__channels = channels
        self.__latency = latency
        self.__pcm: (c_void_p | None) = None
        self.underruns = 0  # Recovered errors and dropped frames, for the diagnostics
        self.stalled = 0

    @classmethod
    def probe(cls, device: str) -> bool:
        try:
            lib = _load_libasound()
        except AudioError:
            return False
        pcm = c_void_p()
        err = int(lib.snd_pcm_open(ctypes.byref(pcm), device.encode(), _SND_PCM_STREAM_PLAYBACK, 0))
        if err == 0:
            lib.snd_pcm_close(pcm)
            return True
        # The busy device is a live device too
        return (err == -errno.EBUSY)

    def open(self) -> None:
        assert self.__pcm is None
        lib = _load_libasound()
        pcm = c_void_p()
        err = int(lib.snd_pcm_open(ctypes.byref(pcm), self.__device.encode(),
                                   _SND_PCM_STREAM_PLAYBACK, _SND_PCM_NONBLOCK))
        if err < 0:
            raise _alsa_error(lib, err, "Can't open PCM playback")
        err = int(lib.snd_pcm_set_params(
            pcm, _SND_PCM_FORMAT_S16_LE, _SND_PCM_ACCESS_RW_INTERLEAVED,
            self.__channels, self.__hz, 1, int(self.__latency * 1000000),
        ))
        if err < 0:
            lib.snd_pcm_close(pcm)
            raise _alsa_error(lib, err, "Can't configure PCM playback")
        self.__pcm = pcm

    def write(self, data: bytes) -> None:
        assert self.__pcm is not None
        lib = _load_libasound()
        frames = len(data) // (2 * self.__channels)
        if frames == 0:
            return
        stalls = 0
        while frames > 0:
            written = int(lib.snd_pcm_writei(self.__pcm, data, frames))
            if written == -errno.EAGAIN:
                # The buffer is full: the host is not reading the gadget fast enough
                ready = int(lib.snd_pcm_wait(self.__pcm, self.__WAIT_MS))
                if ready < 0:
                    if not self.__recover(lib, ready):
                        raise _alsa_error(lib, ready, "Can't play PCM")
                    return
                if ready == 0:
                    stalls += 1
                    if stalls >= self.__MAX_STALLS:
                        # The host doesn't consume the audio at all, the frame is late anyway
                        self.stalled += 1
                        return
                continue
            if written < 0:
                # Underrun or suspend: recover the device and drop the rest of the chunk
                if not self.__recover(lib, written):
                    raise _alsa_error(lib, written, "Can't play PCM")
                return
            frames -= written
            if frames > 0:  # Partial write
                data = data[written * 2 * self.__channels:]

    def __recover(self, lib: ctypes.CDLL, err: int) -> bool:
        self.underruns += 1
        return (int(lib.snd_pcm_recover(self.__pcm, err, 1)) >= 0)

    def close(self) -> None:
        if self.__pcm is not None:
            try:
                _load_libasound().snd_pcm_close(self.__pcm)
            finally:
                self.__pcm = None


class PcmCapture:  # pylint: disable=too-many-instance-attributes
    # A thin blocking wrapper around the ALSA capture device.
    # All the methods are blocking and should be called from a thread.

    # The device buffer is huge by default (the I2S driver offers 2.7 seconds), and we always
    # read the oldest frame, so any stall of the reader turns into a permanent extra latency.
    # We ask for a smaller buffer and, if the driver refuses, skip the backlog by hand.
    __BUFFER_MS = 200
    __MAX_LAG_MS = 100
    __WAIT_MS = 200

    def __init__(self, device: str, hz: int, channels: int, frame_ms: int) -> None:
        self.__device = device
        self.__hz = hz
        self.__channels = channels
        self.__frame_ms = frame_ms
        self.__frames = 0
        self.__max_lag = 0
        self.__buf = ctypes.create_string_buffer(0)
        self.__pcm: (c_void_p | None) = None
        self.period = 0     # The negotiated ALSA params and the recovered errors,
        self.buffer = 0     # ... for the diagnostics
        self.overruns = 0
        self.skipped = 0
        self.stalled = 0

    @classmethod
    def probe(cls, device: str) -> bool:
        try:
            lib = _load_libasound()
        except AudioError:
            return False
        pcm = c_void_p()
        err = int(lib.snd_pcm_open(ctypes.byref(pcm), device.encode(), _SND_PCM_STREAM_CAPTURE, 0))
        if err == 0:
            lib.snd_pcm_close(pcm)
            return True
        # The busy device is a live device too
        return (err == -errno.EBUSY)

    def open(self) -> int:
        # Returns the real capture rate, it can differ from the requested one
        assert self.__pcm is None
        lib = _load_libasound()
        pcm = c_void_p()
        err = int(lib.snd_pcm_open(ctypes.byref(pcm), self.__device.encode(),
                                   _SND_PCM_STREAM_CAPTURE, _SND_PCM_NONBLOCK))
        if err < 0:
            raise _alsa_error(lib, err, "Can't open PCM capture")
        try:
            hz = self.__configure(pcm)
        except Exception:
            lib.snd_pcm_close(pcm)
            raise
        self.__frames = (hz * self.__frame_ms) // 1000
        self.__max_lag = (hz * self.__MAX_LAG_MS) // 1000
        self.__buf = ctypes.create_string_buffer(self.__frames * self.__channels * 2)
        self.__pcm = pcm
        return hz

    def read(self) -> bytes:
        # Returns the whole frame or nothing if the device was recovered after an error
        assert self.__pcm is not None
        lib = _load_libasound()
        self.__skip_backlog(lib)
        addr = ctypes.addressof(self.__buf)
        offset = 0
        frames = self.__frames
        while frames > 0:
            read = int(lib.snd_pcm_readi(self.__pcm, addr + offset, frames))
            if read == -errno.EAGAIN:
                # Not enough data yet, this is the normal case for the non-blocking device
                ready = int(lib.snd_pcm_wait(self.__pcm, self.__WAIT_MS))
                if ready == 0:
                    # The source has stopped sending the samples at all
                    self.stalled += 1
                    return b""
                if ready >= 0:
                    continue
                read = ready
            if read < 0:
                # Overrun or suspend: recover the device and drop the incomplete frame
                self.overruns += 1
                err = int(lib.snd_pcm_recover(self.__pcm, read, 1))
                if err < 0:
                    raise _alsa_error(lib, err, "Can't capture PCM")
                return b""
            frames -= read
            offset += read * 2 * self.__channels
        return self.__buf.raw

    def close(self) -> None:
        if self.__pcm is not None:
            try:
                _load_libasound().snd_pcm_close(self.__pcm)
            finally:
                self.__pcm = None

    def __skip_backlog(self, lib: ctypes.CDLL) -> None:
        # If we are late, the queued frames are the past: drop them instead of playing them late
        avail = int(lib.snd_pcm_avail_update(self.__pcm))
        limit = self.__frames + self.__max_lag
        if avail < 0:
            return  # The error will be handled by the normal read()
        while avail >= limit + self.__frames:
            read = int(lib.snd_pcm_readi(self.__pcm, ctypes.addressof(self.__buf), self.__frames))
            if read <= 0:  # The error will be handled by the normal read()
                return
            avail -= read
            self.skipped += 1

    def __configure(self, pcm: c_void_p) -> int:
        lib = _load_libasound()
        params = c_void_p()
        err = int(lib.snd_pcm_hw_params_malloc(ctypes.byref(params)))
        if err < 0:
            raise _alsa_error(lib, err, "Can't allocate PCM params")
        try:
            for (msg, func, args) in [
                ("initialize", lib.snd_pcm_hw_params_any,          ()),
                ("set access", lib.snd_pcm_hw_params_set_access,   (_SND_PCM_ACCESS_RW_INTERLEAVED,)),
                ("set format", lib.snd_pcm_hw_params_set_format,   (_SND_PCM_FORMAT_S16_LE,)),
                ("set channels", lib.snd_pcm_hw_params_set_channels, (self.__channels,)),
            ]:
                err = int(func(pcm, params, *args))
                if err < 0:
                    raise _alsa_error(lib, err, f"Can't {msg} PCM capture params")
            hz = c_uint(self.__hz)
            err = int(lib.snd_pcm_hw_params_set_rate_near(pcm, params, ctypes.byref(hz), None))
            if err < 0:
                raise _alsa_error(lib, err, "Can't set rate of PCM capture")

            # Both are just a hint: the driver clamps them to what it can do,
            # and the caller checks the result
            period = c_ulong((hz.value * self.__frame_ms) // 1000)
            lib.snd_pcm_hw_params_set_period_size_near(pcm, params, ctypes.byref(period), None)
            buffer = c_ulong((hz.value * self.__BUFFER_MS) // 1000)
            lib.snd_pcm_hw_params_set_buffer_size_near(pcm, params, ctypes.byref(buffer))

            err = int(lib.snd_pcm_hw_params(pcm, params))
            if err < 0:
                raise _alsa_error(lib, err, "Can't apply PCM capture params")

            lib.snd_pcm_hw_params_get_period_size(params, ctypes.byref(period), None)
            lib.snd_pcm_hw_params_get_buffer_size(params, ctypes.byref(buffer))
            self.period = period.value
            self.buffer = buffer.value
            return hz.value
        finally:
            lib.snd_pcm_hw_params_free(params)


class OpusDecoder:
    # The decoding itself takes tens of microseconds, so it doesn't need a thread

    @classmethod
    def probe(cls) -> bool:
        try:
            _load_libopus()
        except AudioError:
            return False
        return True

    def __init__(self, hz: int, channels: int) -> None:
        self.__channels = channels
        self.__frames = (hz // 1000) * _OPUS_MAX_FRAME_MS
        self.__buf = (c_int16 * (self.__frames * channels))()
        lib = _load_libopus()
        err = c_int()
        dec = lib.opus_decoder_create(hz, channels, ctypes.byref(err))
        if not dec:
            raise _opus_error(lib, err.value, "Can't create Opus decoder")
        self.__dec: (c_void_p | None) = c_void_p(dec)

    def decode(self, data: bytes) -> bytes:
        assert self.__dec is not None
        lib = _load_libopus()
        frames = int(lib.opus_decode(self.__dec, data, len(data), self.__buf, self.__frames, 0))
        if frames < 0:
            raise _opus_error(lib, frames, "Can't decode Opus frame")
        return ctypes.string_at(self.__buf, frames * self.__channels * 2)

    def close(self) -> None:
        if self.__dec is not None:
            try:
                _load_libopus().opus_decoder_destroy(self.__dec)
            finally:
                self.__dec = None


class OpusEncoder:
    # About 0.5 ms per stereo frame on the CM4, so it runs in the same thread as the capture

    @classmethod
    def probe(cls) -> bool:
        try:
            _load_libopus()
        except AudioError:
            return False
        return True

    def __init__(self, hz: int, channels: int, bitrate: int) -> None:
        self.__channels = channels
        self.__buf = ctypes.create_string_buffer(_OPUS_MAX_PACKET)
        lib = _load_libopus()
        err = c_int()
        enc = lib.opus_encoder_create(hz, channels, _OPUS_APPLICATION_AUDIO, ctypes.byref(err))
        if not enc:
            raise _opus_error(lib, err.value, "Can't create Opus encoder")
        self.__enc: (c_void_p | None) = c_void_p(enc)
        # The same tuning as uStreamer uses for the WebRTC audio
        for (request, value) in [
            (_OPUS_SET_BITRATE_REQUEST, bitrate),
            (_OPUS_SET_MAX_BANDWIDTH_REQUEST, _OPUS_BANDWIDTH_FULLBAND),
            (_OPUS_SET_SIGNAL_REQUEST, _OPUS_SIGNAL_MUSIC),
        ]:
            code = int(lib.opus_encoder_ctl(self.__enc, request, value))
            if code < 0:
                self.close()
                raise _opus_error(lib, code, "Can't configure Opus encoder")

    def encode(self, data: bytes) -> bytes:
        assert self.__enc is not None
        lib = _load_libopus()
        frames = len(data) // (2 * self.__channels)
        size = int(lib.opus_encode(self.__enc, data, frames, self.__buf, _OPUS_MAX_PACKET))
        if size < 0:
            raise _opus_error(lib, size, "Can't encode Opus frame")
        return self.__buf.raw[:size]

    def close(self) -> None:
        if self.__enc is not None:
            try:
                _load_libopus().opus_encoder_destroy(self.__enc)
            finally:
                self.__enc = None


class Resampler:
    # Converts the captured PCM to the rate that Opus can handle.
    # The HDMI audio rate is defined by the host and can be any of 32/44.1/48/96/192 kHz.

    def __init__(self, channels: int, in_hz: int, out_hz: int, in_frames: int) -> None:
        self.__channels = channels
        # A couple of extra frames for the rounding and the resampler's internal delay
        self.__out_frames = (in_frames * out_hz) // in_hz + 16
        self.__buf = ctypes.create_string_buffer(self.__out_frames * channels * 2)
        lib = _load_libspeexdsp()
        err = c_int()
        res = lib.speex_resampler_init(channels, in_hz, out_hz, _SPEEX_QUALITY_DESKTOP, ctypes.byref(err))
        if not res:
            raise _speex_error(lib, err.value, "Can't create resampler")
        self.__res: (c_void_p | None) = c_void_p(res)

    def process(self, data: bytes) -> bytes:
        assert self.__res is not None
        lib = _load_libspeexdsp()
        in_frames = c_uint(len(data) // (2 * self.__channels))
        out_frames = c_uint(self.__out_frames)
        err = int(lib.speex_resampler_process_interleaved_int(
            self.__res, data, ctypes.byref(in_frames), self.__buf, ctypes.byref(out_frames),
        ))
        if err < 0:
            raise _speex_error(lib, err, "Can't resample PCM")
        return self.__buf.raw[:out_frames.value * self.__channels * 2]

    def close(self) -> None:
        if self.__res is not None:
            try:
                _load_libspeexdsp().speex_resampler_destroy(self.__res)
            finally:
                self.__res = None
