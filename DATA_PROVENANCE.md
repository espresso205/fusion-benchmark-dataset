# 数据来源、许可与字节保真

## 迁移范围

本次整理以明确指定的 `singleview_benchmark_v2/deliverables/challenge_20260916/` 为原题来源，日期为 2026-10-19。该原目录入口 README 指向另一个后续预览发布目录；本次没有切换题面或替换标准答案，而是遵循指定来源与可信评分映射中的 GT SHA256。

来源别名与选择规则如下。这里使用逻辑相对路径，不保存旧机器的绝对路径。

| 来源别名 | 保留内容 | 处理 |
| --- | --- | --- |
| `challenge_20260916` | `agent_questions/Qxx/TASK.md`、`drawing.dxf`、原发题 README | 原字节复制；原 README 命名为 `public/INSTRUCTIONS.md` |
| `challenge_20260916` | `examiner_bundle/Qxx/conversion/source.step` | 原字节复制为 `private/Qxx/gt.step` |
| `gt_derived_annotations_20261017` | `Qxx/annotations.json` | 以可信映射锁定的文件和 SHA256 原字节复制 |
| `challenge_20260916` | 每题 `source_definition.json`、`feature_manifest.json`、`view_contract.json`、`LICENSE.txt` | 管理员区原字节保留；许可证文件重命名为 `SOURCE_LICENSE.txt` |
| `challenge_20260916` | 各题同字节的 `challenge_generator.py` | 去重保存一份，未改变文件内容 |
| `singleview_benchmark_v2_source_snapshot` | 原项目 `src/` 内 15 个被导入辅助模块及原依赖清单 | 2026-10-19 所见快照，原字节复制；不声称有历史 Git 提交证明 |

每份原样复制文件的大小、SHA256 和原相对路径均见 `data_manifest.json`。可信评分映射记录的文件名和自身 SHA256 被保留，用于审计数据选择；原映射本身含旧候选路径，因此没有复制进仓库。十对 GT/标注哈希均与其一致。

未收录原题包的旧评分配置、认证报告、认证重建模型、旧统计结果、预览、机器路径日志，以及后续候选提交和成绩。必要的模型参数、视图规范与生成源码只存放在管理员私有分区。

## 来源与许可限制

各题 `source_definition.json` 声明 `sourceRole=locally_constructed_analytic_challenge`。生成器的定义函数说明其使用毫米制显式参数、不依赖外部模型资产。这些是保留源码中的来源陈述，不能替代版权授权证明。

十题原 `LICENSE.txt` 内容一致，并明确写道：

> SOURCE LICENSE UNKNOWN. No redistribution permission is granted by this generated file.
> Obtain and review the original STEP license before publishing this sample.

该声明在每题 `private/Qxx/SOURCE_LICENSE.txt` 原字节保留，亦随考生输入包携带。未找到可确认的题库及生成源码开源许可证；本仓库不自行授予 MIT、Apache、CC 或其他再分发许可。

本次仓库整理仅用于用户授权的管理员私有备份与内部使用，不因此取得向公众发布、向第三方再分发或二次许可的权利。`public/` 是输入权限分区名；考生 ZIP 的存在不表示可以公开上传。私有 GitHub 仓库设置也不改变上游许可条件。

## 标注语义与重建边界

`annotations.json` 自述为冻结后从 GT BRep 自动派生的直径、计数、轴向平面跨度与截面探针，属于探索性适配。它不是原题独立图纸标注，不应描述为原始论文/数据集的官方评测真值。

提供的可选重建入口调用原始解析建模函数。完整旧题包生成流程还会调用旧 `cadpy` 认证/评分模块，该外部项目不属于本数据仓库。当前保留的辅助源码足以列明本地导入关系；本次未安装旧环境、未生成新 GT，也未验证重新生成的几何或字节等价。原 `requirements.original.lock.txt` 是来源快照，不是维护脚本依赖，也不是已验证可重建环境的承诺。

## 冻结 GT 与标注哈希

下表由可信映射核验后的数据清单生成。

| 题号 | GT SHA256 | 标注 SHA256 |
| --- | --- | --- |
| Q01 | `633183f16dfbfb3f036a6c8a51e86dd7741fc8660c21f583b4b8d6fe81766a79` | `0e87037819a42eb77d2bd58e62941e436224ce8569276ebbf3ce448bbb636fd8` |
| Q02 | `ccc1d3e4d30140cf51ee4aa07652574a1af8d9343482be44fa72649823e810cc` | `f101c7c8bd0fc860ae7a98ab2d93edb3172b927f8bdaac1dbdcedd5243078081` |
| Q03 | `505482f345d6f83583eaaae6dcf13ddd8773a7c8b54964480958a06206882d51` | `fd0d9bbbd3d29320976abdf2b689f6583cf58e46a5aae7c23c71ede20d621c84` |
| Q04 | `14d913f1bfadc888e8b21198ff260e9d3db4a5b7b53f7b5d1784b8667aea6f38` | `2ce625884731d004b7dbca6c86287994cafa688a16fd6249b8b277c0d270a43f` |
| Q05 | `8f86a3620448e18b25ee1e04713ff88a627b09cb96fcc6910690ac53ecb9763d` | `cd2ec8acebcdc184242b9b1f98c758437cc001985592a5588e2ca5cf0ff7fd1d` |
| Q06 | `a257b78b686183602aba1a36f11a37e325d9ed9692fa07139d001b6ca6d5eafb` | `9065313a4bd73053ad26d254f4cd5ad5ec31673442fa238f6b86270fb5d6774f` |
| Q07 | `160c34cc490c2bb56c9c1f564513cbd548de960bd774c0d891df146afa7d5375` | `45443d748031ecb8e2c72a5694bd587dcbab2bdabb3f0d18e6654ca5c76e797d` |
| Q08 | `80ef8eb23c684ac4d3a17bda5911b4392c2afe42afbdb3901a50cfad9a14256c` | `f57dc60bb60bfc8b419c542183957dca4e5846ce24132c7b2f223c0ba6f5e8c2` |
| Q09 | `df103729778fc2e6b49f39b30e97d808b339a43304077e36a81707915a957698` | `aed671951656eee5a54d76a5f4a19d9d8c4f40e7af3f1cd3df60bd47d7a3104a` |
| Q10 | `595b3125a957afdf6a85e4ed23344ad97a526a080086e13aeb2fecff4bf9a227` | `1a8a2a025ee6ea1dfc1cf4959d32e0587245e7de8677711aad1df20dab00b206` |
