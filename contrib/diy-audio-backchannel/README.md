# DIY Audio Backchannel

Experimental mic-only audio backchannel for DIY PiKVM V2 on Raspberry Pi 4.

The normal V3/V4 audio path expects HDMI audio capture from the target host and
a USB UAC2 playback gadget for the browser microphone. A DIY V2 build usually
does not have the HDMI audio capture side, but the outgoing browser microphone
can still be useful for voice applications on the target host.

This overlay enables only the USB microphone part of the OTG gadget:

```yaml
otg:
    devices:
        audio:
            enabled: true
            speakers:
                enabled: false
            mic:
                enabled: true
```

Install it on the PiKVM as root:

```console
rw
cp override.yaml /etc/kvmd/override.d/9900-diy-audio-backchannel.yaml
reboot
```

After reboot:

```console
kvmd-otgconf
aplay -l
```

Expected:

- `kvmd-otgconf` lists `uac2.usb0`.
- ALSA lists a playback device similar to `UAC2Gadget`.
- The target host sees a USB microphone.

This also needs a uStreamer Janus plugin that supports mic-only mode. Without
that change the Web UI can show the microphone switch only when HDMI audio
capture is available.
