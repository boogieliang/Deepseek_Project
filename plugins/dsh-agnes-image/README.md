# dsh-agnes-image

![Agnes 生图插件横幅](../../assets/generated/agnes-plugin-banner.png)

把 **Agnes Image 2.5 Flash** 的生图能力接进 DeepSeek Harness：给当前会话新增一个模型可调用的工具 `agnes_generate_image`，覆盖文生图、图生图和多图合成。生成结果同时落到工作区文件，并在当前模型支持图片输入时直接作为图片块附回对话。

参考文档：<https://www.agnes-ai.com/zh-Hans/docs/agnes-image-25-flash>

## 已接入的状态

- 插件已安装进 `desktop` profile：`C:\Users\hp\.dsh\profiles\desktop\node_modules\dsh-agnes-image`
- `dsh.profile.bundles` 已包含 `dsh-agnes-image`，由插件自带的 `cordis.patch.yml` 插入工具
- 凭证 `AGNES_API_KEY` 已写入 `C:\Users\hp\.dsh\.credentials.yaml` 的 `refs`
- 会话内实测：文生图、图生图（本地图片自动转 Data URI）、文件落盘、图片回显均通过

实测产物：

- `assets/generated/agnes-desktop-scene.png`（会话内直接调用工具，1K / 16:9 / 1312×736）
- `assets/generated/agnes-desktop-watercolor.png`（会话内图生图：同一张改成水彩，保留构图）
- `assets/generated/agnes-plugin-banner.png`（2K / 21:9 / 3127×1340，README 横幅）
- `assets/generated/agnes-20261026-225624-live.png`（live 测试脚本，1K / 1:1）
- `assets/generated/tool-test/agnes-20261026-230023-2c04eb.png`（集成测试，1K / 16:9）
- `assets/generated/tool-test/i2i-apple.png`（图生图：把玻璃方块改成铜块并保留构图）

## 工具能力

| 能力 | 说明 |
| --- | --- |
| 文生图 | 只传 `prompt` + `size` + `ratio` |
| 图生图 | 传 `images`（本地路径 / 公网 HTTPS URL / Data URI） |
| 多图合成 | `images` 传多张，最多 8 张（可在配置里改） |
| 尺寸 | `size` 取 `1K` / `2K` / `3K` / `4K`，再配 `ratio` |
| 输出 | 默认保存为 `assets/generated/agnes-<时间戳>-<随机>.png` |
| 图片回显 | 当前模型声明支持 image 输入时，工具结果附带图片块 |

