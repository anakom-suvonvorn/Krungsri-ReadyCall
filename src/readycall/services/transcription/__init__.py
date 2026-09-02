"""Turning audio into `TranscriptTurn`s (`ARCHITECTURE` §6).

Three pieces, deliberately separable: `endpointer.py` decides where an utterance begins
and ends and has no model in it, the VAD port supplies the probabilities it consumes, and
`stream.py` is the driver that owns a ring buffer and dispatches finished segments to the
`SttEngine`. Only the last one does any I/O.
"""
