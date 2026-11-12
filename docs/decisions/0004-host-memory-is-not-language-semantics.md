# ADR 0004: Host memory is not language semantics

Status: Accepted

The language defines deterministic document and compilation-session budgets, but it does not promise a minimum resident-memory implementation. A valid input that exhausts host memory is reported as a controlled host/CLI failure and must leave no partial PNG or dependency manifest. It is not reclassified as `PXL011`; a future implementation may reduce its memory footprint without changing the accepted language.
