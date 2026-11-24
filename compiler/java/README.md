# Texel compiler for Java

[简体中文](https://github.com/gt6-modern/texel/blob/master/README.zh-CN.md) · [Repository](https://github.com/gt6-modern/texel) · [Normative PXL specification](https://github.com/gt6-modern/texel/blob/master/spec/PXL.md) · [English specification](https://github.com/gt6-modern/texel/blob/master/spec/PXL.en.md)

The Java implementation compiles PXL wire version 1 to deterministic RGBA8/PNG. Production classes target Java 8 bytecode and use only `java.base`; JDK 21 builds them with `--release 8`. It is an independent second implementation, not a port of the Python compiler: the two align through the specification and the conformance corpus, and neither is generated from the other.

Maven Central renders only the POM description for an artifact, so the POM `description` below is the whole Maven-side page for `texel-core` and `texel-cli`. It is generated from `build.gradle`, so it reaches the registry with the next release: the already-published 0.1.0 artifacts keep their one-line description until then.

## Modules and artifacts

| Coordinate | Purpose |
| --- | --- |
| `io.github.gt6-modern:texel-core:0.1.0` | Embeddable compiler, resolvers, PNG encoder, pixel digest, structured PXL errors |
| `io.github.gt6-modern:texel-cli:0.1.0` | Command-line application; the GitHub Release `installDist` archive is the recommended end-user distribution |

Both modules publish a main JAR, a sources JAR, a javadoc JAR and detached signatures. The pair is the Java half of the mirror whose other half is [`texel-importer`](https://pypi.org/project/texel-importer/) and [`texel-compiler`](https://pypi.org/project/texel-compiler/) on PyPI.

```java
FileAssetResolver resolver = FileAssetResolver.rootedAt(assetRoot);
PxlCompiler.Compiled image = PxlCompiler.compile("entry.pxl", resolver);
byte[] png = PngEncoder.encode(image.width(), image.height(), image.rgba());
```

Public returned arrays and collections are defensive copies. `PxlFormatException.code()` exposes stable `PXL001..PXL012` language codes.

## CLI

```console
texel-compiler capabilities --json
texel-compiler compile entry.pxl --asset-root . -o output.png --deps dependencies.tsv
texel-compiler check entry.pxl --asset-root .
```

Exit codes distinguish success, usage/conflict, PXL format failure, and host I/O/resource failure. Output and dependency manifests use temporary files plus atomic replacement; a failed compilation leaves no partial artifact.

## Build

```console
./gradlew --no-daemon clean check -Ppython=python
./gradlew --no-daemon :cli:installDist
```

Windows users may run `gradlew.bat`. If a custom Gradle cache is required, set `GRADLE_USER_HOME` to any writable directory; no machine-specific path is required. Archives are built with fixed timestamps and a stable entry order, so repeated builds of the same sources produce the same JAR bytes. That claim stops at the bundled `.asc` files: an OpenPGP signature packet embeds its own creation time, so a **signed** bundle differs between runs by construction. Byte equality therefore holds for the JARs, the sources/javadoc artifacts and the unsigned bundle — which is what the GitHub Release assets are compared against.

## Guarantees

- Round trips are compared by raw row-major RGBA8 pixels, not PNG file bytes.
- Rendering, dependency manifests and PNG output are deterministic across platforms and locales.
- Color arithmetic is integer-only; operations never create partial pixel coverage.
- Only exact `00000000` uses the transparent slot. RGB values with alpha 0 remain distinct colors.
- No runtime dependency: production code binds to `java.base` only, enforced by a build gate that checks every class major version and module dependency.

## Registries

| Coordinate | Registry |
| --- | --- |
| `texel-importer` | [PyPI](https://pypi.org/project/texel-importer/) |
| `texel-compiler` | [PyPI](https://pypi.org/project/texel-compiler/) |
| `io.github.gt6-modern:texel-core` | [Maven Central](https://central.sonatype.com/artifact/io.github.gt6-modern/texel-core) |
| `io.github.gt6-modern:texel-cli` | [Maven Central](https://central.sonatype.com/artifact/io.github.gt6-modern/texel-cli) |

## Community and security

- Questions: [SUPPORT.md](https://github.com/gt6-modern/texel/blob/master/SUPPORT.md)
- Contributions: [CONTRIBUTING.md](https://github.com/gt6-modern/texel/blob/master/CONTRIBUTING.md)
- Vulnerabilities: [SECURITY.md](https://github.com/gt6-modern/texel/blob/master/SECURITY.md)
- Conduct: [CODE_OF_CONDUCT.md](https://github.com/gt6-modern/texel/blob/master/CODE_OF_CONDUCT.md)

Code is licensed under the [Apache License 2.0](https://github.com/gt6-modern/texel/blob/master/LICENSE); attribution notices are in [NOTICE](https://github.com/gt6-modern/texel/blob/master/NOTICE).
