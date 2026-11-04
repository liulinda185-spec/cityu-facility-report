# 部署到 Hugging Face Spaces（免费、无需绑卡）

## 两个 Space（各自独立网址）

| Space | 内容 | 预计网址 |
|---|---|---|
| `cityu-facility-report` | 主系统 FastAPI（报障/追踪/主管队列/AI 分类） | `https://huggingface.co/spaces/<用户名>/cityu-facility-report` |
| `cityu-llm-demo` | LLM 演示 Flask（DeepSeek 对话 + 工单分类） | `https://huggingface.co/spaces/<用户名>/cityu-llm-demo` |

免费额度：2 vCPU / 16GB / 50GB 磁盘（免费）；**磁盘非持久**——Space 重启或重建后 SQLite 数据（工单记录）会清空，仅适合演示/试点。

## 步骤

### 1. 创建两个 Space（各约 1 分钟）
1. 登录 <https://huggingface.co>（没有就免费注册）。
2. 打开 <https://huggingface.co/new-space>：
   - Space name: `cityu-facility-report`，SDK 选 **Docker**，硬件保持 **CPU basic（免费）**。
   - 同样再建 `cityu-llm-demo`，SDK 选 **Docker**。
3. 建好后进入 Space 页 → **Files** → **Add file → Upload files**：
   - 主系统 Space 上传 `hf_deploy/cityu-facility-report/` 目录里**全部内容**（把 `Dockerfile.main` 改名为 `Dockerfile`、`SPACE_main.md` 改名为 `README.md` 后放入根目录）。
   - LLM 演示 Space 上传 `hf_deploy/cityu-llm-demo/` 目录里**全部内容**（`Dockerfile.llm` → `Dockerfile`、`SPACE_llm.md` → `README.md`）。

> 也可以改用 git 推送（更稳）：在 `hf_deploy/` 下运行脚本
> `.\deploy_hf.ps1 -User <你的HF用户名>`，按提示输入 Access Token
> （Settings → Access Tokens → Create new token，选 **write** 权限）。

### 2. 设置 Secrets（每 Space 的 Settings → Variables and secrets）
- **cityu-facility-report**：新增 `OFFICER_TOKEN` = 一串随机值（至少 20 位，如 `openssl rand -hex 12`）。
- **cityu-llm-demo**：新增 `OPENAI_API_KEY` = 本地 `llm_webapp\.env` 里的值；其余变量已在 Dockerfile 内置。

### 3. 构建与验证
1. 上传文件后 Space 会自动构建（约 5–10 分钟，含模型训练），日志可在 **Logs** 页查看。
2. 构建成功后打开 Space 页面上方的 **App**（或直接访问 `https://<用户名>-cityu-facility-report.hf.space`）验证：
   - 主系统：提交一条报障 → 追踪 → `/officer` 用 `OFFICER_TOKEN` 登录 → 队列/认领/解决。
   - LLM 演示：聊天（需 Key 已填）+ 工单分类。

## 已知限制（免费套餐）
- 两个 Space 均公开：代码任何人可见，因此**密钥一律走 Secrets**，仓库与代码里不留明文。
- 磁盘非持久：重启后数据库重置（代码、模型不受影响）。
- 无自定义域名（免费）；域名固定为 `<用户名>-<space名>.hf.space`。
