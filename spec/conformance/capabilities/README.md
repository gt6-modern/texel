# Capability profile fixture

`pxl-v1.json` is the canonical capability response for Texel 0.1.0. Both
compiler implementations and the CLI must emit the same keys and values.
The profile accepts only wire version 1 and uses the `pxl-png` PNG profile.

Run `python tools/check_release_metadata.py` and
`python -m unittest tests.test_capabilities` after changing it.
