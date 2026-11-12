# Build integrations

Integrations are thin adapters around a conforming Texel compiler. They enumerate targets, pass an asset root and bindings, map outputs, and use the dependency manifest for incremental builds. They must not reimplement PXL parsing, rendering, PNG handling, error priority, or budgets.

The current Gradle example is in [`gradle/`](gradle/). Its verification scripts build a compiler distribution and exercise incremental invalidation and path/output conflict handling in isolated temporary copies.
