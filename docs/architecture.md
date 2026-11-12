
# Architecture

Texel has three independent product paths: the Python PNG importer, the Java compiler, and the Python compiler. The importer reads external PNG files and emits canonical PXL. Both compilers read PXL version 1, resolve its static dependency graph, substitute typed bindings, render integer RGBA8 pixels, and encode deterministic PNG output. The compilers share only the normative specification and conformance corpus.

`texel-core` has no runtime module beyond `java.base`; both Python packages use only the standard library. Build integrations enumerate targets and map outputs but never reimplement language semantics.
