# 上线部署指南（Render 云平台）

本仓库包含两个服务，通过 `render.yaml`（Render Blueprint）一键部署：

| 服务 | 说明 | 端口 |
|------|------|------|
| `cityu-facility-report` | 主系统：设施报障（报障 / 追踪 / 主管队列 / AI 分类） | Render 自动分配 |
| `cityu-llm-demo` | LLM 演示（对话 DeepSeek + 工单分类本地模型） | Render 自动分配 |

## 部署步骤（约 10 分钟）

### 1. 推送代码到 GitHub（已由助手完成）
代码已推送到本仓库（私有）。仓库只包含运行所需代码，不含：
- `llm_webapp/.env`（API Key 已在 Render 环境变量中配置）
- `facility_report.db`（数据库，首次启动自动创建并灌入基础数据）
- 学校文档 / 设计稿等无关文件
- `ml/model.pkl`（部署构建时由 `python ml/train.py` 重新训练生成）

### 2. 导入 Render Blueprint
1. 打开 https://dashboard.render.com 并注册/登录（免费账号即可）。
2. 首次使用需要连接 GitHub：`New + → Web Service → Connect a GitHub account`，授权本仓库。
3. `New + → Blueprint`，选择本仓库，Render 会读取 `render.yaml` 并创建两个服务。
4. 点击 **Apply**，等待两个服务构建完成（首次构建约 3–5 分钟，含安装依赖 + 训练模型）。

### 3. 设置环境变量（可选）
- **SMTP 邮件（可选）**：在 `cityu-facility-report` 服务的 Environment 中设置
  `SMTP_USER` / `SMTP_PASS` / `FROM_ADDR`（Gmail 应用密码）。不设置时邮件打印到日志（dev 模式）。
- **OFFICER_TOKEN**：主管入口密码已预设于 `render.yaml`（24 位随机串），可在面板中修改。
- **OPENAI_API_KEY**：在 `cityu-llm-demo` 服务 → Environment 中手动填入 DeepSeek Key（值在本地 `llm_webapp/.env` 中），填入后点 Save Changes 自动重启生效。

### 4. 访问
- 主系统：`https://cityu-facility-report.onrender.com/`
  - 报障页 `/`，追踪页 `/track`，主管队列 `/officer`（输入 OFFICER_TOKEN）
- LLM 演示：`https://cityu-llm-demo.onrender.com/`

## 重要说明

1. **免费套餐限制**：免费实例闲置 15 分钟后自动休眠，再次访问约需 30–60 秒冷启动；每月共 750 实例小时。
2. **SQLite 数据不持久**：免费实例使用临时磁盘，每次重新部署后工单数据会重置（启动时自动重建表并灌入分类/区域数据）。要长期保留数据需迁移 PostgreSQL（见 README 升级项）。
3. **模型在构建时训练**：`ml/model.pkl` 不入库，由构建命令 `python ml/train.py` 生成（种子数据 350 条，93% 准确率）。
4. **不要公开本仓库**：`render.yaml` 内含 OFFICER_TOKEN，仓库必须保持私有。

## 本地运行（不变）

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r llm_webapp\requirements.txt
python ml\train.py
uvicorn app.main:app --port 8000          # 主系统
python llm_webapp\app.py                  # LLM 演示（5050）
```
