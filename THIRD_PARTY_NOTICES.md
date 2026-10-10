# 第三方组件

本仓库原创代码采用根目录 [LICENSE](LICENSE) 的 MIT 许可。以下运行时和依赖有各自的许可证，不因随包分发而变成 MIT。

源码通过 `requirements.txt` 声明 NumPy、SciPy、Shapely，不把安装目录提交进 Git。`odbAccess` 来自用户安装的 Abaqus；Abaqus 和 Illustrator 均不随本项目分发。

## Windows 完整运行包

| 组件 | 当前验证包版本 | 包内许可位置 |
| --- | --- | --- |
| CPython | 3.12.14 | `v0.2.1/runtime/LICENSE.txt`，包括随附组件声明 |
| NumPy | 2.5.3 | `v0.2.1/_deps/numpy-2.5.3.dist-info/licenses/` |
| SciPy | 1.18.1 | `v0.2.1/_deps/scipy-1.18.1.dist-info/LICENSE.txt` |
| Shapely | 2.1.2 | `v0.2.1/_deps/shapely-2.1.2.dist-info/licenses/LICENSE.txt` |
| 随 Shapely 分发的 GEOS | 以 wheel 为准 | 同目录 `LICENSE_GEOS`（LGPL 2.1 文本） |
| 随附 Microsoft 运行库 | 以 CPython / wheel 为准 | Python 许可通知及 Shapely 的 `LICENSE_win32` |

NumPy / SciPy 二进制包还包含其他组件声明，请保留安装包中的全部许可文件。构建工具保留相关 `dist-info`、`.libs` 和运行时许可。

升级依赖时，请按实际安装版本更新此清单，并核对对应上游二进制的分发条件；此清单是组件说明，不是对所有许可义务的法律结论。

上游：[Python](https://www.python.org/)、[NumPy](https://numpy.org/)、[SciPy](https://scipy.org/)、[Shapely](https://shapely.readthedocs.io/)、[GEOS](https://libgeos.org/)。
