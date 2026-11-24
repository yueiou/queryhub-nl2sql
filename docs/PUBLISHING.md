# 公开仓库发布说明

公开内容为源码、固定评测用例、合成数据生成脚本、脱敏评测汇总和合成库截图。

## 排除内容

- `backend/.env` 及其他实际环境配置：包含模型端点与密钥。
- `backend/data/`：包含连接串、Schema目录、查询日志、会话以及本地SQLite文件。
- `backend/eval/results/`：包含完整模型响应、执行错误、查询SQL与原始结果。
- CSV、日志、Python缓存、依赖目录、前端构建产物及本地预览目录。

`.gitignore` 防止将上述文件加入新提交。请注意，忽略规则不能移除已有Git历史中的秘密；本项目从新仓库首个提交开始只纳入检查后的文件。

## 本地检查

```bash
git add README.md .gitignore .gitattributes backend frontend docs scripts .github
python scripts/check_public_release.py
git diff --cached --stat
```

检查器直接读取Git暂存区，核对禁止路径、常见密钥格式、私钥、个人绝对路径和本地 `.env` 中的实际密钥值；只报告位置，不打印凭据。CI使用 `--head` 检查提交。自动扫描无法替代人工审阅，特别是截图和业务数据内容。

## 评测资料

`docs/evaluation-summary.json` 使用字段白名单导出，仅保留评分、分组结果和非敏感评测配置。未公开模型端点、连接串、完整问答响应或本地路径。README保留首轮成绩及已知口径冲突，不把它当作最终验收结论。

题集结构清单 `backend/eval/databases.json` 只包含合成库标识、展示名称、业务域和生成规模；不包含平台连接ID或机器路径。构建题集不依赖本地数据库manifest。

## 截图

README截图来自独立的合成演示库，禁用模型配置，通过规则引擎完成实际查询，不展示原有会话。截图用于说明产品交互；复杂SQL能力应通过模型评测验证。

## 仓库与服务

公开源码不等于公开运行中的服务。当前应用面向本地演示；接入真实数据前应补充认证、授权、凭据存储和数据库查询资源控制。
