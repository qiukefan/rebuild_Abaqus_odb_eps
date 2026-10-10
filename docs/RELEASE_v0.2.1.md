# Surface Mesh Rebuild v0.2.1

为 Illustrator 中打开的原始 Abaqus EPS 选择对应 ODB，自动匹配 Step / Step Time，重建可见表面网格。保留云图、色标和文字，结果在新文档打开。

同模型、同视角的小幅变化可复用 `.meshcache.json` 模板，无需再次读取 ODB。校验失败时需使用对应 ODB。

## 本版新增

- 自动识别 EPS 中统一倍率（如 `+1.000e+01`）和分方向倍率（如 `x = +1.000e+00 y = +1.000e+00 z = +1.000e+01`）。
- 视角配准、三维遮挡与模板生成都按 X/Y/Z 分量缩放位移，结果报告记录实际倍率。
- 相机导出辅助脚本保存视角及变形模式/倍率；当前 EPS 标注始终是重建倍率依据。
- 缺失、冲突或残缺的倍率标注明确拒绝。倍率变化较大时使用 ODB 模式，免 ODB 模板仍有同视角、小投影变化限制。
- v0.2.0 保留可下载；解压新版后运行新版 JSX 即可，勿将不同版本的 JSX 与后端文件混用。

## 下载

普通 Windows 用户下载维护者上传的 **`SurfaceMeshRebuild-v0.2.1-windows-x64.zip`** 并完整解压。GitHub 自动生成的 **Source code** 不含 Python 环境。

## 要求与使用

- Windows 64 位、Illustrator；首次读取 ODB 需要兼容的 Abaqus。
- 原始 Abaqus 矢量 EPS，保留 Step / Step Time / 变形倍率标注；单实例 C3D20 / C3D20R；U 变形；可验证的透视视角。
- 已验证 Illustrator 29.6.1、Abaqus 2024，其他类型、视角或版本的适用性有限。

打开原始 EPS → 文件 / 脚本 / 其他脚本 → `SurfaceMeshRebuild_v0.2.1.jsx` → 选择 ODB → 重建并打开副本。

运行包包含 Python 与几何依赖，提供进度、计时和取消。原创代码采用 MIT，第三方组件保留各自许可证。详细限制和验证见 README。
