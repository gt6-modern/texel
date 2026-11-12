
# 参与 Texel 开发

[English](CONTRIBUTING.md)

感谢参与 Texel。参与即表示同意遵守[行为准则](CODE_OF_CONDUCT.zh-CN.md)。

## 基本规则

1. 行为变化先修改中文权威规范，再改实现。
2. 先补 conformance 正反例，再改变实现。
3. Java 与 Python 语义对齐，但不共享生产代码。
4. 不新增生产运行时依赖；发布工具依赖必须有明确决策记录。
5. 保持输出确定性，并如实列出实际运行的门禁。

## 开发环境与门禁

需要 Python 3.8+、用于构建 Java 8 字节码的 JDK 21、Git；Gradle 集成探针另需 Bash。

```console
python spec/conformance/tools/gen_cases.py --all
python -m unittest discover -s tests
python spec/conformance/tools/python_ref_verify.py
python compiler/python/tools/conformance.py
cd compiler/java
./gradlew check -Ppython=python
```

PR 必须说明行为变化、链接规范条款与语料，必要时同步两个实现，并同步更新中英文公开文档。
