# ADR 0003: Transparent slot semantics

Status: Accepted

Only exact RGBA `00000000` maps to transparent slot 0. A pixel whose alpha is zero but whose RGB is non-zero remains a distinct palette color and survives a round trip. This rule is required for pixel equivalence even when a viewer displays both pixels as transparent.
