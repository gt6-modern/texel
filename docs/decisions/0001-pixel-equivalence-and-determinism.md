# ADR 0001: Pixel equivalence and deterministic output

Status: Accepted

PXL round trips are equivalent when width, height, and row-major RGBA8 bytes are identical. PNG file bytes from an imported source need not match because ancillary chunks and deflate representation are not semantic pixels. A compiler must nevertheless produce byte-identical PNG output for the same PXL input, bindings, and assets. Clock, randomness, locale, host path separators, hash iteration order, and filesystem traversal order must not affect output.
