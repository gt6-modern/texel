
# Contributing to Texel

[简体中文](CONTRIBUTING.zh-CN.md)

Thank you for improving Texel. By participating, you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Ground rules

1. The normative Chinese specification changes before implementations.
2. Add or update conformance cases before changing behavior.
3. Keep Java and Python semantically aligned without sharing production code.
4. Add no production runtime dependency. A release-tool dependency requires an explicit decision record.
5. Preserve deterministic output and report exactly which checks were run.

## Development setup

- Python 3.8 or newer
- JDK 21 for building Java 8 bytecode
- Git; Bash is required for the Gradle integration probes

```console
python spec/conformance/tools/gen_cases.py --all
python spec/conformance/tools/check_spec_sync.py
python tools/check_release_metadata.py
python -m unittest discover -s tests
python spec/conformance/tools/python_ref_verify.py
python compiler/python/tools/conformance.py
cd compiler/java
./gradlew --no-daemon clean check -Ppython=python
```

Pull requests must explain the behavior change, link the corresponding specification clauses and corpus cases, update both language implementations when applicable, and update both English and Chinese public documentation.
