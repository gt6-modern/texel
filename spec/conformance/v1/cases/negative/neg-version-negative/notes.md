version -1 越出通用分派域 0..65535，按 v1§2 是 PXL001（域内不支持才是 PXL007）。

这条走**生产入口**（Java 的 PxlCompiler、Python 的 v1 路径），不是 v0 的 PxlParser——
F01 之所以长期漏网，就是因为门禁一直只经过 PxlParser 里那份正确的域判定。
