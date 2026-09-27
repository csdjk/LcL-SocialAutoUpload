# YouTube 官方授权配置

工作台的 YouTube 账号现在通过系统默认浏览器完成 Google OAuth 授权，通过 YouTube Data API v3 上传。无需扩展，也不再用自动化 Edge 登录 Google。Google 密码、验证码由本人在 Google 页面填写。

## 首次配置

1. 打开 [Google Cloud 控制台](https://console.cloud.google.com/projectcreate)。登录你管理频道的 Google 账号，创建一个项目（名称可用「视频发布工作台」），然后确认顶部选中了该项目。
2. 左侧「API 和服务 → 库」，搜索 **YouTube Data API v3**，打开并点击「启用」。不要只创建 API Key，上传视频需要用户授权。
3. 进入 **Google Auth Platform**，首次使用点击开始配置。填写应用名称、用户支持邮箱和开发者联系邮箱；受众选择 **外部（External）**。邮箱填写你自己的真实邮箱。
4. 在「受众（Audience）」中保留 **测试（Testing）**，把实际授权的 Google 邮箱加入 **测试用户（Test users）**。如果使用品牌频道，也要添加操作该频道的 Google 登录邮箱。
5. 在「数据访问（Data Access）」添加所需范围：`https://www.googleapis.com/auth/youtube.upload` 和 `https://www.googleapis.com/auth/youtube.readonly`。工具只请求上传视频和读取频道/视频所需权限。
6. 进入「客户端（Clients）→ 创建客户端」，应用类型选择 **桌面应用（Desktop app）**，名称任意。创建后下载 **JSON**。桌面应用使用本机动态端口回调，不必配置网站域名或手动填写重定向 URI。
7. 回到工作台「账号管理 → 添加账号 → YouTube」，在 **选择 OAuth 客户端 JSON** 中选择下载的文件。出现「客户端已配置」后，点击 **Google 授权**。
8. 系统默认浏览器打开 Google 授权页。选择 Google 账号及要投稿的 YouTube 频道，允许读取和上传权限。授权回应页面出现后，返回工作台等待频道验证与保存成功。

添加成功后自动使用频道名，无需填写账号名。然后在「今日待发布」的 YouTube 卡片绑定该账号，核对封面、可见性和儿童受众设置。

如果 Google 页面菜单仍显示旧版名称，对应入口是「API 和服务 → OAuth 同意屏幕」及「凭据 → 创建凭据 → OAuth 客户端 ID」。

## 记住登录及常见问题

- 工具保存本机离线授权，并在到期前自动续期。测试模式、主动撤销权限或 Google 安全策略可能要求再次授权，不能保证永久有效。
- 「access_denied」：核对当前邮箱是否加入本项目的测试用户，是否允许了所需权限。
- 「API 未启用／权限不足」：确认启用 API 的项目和 JSON 所属项目相同；同时检查项目配额和频道权限。
- 找不到频道：先在 YouTube 创建频道；有多个频道时，在授权页选择目标频道。重新授权旧账号时不能改成另一个频道；用添加账号新增。
- 网络请求未完成：浏览器与本机 Python 服务都需要能访问 Google API。仅浏览器能打开 Google，不代表后台请求也能连接。配置服务运行环境的 HTTPS 代理后重试。
- JSON 选错：必须是「桌面应用」OAuth 客户端 JSON，不能使用 Web 应用、服务账号或 API Key。配置保存在已被 Git 忽略的 `db/credentials/`，账号授权在 `cookiesFile/`；不要分享或提交这些文件。

## 上传不等于公开

Google 对 2020-07-28 后创建、未经 YouTube API 合规审核的项目限制上传视频为 **私享**。这与 Google OAuth 应用验证是两件事；仅完成授权、把 OAuth 应用改为正式发布，并不会自动解除视频私享限制。参见 [videos.insert 官方说明](https://developers.google.com/youtube/v3/docs/videos/insert)。

工作台上传后保存视频 ID，回读处理状态及实际可见性。只有 API 确认处理完成且为公开，才记为已公开发布；私享、不公开或处理失败会提示本人处理。封面接口失败时保留原视频，提示去 Studio 补设封面，不会重新上传。

上传使用同一断点会话确认已接收字节；无法确认最终结果时保留待核对状态，不会改用网页或新建上传来重试。

## 实现与验证范围

桌面 OAuth 使用随机 loopback 端口、state 校验和 PKCE；日志与界面不包含授权码或 Token。取消、超时、频道不匹配或权限不足时不保存新账号。服务端验证频道名后事务更新账号。

本地测试模拟 Google 响应，真实运行本机授权回调，覆盖 PKCE、错误 state、拒绝授权、频道匹配、重复账号、刷新、分块上传、封面失败和实际可见性回读。真实 Google 授权与投稿需在提供客户端 JSON 后验收，不能把本地通过当作平台投稿成功。

官方资料：[桌面 OAuth](https://developers.google.com/identity/protocols/oauth2/native-app)、[创建客户端](https://developers.google.com/workspace/guides/create-credentials)、[断点上传](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol)、[上传封面](https://developers.google.com/youtube/v3/docs/thumbnails/set)。

### 2026-09-27 本机验收

- 相关后端 141 项及 9 个子测试通过；最后补强频道 ID 的任务绑定摘要后，受影响子集 70 项及 6 个子测试通过。前端登录流、凭据导入及任务展示 28 项通过；生产构建通过。
- UI 使用 Windows / Edge headless / DPR 1 的实际生产构建，页面 `/#/account-management`；视口为 1320×880、320×568、375×667、390×844，窄屏启用移动模式。Google 配置及授权状态使用本机夹具，没有真实授权或投稿。
- 截图共 23 张，覆盖未配置、JSON 格式错误、已配置、授权等待与取消，包含深浅色及窄屏滚动后的按钮区域。人工检查标题、说明、文件选择、遮罩、按钮和换行；修正步骤序号和深色提示对比后重新截图。无横向溢出或页面运行错误。本轮不是参考图还原，不计算相似度。
- 截图及报告位于 `Temp/youtube-oauth-qa/`；代表图为 `setup-light-1320x880.png`、`configured-dark-320x568.png`、`configured-actions-dark-320x568.png`、`waiting-light-375x667.png`。运行脚本为 `Temp/verify_youtube_oauth_ui.py`。
- 确认无排队或上传任务后重启网页、桌面和队列进程；5409、5410 首页及 OAuth 配置接口均返回 200。目前客户端状态为未配置，已有四个其他平台账号保留。真实 Google 授权和上传仍待客户端配置后验证。
