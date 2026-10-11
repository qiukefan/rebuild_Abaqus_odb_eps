# 第一次发布到 GitHub

本项目仓库：[qiukefan/rebuild_Abaqus_odb_eps](https://github.com/qiukefan/rebuild_Abaqus_odb_eps)。以下步骤供维护者使用；只想运行脚本的用户请看 [README](../README.md)。

## 先认识几个词

| 名称 | 作用 |
| --- | --- |
| README | 仓库首页，告诉别人程序能做什么、如何安装和使用。 |
| LICENSE | 开源许可证；本项目已采用 MIT，保留 Kefan Qiu 的版权署名。 |
| Git | 在本机记录代码版本的工具。 |
| commit（提交） | 在本机保存一次代码快照，不会自动上传。 |
| push（推送） | 把已提交的版本上传到 GitHub。 |
| Release（发布版本） | 为某次版本附上说明和可下载的完整运行包。 |

## 已有仓库，不必重新创建

仓库已完成首次发布，`origin` 已连接到上述仓库。后续版本在 `codex/` 开头的本地分支准备，再按下面步骤上传。不要重新 `git init`、删除 `.git` 或强制推送。

源码包括 JSX、Python 后端、公开测试、构建脚本和说明。`.gitignore` 使用允许清单，排除 ODB、EPS、缓存、旧实验、Python 安装目录与 ZIP。以后新增源码文件时，记得按需更新允许清单。

## 1. 打开项目终端

在 Windows 文件资源管理器打开本项目文件夹，右键选择“在终端中打开”。以下命令都在仓库根目录执行，不要进入 `v0.2.2` 子目录。

```powershell
git status
git remote -v
```

远程地址应为本项目 GitHub 地址。`git status` 会列出本地尚未提交的变化。

## 2. 设置提交署名（本机此仓库设置一次）

以下署名与此仓库已有 GitHub 初始提交一致，邮箱是 GitHub 的公开 noreply 地址：

```powershell
git config --local user.name "Kefan Qiu"
git config --local user.email "56949475+qiukefan@users.noreply.github.com"
```

其他贡献者应替换为自己的 GitHub 姓名及邮箱。署名不等于登录，不需要在命令中填写密码或访问令牌。

## 3. 检查并提交源码

```powershell
git add .
git diff --cached --stat
git diff --cached --name-only
```

检查列表应当是代码和说明，不能包含真实 ODB、EPS 或缓存。确认内容后执行：

```powershell
git commit -m "Fix Windows progress file conflicts in v0.2.2"
```

如果提示没有需要提交的变化，可用 `git log -1 --oneline` 查看最近记录，无需重复提交。

## 4. 上传到已有仓库的 main

```powershell
git push origin HEAD:main
```

这条命令把当前本地分支的提交推送到远程 `main`。如 Git Credential Manager 打开浏览器，使用仓库所有者账号登录。不要把密码或令牌粘贴进聊天或代码。

成功后刷新 GitHub 仓库首页，应看到完整 README 与源码。若出现 `non-fast-forward`，说明远程有新的提交；先获取并合并远程变化，不要添加 `--force`。若提示认证或分支保护错误，保留错误文字，按错误原因处理。

后续修改通常重复“检查 → add → commit → push”。本地分支名可以与远程 `main` 不同。

GitHub 官方：[把本地代码加入 GitHub](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github)。

## 5. 发布完整运行包（以 v0.2.2 为例）

1. 先完成源码推送，再进入仓库的 **Releases**，创建新 Release。
2. 标签填写 `v0.2.2`，目标选择 `main`。如果已有同名标签或版本，请先检查，不要覆盖已有发布；下一次修复应使用新的版本号。
3. 标题填写 `Surface Mesh Rebuild v0.2.2`。
4. 将 [版本说明](RELEASE_v0.2.2.md) 的正文复制到描述中。
5. 上传项目根目录的 **`SurfaceMeshRebuild-v0.2.2-windows-x64.zip`**。
6. 检查标签、说明和附件后点击发布。GitHub 自动生成的 `Source code (zip)` 不含 Python 环境，不能代替这个附件。

完整包大约 56 MB，包含独立 Python 和依赖；放 Releases，避免把二进制安装目录放进代码历史。GitHub 对普通 Git 文件大小有限制，并建议通过 Releases 分发二进制。[官方大文件说明](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)

如果需要重新构建，按 README 的“构建完整运行包”操作。发布包必须保留根目录 MIT 许可证，以及 Python、NumPy、SciPy、Shapely 等组件的原有许可证。

## 发布后检查

- 在 GitHub 页面确认 README 和 LICENSE 可见。
- 从 Releases 重新下载完整 ZIP 到一个新目录，确认 JSX 与 `v0.2.2` 文件夹在同一层级。
- 按 README 在 Illustrator 打开原始 Abaqus EPS，选择对应 ODB，确认能生成新文档。
- 将已知支持范围和限制保留在 README 中。后续有功能修正时，更新版本号、CHANGELOG、脚本文件名与发布说明。

源码测试可以公开复现；实际 ODB/EPS 数据没有随项目公开，真实文件验证仍需使用自己有权使用的数据。
