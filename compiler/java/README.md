# Texel compiler for Java

[Repository documentation](../../README.md) · [Normative PXL specification](../../spec/PXL.md)

The Java implementation compiles PXL wire version 1 to deterministic RGBA8/PNG. Production classes target Java 8 bytecode and use only `java.base`; JDK 21 is used to build with `--release 8`.

## Build

```console
./gradlew --no-daemon clean check -Ppython=python
./gradlew --no-daemon :cli:installDist
```

Windows users may run `gradlew.bat`. If a custom Gradle cache is required, set `GRADLE_USER_HOME` to any writable directory; no machine-specific path is required.

## Modules and artifacts

- `io.github.gt6-modern:texel-core:0.1.0`: embeddable compiler, resolvers, PNG encoder, pixel digest, and structured PXL errors.
- `io.github.gt6-modern:texel-cli:0.1.0`: command-line application; the GitHub Release `installDist` archive is the recommended end-user distribution.

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
