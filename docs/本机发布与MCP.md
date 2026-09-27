# 本机视频发布与 Codex MCP

Windows 桌面窗口、托盘、普通视频导入和可选自动排程见[桌面版与自动排程](桌面版与自动排程.md)。本页保留原有日报与 MCP 使用说明。

本工具运行在 Windows 本机。它消费已有 `发布输出/YYYY-MM-DD/rNNN/package.json`，不改变日报生产脚本、资源包字段、视频、双封面、字幕或平台文案的输出格式。

“今日待发布”、Codex MCP、日报 CLI 使用同一个 SQLite 发布账本。页面关闭后，已提交任务由独立后台进程继续执行。电脑关机、休眠或退出 Windows 用户会话后不能保证继续上传；重新启动工具会恢复尚未执行的队列，已开始执行而中断的任务先核对结果。旧“发布中心”的自由素材投稿不属于日报账本流程。

## 启动

在项目根目录安装依赖并构建一次：

```powershell
uv sync --extra web --extra mcp
cd sau_frontend
npm install
npm run build
cd ..
```

运行根目录 `启动本机发布工具.ps1`，或执行：

```powershell
.venv\Scripts\python.exe -X utf8 publisher_app.py --open
```

管理地址 `http://127.0.0.1:5409`，仅本机可访问。已有旧版后端占用此端口时，先确认没有执行中的投稿，再关闭旧后端并启动本工具。`publisher_app.py --port <端口>` 可使用其他本机端口。生产页面使用同源 API，无需单独运行 Vite。

登录仍使用账号管理的官方浏览器窗口。首次登录、授权失效或平台验证时需要人工处理。扫码用于建立并保存登录态，不是每次发布都重新扫码；平台能否同时保留其他设备会话由平台决定，工具不能承诺永不被挤出。抖音视频投稿与账号登录统一使用 Edge；每日任务按账号互斥执行。

## 今日资源与手动投稿

在本机的 `db/daily-settings.json` 设置 `output_root` 和账号，结构参见 `daily-settings.example.json`。当前工作区已有设置可直接沿用。账号凭据不写进资源包或 MCP 配置。

三个平台卡片都可点击“更换账号”，直接选择账号管理中已登录的对应平台账号，无需复制 Cookie。此操作会明确切换以后任务的目标，旧任务和历史投稿仍保留原账号身份。B站仍需配置分区及已经核实的 AI 声明字段；绑定账号本身不等于这些投稿参数已具备。

打开“今日待发布”后，每 3 秒读取北京时间当天的资源目录，并校验清单与文件哈希。新版本没有未保存编辑时自动切换；有编辑时显示新版本提示，手动刷新后切换。缺少当天资源时显示空状态，不拿昨天的视频投稿。异常版本会说明原因，继续使用当天最新的有效版本。

- 单个平台：编辑文案后点击“投稿”，一次完成保存当前文案和入队。
- 多平台：点击“发布全部可用平台”，只提交按钮显示的可用平台，逐个平台返回任务或错误。一个平台失败不会让其他已入队任务消失。
- “保存草稿”仅保存到工具，MCP 会使用该草稿；未保存的页面编辑不会被 MCP 读取。
- “已排队”表示本机任务已接受；“已提交／审核中”表示已获得回执或在后台找到作品，均不等于公开发布。
- “结果待核对”时不会自动重发。视频号可同步后台结果；其余通道是否支持自动核对以工具返回为准。

## API 优先的实际含义

| 平台 | 默认通道 | 条件和限制 |
| --- | --- | --- |
| B站 | biliup 直接调用 B站接口 | 已有登录态、分区和经过核实的 AI 声明字段；不是 B站开放平台应用授权。只有成功的结构化回执才能自动记录稿件 ID；无回执则待核对。 |
| 抖音 | 可用官方 API 优先，否则本机浏览器 | 官方 API 需要 `video.create.bind` 应用权限及账号 OAuth 授权。当前 API 适配器支持单视频、视频帧封面和不超过 286.10 MiB（300000000 字节）的文件。 |
| 视频号 | 官方创作者网页 | 当前未接入通用投稿 API，沿用工具账号、双封面和后台作品回读。 |

抖音官方 API 文档中尚未核实 AI 声明字段，且当前适配器不能完整表达双封面。需要这些能力的现有 AI 日报会明确选择浏览器，并在卡片上显示原因。网络错误、令牌过期或已经尝试提交后，绝不会自动切换通道再次投稿。不能将“接口存在”当成当前账号已获授权。

如需为符合能力范围的抖音视频启用 API，在本机配置中增加：

