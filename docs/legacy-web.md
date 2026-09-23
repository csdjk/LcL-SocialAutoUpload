# 历史 Web 版本说明

这套 Web 相关代码主要包括：

- `sau_backend.py`
- `sau_backend/`
- `sau_frontend/`

它们属于项目过去阶段的实现，当前已经不是主线维护方向。

AI 日报新入口 `/` 的“今日待发布”页及 `/daily/*` 接口是增量维护的专用流程，见 [AI 日报每日交付与发布](ai-daily-workflow.md)。原 `/publish-center`、`/postVideo` 与批量接口仍属于本页描述的历史通用 Web 实现。

## 当前定位

- 作为历史版本保留
- 作为过去 API / Web 封装思路的参考
- 不承诺当前一定可直接运行
- 不承诺和当前 `uploader/`、`sau_cli.py` 的最新实现完全同步

## 为什么单独拆出来说明

当前工程正在整体重构，主线已经切到：

- `uploader/`：核心平台实现
- `sau_cli.py`：CLI 主入口
- `skills/`：面向 agent 的 skill

所以 README 不再把 Web 版本当成主入口来介绍，避免让新用户误以为这是当前最稳定的使用方式。

## 如果你仍然想研究这套历史 Web 版本

可以参考这些文件：

- `sau_backend/README.md`
- `sau_frontend/README.md`
- `sau_backend.py`

但请预期：

- 接口契约可能与当前主线不一致
- 平台能力覆盖可能落后于当前 `uploader/`
- 依赖和运行方式可能需要自行排障

## 当前推荐入口

如果你要使用当前主线能力，优先看：

- `uploader/`
- `sau_cli.py`
- `docs/CLI.md`
- `skills/douyin-upload/SKILL.md`

## Windows 本地启动旧 Web 界面

首次启动前，在仓库根目录为独立 Python 环境安装旧 Web 后端依赖，并安装前端依赖：

```powershell
uv pip install --python .\.venv\Scripts\python.exe "Flask[async]==3.1.1" "flask-cors==6.0.0" "xhs==0.2.13" "pillow==11.2.1" "schedule==1.2.2"
Push-Location .\sau_frontend
npm install --no-package-lock
Pop-Location
```

然后双击仓库根目录的 `start-win.bat`。脚本固定使用 `.venv` 的 Python，检查前后端依赖，仅在缺失时创建数据库表和运行目录，并将后端与界面分别启动在本机的 `127.0.0.1:5409`、`127.0.0.1:5173`。界面地址为 http://127.0.0.1:5173/ 。关闭两个新终端窗口即可停止服务。

启动成功只表示本地界面和基础接口可用；这个历史 Web 版的各平台登录、上传和发布流程没有随当前 CLI 保证同步，使用时应按平台实际页面与远端结果核对。


## 视频号账号添加修复（2026-09-23）

旧 Web 的视频号账号登录现已复用 `uploader/tencent_uploader/main.py`，不再只读取第一个 iframe 图片地址后等待主页面跳转。微信 iframe 中的相对二维码地址由后端下载为图片数据，再显示在添加账号弹窗中；无需另开浏览器窗口扫码。

使用时刷新账号管理页面，选择“视频号”、填写名称并确认，用微信扫描弹窗二维码，再在手机上确认。界面会区分获取二维码、等待扫码、手机确认、保存账号和失败重试；二维码更新会替换旧图，取消或关闭页面会终止对应的后台登录任务。登录成功事件会立即关闭连接，避免正常结束被误报为网络错误。

二维码识别不把登录页的备用“加载失败，点击重试”文字当作二维码过期；账号有效性也不再仅凭 URL 变化或页面中没有错误文字判断。验证失败或取消时不会写入新账号，临时凭据与二维码会清理。

相关回归测试（开发环境需要 pytest）：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_legacy_login_stream.py tests/test_tencent_web_login.py tests/test_tencent_verification_dialog.py -q
node --test sau_frontend/tests/loginStream.test.js
```

本次本机实测覆盖真实二维码获取与显示、保持稳定、取消、再次获取，以及取消后的文件清理；最终微信扫码授权和真实账号保存需要账号持有人在手机上确认。其他平台上传/CLI 的独立测试问题不属于这次登录修复范围。


## 发布账号列表与校验状态（2026-09-23）

发布中心现在会独立加载已保存账号，直接打开或刷新发布页也不再依赖先访问账号管理。打开「选择账号」会重新读取列表，可使用「刷新账号」重试；没有该平台账号时显示明确提示。切换平台会清空之前选择的账号，失效账号显示「需重新登录」。

账号管理优先显示上次保存的校验状态，不再把所有账号强制置为「验证中」。点击刷新按钮才重新校验；添加账号已完成校验后直接更新列表。账号列表请求最多等待 10 秒；校验请求设置前后端超时，并保留临时超时/异常账号的上次状态。校验成功可将异常账号恢复为正常，旧校验结果不会覆盖重新登录后的新凭据。

验证命令：`python -m pytest tests/test_account_validation.py tests/test_legacy_login_stream.py tests/test_tencent_web_login.py tests/test_tencent_verification_dialog.py -q`；在 `sau_frontend` 中执行 `node --test tests/*.test.js` 与 `npm run build`。本次页面验证仅进行账号选择、取消、切换平台、刷新与失败恢复，未触发视频发布。
