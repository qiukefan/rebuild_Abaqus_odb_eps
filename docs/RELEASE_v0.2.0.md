# Surface Mesh Rebuild v0.2.0

为 Illustrator 中打开的原始 Abaqus EPS 选择对应 ODB，自动匹配 Step / Step Time，重建可见表面网格。保留云图、色标和文字，结果在新文档打开。

同模型、同视角的小幅变化可复用 `.meshcache.json` 模板，无需再次读取 ODB。校验失败时需使用对应 ODB。

## 下载

普通 Windows 用户下载维护者上传的 **`SurfaceMeshRebuild-v0.2.0-windows-x64.zip`** 并完整解压。GitHub 自动生成的 **Source code** 不含 Python 环境。

## 要求与使用

- Windows 64 位、Illustrator；首次读取 ODB 需要兼容的 Abaqus。
- 原始 Abaqus 矢量 EPS；单实例 C3D20 / C3D20R；U 变形；可验证的透视视角。
- 已验证 Illustrator 29.6.1、Abaqus 2024，其他类型、视角或版本的适用性有限。

打开原始 EPS → 文件 / 脚本 / 其他脚本 → `SurfaceMeshRebuild_v0.2.0.jsx` → 选择 ODB → 重建并打开副本。

运行包包含 Python 与几何依赖，提供进度、计时和取消。原创代码采用 MIT，第三方组件保留各自许可证。详细限制和验证见 README。
