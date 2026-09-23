# AI 日报每日交付与发布

`social-auto-upload` 的“今日待发布”页读取制作端 `发布输出/YYYY-MM-DD/rNNN/package.json`。页面只自动选择北京时间当天最新且校验通过的版本；旧版和昨日视频不会顶替当日内容。正式资源包只读，手动文案保存在本工具的 SQLite 草稿中。

首次将 `docs/daily-settings.example.json` 复制为 `db/daily-settings.json`，填写本机资源根目录、各平台已核实账号 ID 与 CLI 登录别名。视频号可改用本工具账号库 `db/database.db` 中对应的 `user_info.id` 作为 `web_account_id`，其登录文件仍保留在 `cookiesFile/`，无需复制凭据。此文件和 `db/daily-jobs.db` 均不纳入 Git。`access_status` 只有在相应平台登录、权限、声明与实际发布链路都验收后才能设置为 `ready`；视频号仍受访问限制时保持 `blocked_browser_policy`。

```powershell
python -m daily_publish import-legacy
python -m daily_publish today
```

第一次启用前导入制作工作区的 `publications.json`，同日同平台同账号的旧记录会合并，已提交、审核中、结果不明及已发布均阻止再次投稿。账号换绑时仍检查旧账号记录。不会自动补发历史期次。

新服务提供 `GET /daily/today`、`GET /daily/asset/<日期>/<版本>/<资源键>`、`PUT /daily/draft`、`POST /daily/submit`、`GET /daily/task/<任务ID>` 和 `POST /daily/reconcile`。任务在上传前先写入 SQLite，刷新页面可恢复；上传器返回或异常都先标为“结果待核对”，需凭平台内容管理中的真实作品 ID、审核状态及链接核对。只有确认失败且没有远端作品的任务才允许重新提交。视频号只接受页面的手动提交，自动化入口拒绝视频号。

自动任务接入时，在合格资源包准备完毕后执行：

```powershell
python -m daily_publish submit --package "<当日 package.json>" --platforms bilibili,douyin
```

该命令不会将上传器正常退出解释成公开发布。任务需继续回读平台内容管理并写入远端证据。B站时间定位评论、置顶及合集归类仍是视频投稿后的独立步骤。

上传器返回后命令会以非零状态提示“结果待核对”。从平台内容管理取得实际证据后，用 `python -m daily_publish reconcile --task-id <任务ID> --state processing|published|failed|unknown|needs_action --evidence-file <JSON>` 写回；JSON 至少包含 `platform`、`account_id`、带时区的 `checked_at` 和实际核对说明 `note`，审核中与已发布还需要真实 `remote_id`，已发布另需作品 `url`。确认失败还须记录 `remote_absent: true` 和实际核查依据，才能再次投稿；历史失败记录缺少这项证据时按结果待核对处理。

## 切换门槛

本地代码、资源包和页面可先验收；当前自动投稿任务暂时继续原路径。正式让 06:00 任务改用统一服务前，须核对 B站、抖音登录别名和目标账号、B站分区与 AI 声明能力、抖音 AI 声明、视频号可用的获准访问路径，以及三个平台各自一次符合投稿条件的真实回读。视频号当前访问受限，不用其他浏览器、原始 CDP 或私有接口绕过。任一平台未完成时保留旧任务，不把上传器日志、按钮消失或页面跳转写成已发布。