```json
{
  "accounts": {
    "douyin": {
      "alias": "ai_daily",
      "account_id": "已核实的稳定账号标识",
      "open_id": "对应 OAuth 授权的 open_id"
    }
  },
  "platforms": {
    "douyin": {
      "access_status": "ready",
      "transport": "auto",
      "api": { "credentials_file": "db/credentials/douyin.json" }
    }
  }
}
```

`auto` 自动匹配能力；`douyin_api` 强制官方 API，能力不足直接报错；`browser` 明确使用浏览器。不要用示例覆盖其他平台现有配置。

授权文件格式如下。请在本机填入真实授权值，不发送到聊天，不纳入 Git：

```json
{
  "access_token": "OAuth 授权令牌",
  "open_id": "与绑定账号一致的 open_id",
  "expires_at": "2026-10-01T12:00:00+08:00",
  "scopes": ["video.create.bind", "posting.behavior"]
}
```

`posting.behavior` 仅自动回读需要。当前版本不负责开放平台应用申请、OAuth 回调服务或刷新过期令牌；明确显示需要补充的授权。API 请求超时后保留未知结果，不重试创建视频。

## Codex MCP

采用本机 stdio，不监听网络 MCP 端口，不需要云服务器。MCP 进程按绝对路径启动，因此不依赖 Codex 当前项目目录。

在根目录执行一次注册：

```powershell
$publisherPython = (Resolve-Path '.venv/Scripts/python.exe').Path
$publisherMcp = (Resolve-Path 'publisher_mcp.py').Path
codex mcp add local-video-publisher -- $publisherPython -X utf8 $publisherMcp
codex mcp get local-video-publisher
```

若当前 Codex 任务尚未载入新工具，重新载入 MCP 或在新任务中使用。

| 工具 | 用途 |
| --- | --- |
| `get_daily_package` | 获取当日包、账号、实际通道、草稿、可发布平台和 `selection_id` |
| `publish_daily` | 指定 `selection_id` 和平台列表，实际创建投稿任务 |
| `get_publish_task` | 查询进度、作品 ID、错误和证据 |
| `list_publish_tasks` | 查看最近任务，恢复连接断开后丢失的任务 ID |
| `sync_publish_result` | 只核对结果，绝不重投；暂不支持的通道明确返回原因 |

可对 Codex 说：“读取今天的视频资源，检查账号，发布到 B站、抖音和视频号，再告诉我各平台的任务结果。”Codex 应先查看包，再调用 `publish_daily`，然后查询任务；不能把入队、日志中的成功字样或浏览器跳转当成公开发布。

`selection_id` 绑定本次读取的资源、草稿、目标账号和通道。内容变化时旧选择失效。即使页面与 MCP 同时提交，同一期同平台同账号也只有一个有效预约；其他账号的已有投稿仍会阻止误发。

## 执行与恢复

`attempts` 为统一账本；`delivery_queue` 与预约在同一事务中创建；`run_claims` 记录唯一执行权。后台进程用 Windows 文件锁保证单执行器；账号锁还覆盖旧 CLI 同步入口。

启动失败的任务仍保持 `queued`，重启工具恢复，无需重新投稿。执行器崩溃后，未领取执行权的任务可继续；领取过执行权的任务变为 `unknown`，不会自动再次上传。跨日尚未开始的任务在调用上传器前停止，不自动补发过期日报。配置或素材入队后变化也在上传前停止。

本机数据位置：`db/daily-jobs.db`、`db/daily-settings.json`、`db/credentials/`、现有登录目录。后台日志为 `db/daily-jobs.worker.log`；启动脚本日志为 `db/publisher-app*.log`。后台暂不提供重启系统后自动启动；登录 Windows 后运行启动脚本即可。

本轮没有修改既有 06:00 Codex 自动化，没有创建第二个定时发布器。日报生产与定时策略可以继续使用原流程，发布动作逐步接入 MCP；历史 `publications.json` 会导入账本以阻止重复投稿。

## 验证边界

隔离数据库、模拟 API、真实本机浏览器测试页验证任务互斥、恢复、超时和最终按钮只点击一次。真实 stdio 客户端验证 MCP 初始化、工具列表与调用。构建页面验证“今日待发布”的实际渲染和交互。

这些检查不代表三个平台的实号投稿已验收，也不代表获得了抖音开放平台应用权限。本轮不为验收再次发送今天已经发布的视频。正式使用时以各平台实际回执、内容管理和公开作品为最终证据。

参考：[Codex MCP](https://developers.openai.com/codex/mcp/)、[官方 Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk)、[抖音上传视频](https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/video-management/douyin/create-video/upload-video)、[抖音创建视频](https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/video-management/douyin/create-video/video-create)、[抖音作品查询](https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/video-management/posting-task/video-basic-info)。
