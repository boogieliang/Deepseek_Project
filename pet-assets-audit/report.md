# 桌宠插件素材审计报告

- 扫描时间：2026/10/24 22:56:15
- 插件目录：`C:\Users\hp\.dsh\profiles\desktop\node_modules\dsh-pet`
- 插件版本：dsh-pet 0.3.8
- 官方清单：`assets/config.jsonc`（106 个动画名）+ GitHub main / npm 0.3.8 对照

## 结论

**无缺失：官方清单 106 个动画，本地 106 个文件全部齐备（100%）。**
本次下载 **0 个文件**——所有文件均已存在，按“跳过已存在、不重复下载”执行。

## 覆盖情况

| 分组 | 官方数量 | 本地已有 |
| --- | --- | --- |
| 核心动画（待机/转向/拖拽/点击/移动） | 11 | 11 |
| 随机动作（小动作/玩耍/吃什么/时节/文字） | 80 | 80 |
| 事件动画（余额/碎碎念/工作状态） | 15 | 15 |
| **合计** | **106** | **106** |

- 本地 webm 文件：106 个，共 51.8 MB
- 官方有、本地无：0 个
- 补齐后仍缺：0 个
- 本地有、官方清单无：0 个

## 其他素材

| 类别 | 官方/配置 | 本地 | 缺失 |
| --- | --- | --- | --- |
| 表情包 memes | 27 | 27 | 无缺失 |
| 通知图 pic | - | 8 | 随包完整 |
| 字体 fonts | - | 1 | 随包完整 |

## 完整性校验

- npm 官方 tarball 对照：dsh-pet-0.3.8.tgz，webm 106 个
- 与 npm 0.3.8 同名文件大小不一致：0 个
- 与 GitHub main 同名文件大小不一致：0 个
- webm EBML 头校验失败：0 个
- GitHub main 对照：GitHub main，官方 webm 106 个

## 下载结果

- 本次下载：0 个
- 跳过（已存在）：106 个
- 补齐后仍缺：0 个
- 说明：无缺失，0 个下载（已存在文件全部跳过）

## 关于「core + extra」

dsh-pet 0.3.8 官方发布的是**单一 webm 素材集**（一个 npm 包、一份 `assets/config.jsonc` 清单），没有把动画拆成 `core/` 与 `extra/` 两个目录。
本报告按官方清单的三个功能组统计：核心 11 + 随机 80 + 事件 15 = 106。
官方另有一个**可选的 macOS 专用 .mov 包**（GitHub Release `assets-mov`，106 个文件、与 webm 一一对应），Windows 播放不使用，本次未下载（避免重复下载同一批动画的另一种容器）。

> 生成脚本：`G:\DeepSeek-Project\test\pet-assets-audit\audit-pet-assets.mjs`
> 逐文件清单：`G:\DeepSeek-Project\test\pet-assets-audit\inventory.csv`