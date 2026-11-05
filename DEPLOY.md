# 上线部署指南（Render 云平台）

本仓库现在只有 **一个服务**。助手（原 `cityu-llm-demo` Flask 服务）已经并入主应用，
统一挂在 `/assistant`，共用同一个数据库和同一个 `OFFICER_TOKEN`。

| 服务 | 说明 | 入口 |
|------|------|------|
| `cityu-facility-report` | 报障 / 追踪 / 主管队列 / 看板 / AI 助手 / Watchtower | Render 自动分配 |

## 部署步骤（约 10 分钟）

### 1. 推送代码到 GitHub
仓库只应包含运行所需代码，不要提交：
- `.env`（API Key 走 Render 环境变量）
- `facility_report.db`（数据库，首次启动自动创建）
- `ml/model.pkl`（构建时由 `python ml/train.py` 生成）
- `_preview/`、个人文档、设计稿

### 2. 导入 Render Blueprint
1. 打开 https://dashboard.render.com（免费账号即可）。
2. `New + → Blueprint`，选择本仓库，Render 读取 `render.yaml` 创建 **一个** Web 服务 + 一个免费 Postgres。
3. 点击 **Apply**，首次构建约 3–5 分钟（装依赖 + 训练模型）。

### 3. 设置环境变量
`render.yaml` 里标了 `sync: false` 的都需要在面板里手动填：

| 变量 | 必需 | 说明 |
|------|------|------|
| `OFFICER_TOKEN` | 是 | 主管令牌。**没有默认值**，不填则每次启动随机生成并打印到日志。在 `/officer` 页面的输入框**登录一次**即可，之后靠 `fr_officer` HttpOnly Cookie 维持 12 小时，URL 里不再出现令牌。 |
| `OFFICER_NAME` | 否 | 值守人名字，写进 `status_events`（如 `Claimed by Linda`）；不填则记为 `officer`。 |
| `PUBLIC_BASE_URL` | 否 | 邮件里追踪链接的域名，如 `https://cityu-facility-report.onrender.com`。不填则按请求的 Host 推断。 |
| `DATABASE_URL` | 是 | Blueprint 已自动从 `cityu-facility-db` 注入。免费实例磁盘是临时的，**不接 Postgres 每次部署都会丢工单和埋点**。 |
| `SMTP_USER` / `SMTP_PASS` / `FROM_ADDR` | 否 | Gmail 应用密码。不设置时邮件只打印到日志（dev 模式）。 |
| `OPENAI_API_KEY` | 否 | DeepSeek / 任何 OpenAI 兼容 Key。不填则对话与工单助手返回"未接入"，**看板与 Watchtower 仍然工作**（走规则）。 |
| `CHAT_BACKEND` | 否 | 默认 `openai`；想完全关闭模型调用可设为 `mock`。 |
| `OPENAI_BASE_URL` / `OPENAI_MODEL` | 否 | 默认 `https://api.deepseek.com/v1` + `deepseek-chat`。 |

### 4. 访问（同一个域名）
- 报障：`https://cityu-facility-report.onrender.com/`
- 追踪：`/track`　主管队列：`/officer`　AI 助手：`/assistant`
- 运营看板：登录 `/officer` 后点 Dashboard，或带 `?token=<OFFICER_TOKEN>`（脚本用）
- 监控 JSON：`/api/monitor/brief?days=7&token=<OFFICER_TOKEN>`
- 健康检查：`/api/health`

## 公开接口的边界

| 接口 | 行为 |
|------|------|
| `GET /api/reports/{code}` | 只返回状态、分类、描述和时间线；**不含报障人邮箱、照片路径**。按会话 30 次/10 分钟、按来源 60 次/10 分钟限流，超出 **429**。 |
| `POST /api/reports` | 描述 3–2000 字；邮箱需像邮箱；照片 ≤5 MB 且**按字节**确认是 JPEG/PNG/WebP，文件名由服务端生成。邮件在响应之后发送，SMTP 挂了也不会让提交失败。 |
| `GET /uploads/{name}` | 只服务服务端生成的文件名（正则白名单 + 禁止 `..`），带 `nosniff`，不再整目录挂载。 |
| 主管端点 | 接受 Cookie 或 `token`（脚本），`claim`/`resolve` 现在接受空 token，登录后无需再带。 |
| `/officer/login`、`/officer/logout` | 登录发 12 小时 `HttpOnly` Cookie，登出清除；`next` 只允许站内相对路径。 |

## 重要说明

1. **免费套餐限制**：闲置 15 分钟休眠，再次访问约 30–60 秒冷启动；每月 750 实例小时。
2. **数据持久性**：必须设置 `DATABASE_URL`。接上 Postgres 后 `user_events`、`tickets`、`monitor_notes`
   都在数据库里，重新部署不丢。
3. **模型在构建时训练**：`ml/model.pkl` 不入库，由构建命令生成（350 条种子，93% 准确率）。
4. **仓库保持私有**：即使密钥都在面板里，`test_online_pg.py` 里仍有历史明文令牌，务必先轮换再公开。
5. **模型成本**：Watchtower 的解读每 6 小时最多生成一次，24 小时内最多 8 次模型调用；超出后自动退回规则版。

## 本地运行

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
python ml\train.py
$env:OFFICER_TOKEN = "testtoken123"
uvicorn app.main:app --port 8000          # 一个进程就是全部：/ /track /officer /assistant /dashboard
```

`llm_webapp/` 是合并前的 Flask 原版，已不再部署，仅作参考。
