"""The media gateway: audio in from telephony, normalised frames out (`ARCHITECTURE` §6).

Everything above this package is promised 16 kHz mono float32 and never has to ask what a
phone line did. See `audio.py` for the normalisation and `gateway.py` for the fan-out.
"""
