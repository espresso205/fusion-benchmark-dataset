# 数据使用与迁移

## 管理员首次使用

1. 将管理员仓库或管理员 ZIP 放在考生无法访问的位置。
2. 用 `python scripts/verify_dataset.py` 验证所有保留来源文件的 SHA256、标注与 GT 的绑定，以及考生分区的允许文件列表。
3. 用 `python scripts/prepare_config.py --output local/private_cases.json` 生成本机配置。`local/` 不进入 Git 或发布 ZIP。
4. 将配置路径提供给评分程序，候选模型路径由本次运行另行提供。

`prepare_config.py` 只使用标准库。生成 JSON 的顶层是 `annotation_kind`、`coordinate_frame`、`cases`；每题含 `case`、`gt`、`annotations`、`expected_hashes`。路径为当前数据根目录下已校验文件的绝对路径，不复制旧候选提交路径或成绩路径。输出默认禁止覆盖已有文件；确需覆盖本机配置可加 `--force`。仓库内的配置必须写进 `local/`；也可指定仓库外的管理员路径。脚本拒绝写入冻结数据或维护文件位置。

可以选择子集：

```powershell
python scripts/prepare_config.py --cases Q01 Q02 --output local/private_cases_q01_q02.json
```

移动或重新克隆仓库后重新生成配置，不必编辑数据文件。也可从其他工作目录调用脚本的完整路径，或显式指定 `--root`。路径包含中文与空格时用引号包围命令参数。

## 给考生的输入

每次只复制 `public/Qxx/`，其中只有原始 `TASK.md` 与 `drawing.dxf`。原始题面没有附图，因此未添加任何新图或从答案渲染的预览。考生按任务要求提交可独立重开的 `submission.f3d`，评分端负责可信重开与 STEP 导出。

`public/INSTRUCTIONS.md` 保留原题包的发题说明字节。其中预算是原始建议，不是软件自动执行的限额；正式评测开始前应统一冻结实际预算与工具版本。目录名中的 public 仅表示可交付给授权考生的输入，不表示有向公众传播的许可。

管理员完整仓库、`data_manifest.json`、`private/` 以及生成源码不得交给考生。仅把目录改名或在同一账户下建立两个文件夹不能防止越权读取。

## 数据与评分语义

- 单位：毫米；坐标：`fixed_world_mm`，不进行拟合平移、旋转、镜像或缩放。
- GT：保留可信映射指定的原始 `conversion/source.step`，不是 `source_aligned.step`。
- 标注：`gt_derived_probe`，是在冻结 GT 上自动派生的解析直径/计数、平面跨度和截面探针；**不是独立人工图纸真值**。其原始 provenance 字段保持不变。
- 每个 `annotations.json` 的 `gt_sha256` 必须匹配同题 `gt.step`。
- 数据仓库不定义总分、权重或测量后端；这些由版本化评分程序定义。

## 可选来源重建

`private/_sources/` 保存原生成器及本地导入闭包。维护脚本不需要其 CAD 依赖。需要复核生成几何时，按该目录 README 准备独立环境，再运行：

```powershell
python scripts/regenerate_reference.py --case Q01 --output local/rebuilt_Q01.step
```

该命令读取保留的参数并调用原始建模函数，不覆盖冻结 GT。新 STEP 的时间戳、OCCT 序列化和版本可能改变字节，因此不能用重建文件的 SHA256 替代冻结文件哈希；几何等价需另行检验。本次整理没有运行该可选几何重建。

## 维护

`data_manifest.json` 记录所有 99 个原样复制文件的相对路径、大小、哈希和来源别名。根目录 `SHA256SUMS` 额外覆盖维护文档与脚本；`public/SHA256SUMS` 只覆盖考生包内容。`.gitattributes` 禁止 Git 自动换行转换，以保留原字节。

校验成功只说明文件完整性、映射一致性和已定义的公开文件边界检查通过，不等于操作系统访问隔离、外部再分发授权或几何重建验证。
