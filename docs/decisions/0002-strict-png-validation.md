# ADR 0002: Strict PNG validation

Status: Accepted

Every PNG chunk CRC, structural constraint, zlib stream, Adler checksum, filter byte, and exact inflated length is validated before pixels are accepted. Unknown ancillary chunks may be ignored only after CRC validation. Ten upstream sample files contain a damaged `iCCP` CRC; they remain negative fixtures rather than creating a lenient decoder mode. This keeps malformed-file acceptance deterministic across implementations.