> 2.5 Flash 与 2.1 Flash 的请求/响应参数、尺寸、价格一致。目前官方对所有输出档位和输入参考图免费，具体以[定价页](https://www.agnes-ai.com/zh-Hans/docs/pricing)为准。

## 用法

直接在对话里说需求即可，模型会调用工具，例如：

- 「画一张 16:9 的深色科技感封面，主题是……」
- 「把 `assets/generated/agnes-desktop-scene.png` 改成赛博朋克夜景，保留构图和相机角度」
- 「把这两张图合成一张活动海报：第一张当主角，第二张当产品参考」

工具参数：

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `prompt` | 是 | 生成或编辑指令。图生图时写清「要改什么」和「要保留什么」 |
| `size` | 否 | `1K` / `2K` / `3K` / `4K`，默认 `2K` |
| `ratio` | 否 | `1:1`、`3:4`、`4:3`、`16:9`、`9:16`、`2:3`、`3:2`、`21:9`，默认 `1:1` |
| `images` | 否 | 参考图数组；本地路径、公网 HTTPS URL 或 Data URI |
| `output_path` | 否 | 输出路径，默认 `assets/generated/agnes-<时间戳>-<随机>.png` |
| `overwrite` | 否 | 目标文件已存在时是否覆盖，默认 `false` |

常用尺寸（完整表见官方文档）：

| ratio | 1K | 2K | 4K |
| --- | --- | --- | --- |
| `1:1` | 1024×1024 | 2048×2048 | 4096×4096 |
| `16:9` | 1312×736 | 2624×1472 | 5248×2944 |
| `9:16` | 736×1312 | 1472×2624 | 2944×5248 |
| `4:3` | 1152×864 | 2304×1728 | 4608×3456 |
| `3:4` | 864×1152 | 1728×2304 | 3456×4608 |

## 安装 / 重装

当前机器已经装好，一般不需要再执行；换机器、换 profile 或修改插件源码后重跑：

```powershell
# 当前机器（桌面版 profile = desktop）
& "C:\Users\hp\AppData\Local\Programs\DeepSeek Harness\resources\runtime\cli\bin\dsh.cmd" `
  plugin --profile desktop add "file:G:\DeepSeek-Project\test\plugins\dsh-agnes-image"
```

或使用自带脚本（`install.cmd` 会绕过 PowerShell 执行策略；会按需写入凭证，不回显 key）：

```powershell
.\install.cmd -ApiKey "sk-..."
```

`file:` 依赖在 profile 的 `node_modules` 下是**拷贝**，不是软链。改完 `index.js` / `lib` 后要让新代码生效：

```powershell
# 先删再装，强制重新拷贝当前源码
.\install.cmd -Reinstall
```

或者手动：

```powershell
$dsh = "C:\Users\hp\AppData\Local\Programs\DeepSeek Harness\resources\runtime\cli\bin\dsh.cmd"
& $dsh plugin --profile desktop remove dsh-agnes-image
& $dsh plugin --profile desktop add "file:G:\DeepSeek-Project\test\plugins\dsh-agnes-image"
```

也可以直接把 `index.js`、`lib\*` 拷进
`%USERPROFILE%\.dsh\profiles\desktop\node_modules\dsh-agnes-image\`，再触发一次 profile 重载。

装完通常由 HMR 自动挂载；若工具没出现，**完全退出并重启 DeepSeek Harness**。刷新浏览器不算重启。

卸载：

```powershell
& $dsh plugin --profile desktop remove dsh-agnes-image
```

再按需删掉 `.credentials.yaml` 里的 `AGNES_API_KEY`。

## 凭证

插件只认一个**凭证引用名**，默认 `AGNES_API_KEY`。解析顺序由 DSH 的 credentials 服务决定：

1. `$DSH_HOME\.credentials.yaml` 的 `refs`
2. 启动进程的环境变量
3. 工作区/用户 `.env`

当前写入形式：

```yaml
refs:
  AGNES_API_KEY: sk-...
```

不要把 key 写进插件源码、`cordis.patch.yml` 或仓库文件。Key 只在发请求时读取，不会出现在工具输出、日志或错误信息里。

## 配置

工具行 id 是 `agnes-image`，可在 profile 补丁层覆盖配置：

```yaml
# C:\Users\hp\.dsh\profiles\desktop\cordis.patch.yml
- id: agnes-image
  config:
    defaultSize: 2K
    defaultRatio: 16:9
    outputDir: assets/ai
```

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `apiKeyEnv` | `AGNES_API_KEY` | 凭证引用名 |
| `baseURL` | `https://apihub.agnes-ai.com/v1` | API 根地址 |
| `model` | `agnes-image-2.5-flash` | 模型名 |
| `defaultSize` | `2K` | 未传 `size` 时的档位 |
| `defaultRatio` | `1:1` | 未传 `ratio` 时的比例 |
| `outputDir` | `assets/generated` | 默认输出目录（相对工作区） |
| `timeoutMs` | `300000` | 单次请求超时；官方建议 60–360 秒 |
| `maxInputImages` | `8` | 参考图数量上限 |
| `attachImage` | `true` | 是否把图片作为附件回传 |
| `allowOutsideWorkspace` | `false` | 是否允许 `output_path` 写出工作区 |

## 请求细节（与官方文档对齐）

- 端点：`POST https://apihub.agnes-ai.com/v1/images/generations`
- 必填：`model`、`prompt`、`size`
- `response_format` 只放在 `extra_body` 下，顶层不传
- 图生图 / 多图合成把输入放在 `extra_body.image`，不传 `tags: ["img2img"]`
- 返回优先读 `data[0].url`，其次 `data[0].b64_json`；URL 会再下载为字节落盘
- 本地参考图会读文件、嗅探格式、转成 `data:image/...;base64,...`
- 没有原生支持的精确尺寸（如 1920×1080）不硬塞；用 `size: "2K"` + `ratio: "16:9"`，再在下游裁剪

## 测试

```powershell
cd G:\DeepSeek-Project\test\plugins\dsh-agnes-image

# 纯单元测试，不联网、不花钱
node --test test/unit.test.js

# 真实 API 的 live 测试：生成 1K 1:1 图并落到 assets/generated
node test/live.mjs

# 入口模块集成测试：stub 掉 harness 包，真实调用文生图 + 图生图
node test/tool.integration.mjs
```

集成测试会覆盖：工具注册、参数校验、1K/16:9 尺寸、本地参考图 → Data URI、文件写入、已存在文件保护、`render` 输出 text + image 两种内容块。

## 目录结构

```text
plugins/dsh-agnes-image/
  index.js                工具注册与执行（唯一 import harness 包的入口）
  lib/agnes-client.js     API 请求、响应解析、下载（纯 Node）
  lib/image.js            格式嗅探、尺寸解析、Data URI、文件名
  cordis.patch.yml        bundle 补丁：插入 agnes-image 行
  package.json            dsh.bundle 声明
  test/unit.test.js       离线单测
  test/live.mjs           真实 API 冒烟
  test/tool.integration.mjs  入口模块集成测试
  install.ps1             安装 / 重装脚本
```

## 故障排查

**工具没出现**

- 确认 `desktop/package.json` 的 `dsh.profile.bundles` 含 `dsh-agnes-image`
- 完全退出并重启 Harness；仅刷新页面不会重新挂载
- 看启动告警：`dsh: warning: 1 entry did not activate` 后面的 `failed to import` 会指出缺哪个依赖

**提示 API key 未配置**

```powershell
Get-Content "$env:USERPROFILE\.dsh\.credentials.yaml" | Select-String AGNES
```

没写进去就用 `install.ps1 -ApiKey ...` 重跑，或手动加到 `refs:` 下。文件被占用时先完全退出 DSH。

**请求超时**

默认 300 秒。复杂提示、4K、服务器排队都可能到几十秒到几分钟；仍未返回就降低档位（2K → 1K）或简化提示。

**4K 图没有回显，但文件在**

附件服务有像素/字节上限，超大图会保存成功但跳过附件。文件路径仍然返回，可直接打开。

**图生图提示输入图不可访问**

公网 URL 必须是无需登录、无需 Cookie 的 HTTPS；本地图用工作区路径最稳，插件会转 Data URI。

## 已知边界

- 当前装的是**本机 profile 依赖**，不是 npm 发布包；换机器要重新 `dsh plugin add`。
- 插件在宿主进程内直接写文件（图片是二进制，`ctx.fs` 只提供文本写入），因此 `allowOutsideWorkspace` 默认关闭，输出默认限制在工作区内。
- 生成图目前一张请求返回一张；多图是「多张参考图合成一张」，不是一次出多张。
- 计费与免费额度以 Agnes 官方为准，本插件不做额度判断。
