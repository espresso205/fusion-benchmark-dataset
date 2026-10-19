# Fusion Benchmark Dataset · Q01–Q10

管理员私有题库。收录十道单视角 DXF 建模题、标准 STEP、GT 派生标注及必要的来源参数和生成源码。数据以毫米、固定世界坐标解释；迁移保留题面、标准模型和标注的原始字节。

**`public/` 是考生输入权限分区，不代表可以向公众发布。** 本仓库用于用户授权的管理员私有备份与内部使用。原始来源文件明确声明许可证未知、该声明不授予再分发权限；本次整理不改变许可状态，也不设新的开源许可。请阅读 [DATA_PROVENANCE.md](DATA_PROVENANCE.md)。

```text
public/Q01–Q10/       TASK.md + drawing.dxf；每次只发当前题目录
private/Q01–Q10/      gt.step、annotations.json、参数、视图与特征规范
private/_sources/    原始生成器、15 个辅助模块及原依赖记录
data_manifest.json   相对路径、来源位置、文件大小和 SHA256
scripts/             标准库校验/配置工具，另有可选几何重建入口
```

普通使用只需 Python 3.10 或更高版本，不依赖 Fusion、OCCT 或网络。可以直接使用任一已解压便携插件包或评分器包中的 `runtime/python/python.exe`，无需额外安装 Python：

```powershell
python scripts/verify_dataset.py
python scripts/prepare_config.py --output local/private_cases.json
```

使用便携解释器时，将以上 `python` 替换为该包中 `runtime/python/python.exe` 的完整路径；脚本也可用完整路径从任意工作目录调用。

生成的配置使用这台机器的绝对路径，可交给评分器的 private-cases 入口；不含候选答案或历史成绩。移动仓库后重新生成即可。详见 [DATASET_USAGE.md](DATASET_USAGE.md)。

发布附件有两种：

- `fusion-benchmark-dataset-candidate-inputs-20261019.zip`：仅含 `public/` 的内容，供授权内部评测发题。
- `fusion-benchmark-dataset-admin-full-20261019.zip`：整个管理员题库，含标准答案与生成参数，只发给获准管理员。

考生不应获得仓库访问权或管理员 ZIP。`public/` 与 `private/` 的路径分隔不是操作系统隔离；评测时应由独立账户、容器、虚拟机或实际访问控制保证考生只能读取当前题输入。

本仓库不包含历史候选提交、历史成绩、运行日志、API 凭据或本机配置。保留的源码用于来源复核，不代表本次重新运行了原生成流程或复现了旧认证结论。
