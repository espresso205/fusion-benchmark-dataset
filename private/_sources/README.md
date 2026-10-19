# 管理员来源源码快照

`challenge_generator.py` 来自原题考官包；十题携带版本的 SHA256 相同，因此只保留一份。其余 15 个 Python 文件是原项目 `src/` 中的递归本地导入闭包，按 2026-10-19 所见字节保存。所有文件及原位置均登记在根 `data_manifest.json`，本目录不向考生提供。

原生成器的 `build_model(definition)` 使用每题 `source_definition.json` 的解析参数构造标准几何。`scripts/regenerate_reference.py` 只调用该建模函数并导出新 STEP，不执行完整旧题包生成、认证或评分流程。

普通数据校验和配置生成只需要 Python 标准库。可选几何重建的源码导入涉及 `OCP`（cadquery-ocp）、`ezdxf`、`numpy`、`scipy`；参考来源记录为 `requirements.original.lock.txt`，其中还包含原项目其他功能的依赖。该文件原样保留，不代表已重新安装或验证这些版本。

完整 `challenge_generator.py` 的 `generate_set()` / `_task()` 另外引用旧 `cadpy.step_evaluator` 与 `cadpy.step_assessment`，本仓库没有捆绑该外部旧评分项目。请勿把完整旧认证流程的可用性与上述独立标准模型构造混为一谈。

源文件自述为本地解析构造，不依赖外部模型资产；其许可仍未确认。每题 `SOURCE_LICENSE.txt` 的限制继续适用，本快照不授予额外开源或再分发权利。保留源码不是本次重建、认证或 Fusion 测试的证明。
