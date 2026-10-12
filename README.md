# Abaqus EPS 表面网格重建

**Surface Mesh Rebuild v0.2.2** — 为 Adobe Illustrator 中打开的 Abaqus EPS 重建可见表面网格，减少彩色云图遮挡网格造成的断线，避免把底部隐藏网格一起置顶。

Reconstruct visible surface mesh lines in Abaqus EPS exports using the corresponding ODB or a verified reusable model template. A Windows Illustrator JSX front end calls a Python geometry backend; the original contour artwork and annotations are retained.

> 当前工具针对特定 Abaqus 矢量 EPS 结构，并非任意 EPS 修复器。项目与 Adobe、Dassault Systèmes 无隶属关系。

## 下载与使用

普通用户请到 **[Releases](https://github.com/qiukefan/rebuild_Abaqus_odb_eps/releases)** 下载维护者上传的完整运行包。GitHub 自动生成的 **Source code (zip)** 只含源码，不含 Python 环境。若尚无完整运行包，可按下方“从源码运行”配置环境。

1. 完整解压运行包，保留 `SurfaceMeshRebuild_v0.2.2.jsx` 与 `v0.2.2` 文件夹的相对位置。
2. 在 Illustrator 打开 **由 Abaqus 直接导出的原始 EPS**。
3. 选择 **文件 → 脚本 → 其他脚本**，运行 `SurfaceMeshRebuild_v0.2.2.jsx`。
4. 数据来源选择 **选择 ODB 并重建**，选择当前 EPS 对应的 ODB。
5. 点击 **重建并打开副本**。分析步和帧编号通常留空，程序自动匹配。
6. 完成后关闭脚本窗口，查看新文档，并按需要另存为 AI。

**不要用 Illustrator 保存覆盖原始 EPS。** 重保存会改变 PostScript 结构。已有编辑请另存保留，再打开原始 Abaqus EPS 重建。更多操作见 [使用说明](表面网格重建_v0.2.2_使用说明.txt)。

## 两种工作方式

| 模式 | 输入 | 行为 |
| --- | --- | --- |
| ODB 重建 | 当前 EPS + 对应 ODB | 自动读取 EPS 的 Step、Step Time、变量名，以及存在时的增量与 ODB 名称；匹配帧后读取模型及位移，计算投影和遮挡。 |
| 模型模板重建 | 当前 EPS + `.meshcache.json` | 复用网格连接关系，从当前 EPS 恢复当前节点位置，重新校验表面和可见性；无需读取 ODB。 |

程序保留彩色云图、色标和文字，替换经过配准的黑色网格线。它不重新运行有限元分析，也不根据变量名重新计算云图。

## v0.2.2：修复 Windows 进度文件冲突

修复 Illustrator 读取进度时，后台替换 `status.json` 偶发出现 `PermissionError: [WinError 5] Access is denied`、导致任务中止的问题。短暂文件占用会有限重试；普通进度更新暂时失败不会中断重建，任务结束后通过独立终态文件交付完成、错误或取消状态。实际结果和缓存写入仍检查错误，不会把写入失败当作成功。

从旧版升级时，先停止旧任务并关闭旧版脚本窗口，将新版完整解压到独立目录，再用 `SurfaceMeshRebuild_v0.2.2.jsx` 打开原始 EPS 重试。**这项修复不需要管理员权限，也不需要修改 ODB。** 不要只替换 JSX 或把不同版本的后端混在一起。

## v0.2.1 起支持：统一与分方向变形倍率

直接读取当前 EPS 的文字标注，无需手工输入倍率：

```text
Deformation Scale Factor: +1.000e+01
Deformation Scale Factor: x = +1.000e+00  y = +1.000e+00  z = +1.000e+01
```

第一行表示统一 10 倍，第二行表示 X=1、Y=1、Z=10。配准、三维遮挡与模板生成统一使用 `原始坐标 + [X倍率, Y倍率, Z倍率] × 位移`，不对原始模型坐标整体拉伸。结果报告记录倍率模式、三个分量及原标注。

新版相机导出脚本同时保存 AUTO / UNIFORM / NONUNIFORM 及有效倍率，用于记录与诊断；**重建仍以当前 EPS 标注为准**。旧相机 JSON、ODB 帧缓存及模板参考倍率不会覆盖当前 EPS 的倍率。缺失、冲突或不完整的倍率标注会明确报错。

倍率变化较大时，请使用 ODB 模式。免 ODB 模板仍须满足“小投影变化、无歧义、覆盖可验证”的条件，不保证 1 倍模板能处理 10 倍 EPS。v0.2.0 / v0.2.1 的 `.meshcache.json` 可以交给新版校验，但新版模板不供旧版使用。

同模型、同视角、更换工况或帧时，可以尝试模板模式。它不会复制旧帧位移或直接套用旧可见性；节点歧义、视角变化、面重叠、证据不足等情况会拒绝复用，提示使用当前 ODB。

ODB 模式另有模型与帧缓存：相同模型共享原始拓扑，不同 ODB / 帧的位移分别保存。ODB 文件大小或修改时间变化后重新读取。

## 支持范围

| 项目 | v0.2.2 |
| --- | --- |
| 操作系统 | Windows 64 位 |
| Illustrator | v0.2.0 曾在 29.6.1 实机验证；本版未重做原生 UI 导入 |
| ODB 读取 | 需要兼容 ODB 的 Abaqus；已使用 Abaqus 2024 验证 |
| EPS | 原始 Abaqus 单视图矢量 EPS，带可识别的分析步、时间和变形缩放信息 |
| 网格 | 单个实例，C3D20 / C3D20R 实体外表面 |
| 变形与投影 | 变形变量 U；EPS 中明确的统一或 X/Y/Z 分方向变形倍率；透视视图，且配准必须通过 |
| 模板复用 | 同模型、同视角、投影变化较小；遮挡与覆盖须可验证 |

包内相机参数只是候选视角，每份 EPS 都重新配准。其他视角可在 Abaqus 恢复 EPS 导出视图后运行 [相机导出辅助脚本](v0.2.2/ExportCurrentView_v0.2.2.py)，再在 Illustrator 脚本高级设置中选择该 JSON。

操作路径：Abaqus **File → Run Script** → 上述辅助脚本 → 保存到新 JSON 文件；Illustrator 勾选 **高级设置**，在 **相机 JSON** 填写完整路径（不加引号）。辅助脚本记录当前视口，请保持与 EPS 导出时一致的视图。名为 `camera.json` 或 `view.json` 且与 EPS / ODB 同目录时也会自动查找。

暂不支持任意 Illustrator 重保存 EPS、多实例、多视图排版或其他单元类型。当前没有 GPU 后端。

## 输出与进度

- 默认在源 EPS 旁的 `SurfaceMeshRebuild_v0.2.2` 文件夹生成新的 EPS 和 JSON 校验报告。
- 在新 Illustrator 文档打开结果，不覆盖原 EPS / ODB，不自动保存为 AI。
- 提供阶段进度、计时及取消；Abaqus 首次读取与 Illustrator 最终导入可能需要等待。
- 设置与默认缓存位于 Windows 用户数据目录的 `SurfaceMeshRebuild/v0.2.2`；模板路径显示在窗口中。
- 报告、缓存及日志可能包含文件路径和模型信息，反馈问题前请检查需要公开的内容。

## 从源码运行

源码仓库不包含 Abaqus、Illustrator、Python 二进制、真实 ODB/EPS 或计算缓存。

安装 Windows x64 CPython **3.12**，在仓库根目录执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

在 Illustrator 运行 JSX，在“高级设置 → Python 路径”填写 `.venv\Scripts\python.exe` 的完整路径。未使用完整运行包时，此步骤必需。

如果找不到 Abaqus，在“Abaqus 命令”填写启动器路径，例如 `C:\ABAQUS\2024\Commands\abaqus.bat`。Abaqus 自带 Python 只负责 ODB 提取，不要把它填入几何后端 Python 路径。

`requirements.txt` 指定兼容范围，不是二进制发布包的逐字节重现锁文件；实测版本见 [验证记录](v0.2.2/验证记录_v0.2.2.txt)。

## 测试

无需 Abaqus 或 Illustrator 的公共自动测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s v0.2.2/tests -p "test_*.py" -v
node test-surface-mesh-v0.2.2.cjs
```

前端 mock 测试需要 Node.js 18 或更新版本。Windows 进程取消测试仅在 Windows 执行。真实 EPS 的几何对照项在没有私有测试数据时跳过；自动测试不等于真实宿主验证。

v0.2.0 已使用同一模型的两份实际工况文件验证 ODB 重建、双向模板复用及 Illustrator 导入，首次完整 ODB 重建实测约 42 秒、直接模板计算约 2 秒。v0.2.1 新增统一与分方向变形倍率回归，v0.2.2 修复进度文件并发读写；当前版本的实测结果、误差及限制见 [验证记录](v0.2.2/验证记录_v0.2.2.txt)。耗时依机器与文件变化，真实模型数据不随源码发布。

## 构建完整运行包

使用完整安装的 Windows x64 CPython 3.12（含 `Lib`、`DLLs` 和许可证），先安装依赖到独立目录：

```powershell
py -3.12 -m pip install --only-binary=:all: --target .build/deps -r requirements.txt
py -3.12 v0.2.2/build_portable_runtime.py --python-root "C:\Python312" --deps-root ".build\deps"
py -3.12 v0.2.2/build_release.py
```

将 `C:\Python312` 改为实际安装目录。构建工具拒绝覆盖已有 `runtime` / `_deps`，重新构建请使用干净的源码检出目录。输出为根目录的 `SurfaceMeshRebuild-v0.2.2-windows-x64.zip`，用于 Releases，不提交进 Git 历史。

## 目录

```text
SurfaceMeshRebuild_v0.2.2.jsx  Illustrator 界面与后台任务入口
v0.2.2/backend/               EPS、ODB 缓存、配准、遮挡及模板重建
v0.2.2/resources/             需要实际配准验证的相机候选
v0.2.2/tests/                 公共单元测试
v0.2.2/launch_job.vbs         Windows 隐藏启动器
docs/                        发布步骤与版本说明
```
## 效果
原图：
<img width="950" height="500" alt="image" src="https://github.com/user-attachments/assets/dfe62e48-ef3e-4a20-a86a-4ccb4f36652c" />
处理后：
<img width="950" height="500" alt="image" src="https://github.com/user-attachments/assets/6fdf94f1-20d0-4d3e-896d-fc23853be71f" />


## 反馈与贡献

欢迎通过 [Issues](https://github.com/qiukefan/rebuild_Abaqus_odb_eps/issues) 提交问题。建议附上版本、Windows / Illustrator / Abaqus 版本、模式、单元类型、错误文字及可公开的最小复现文件。请勿上传未获授权的 ODB、研究数据或带私人路径的完整缓存。

修改代码后请运行公共测试；几何变更还应说明实际文件验证与已知限制。

## 许可证

本项目原创代码采用 [MIT License](LICENSE)，Copyright © 2026 Kefan Qiu。第三方运行时和依赖保留各自许可证，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。Abaqus 和 Adobe Illustrator 需用户另行安装及授权。

维护者首次发布见 [GitHub 发布步骤](docs/PUBLISHING.zh-CN.md)。
