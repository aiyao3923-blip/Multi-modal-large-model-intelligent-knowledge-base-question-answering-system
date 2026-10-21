# 后端 · 论文检索与问答

FastAPI / SQLAlchemy / SQLite，项目绑定已有 Miniconda `myenv12`。读取全局 Python 环境说明后核验解释器，不使用 base 或 PATH 中的裸 Python。

## 启动

推荐在项目根执行 `检查本机.cmd`、`启动本机.cmd`；PowerShell 7 可使用同名 `.ps1`。默认 API 为 <http://127.0.0.1:8001>。

手动运行（在 backend 目录）：

```powershell
$py = Join-Path $env:USERPROFILE 'miniconda3\envs\myenv12\python.exe'
$env:APP_CONFIG = Join-Path (Get-Location) 'config.yaml'
& $py -X utf8 -B -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

相对 storage 路径按 backend 根解析。已有库不要重新初始化；普通启动不迁移、不创建示范库。`app.db.init_db` 仅在确认的新库中显式运行，建立配置管理员、角色与参数，不生成行业账号或资料。

## 关键契约

- **库访问**：默认私人；active owner 读写，显式角色共享只读，admin 不自动读他人私库。普通用户可以建库、上传并管理自己资料。
- **问答**：`POST /api/qa/ask` 的 session 与 kb 必须匹配。返回 `mode`、`generation_status`、`notice`、`retrieval` 和 citations；历史保留生成结果状态。没有生成成功时只是证据，不是模型回答。
- **检索**：BM25/向量均限定 document 与 chunk 的库、当前版本与检索标记；生成前绑定真实片段，生成后再校验权限/哈希。上下文同页补全、不同论文优先，证据总预算默认 24000 字符；已有 SysParam 值继续有效。
- **原文**：`GET /api/kbs/{kb_id}/docs/{doc_id}/file` 校验所有权/共享、库状态、版本和上传根。PDF 检查扩展/类型/文件头；其他类型下载且 nosniff。`download=true` 返回二进制附件；`encoding=base64` 是同权限、有界传输兜底，不是公开链接。
- **模板**：新库忽略旧客户端 template/import_samples 字段，不产生内容；已退休模板 API 返回410。旧数据表只为兼容保留。
- **版本**：已启用的版本库保留草稿/发布/CAS机制，普通owner可维护；共享管理员也不能越过owner权限。未知迁移版本失败关闭。

扫描 PDF 不会自动OCR；图片OCR和Embedding只加载显式已有本地组件。模型网关沿用当前端点，异常不输出密钥或服务端响应正文。

## 验证

项目根：

```powershell
$py = Join-Path $env:USERPROFILE 'miniconda3\envs\myenv12\python.exe'
& $py -X utf8 -B .\backend\scripts\local_diagnostics.py --json
& $py -X utf8 -B .\backend\scripts\run_tests.py
```

`local_diagnostics` 只读检查依赖/路径/数据库结构；测试使用自身隔离数据。`test_paper_qa`、`test_library_isolation`、`test_version_integration`分别覆盖问答证据、库/文件权限、普通owner的版本生命周期。模型成功fixture不是实际在线生成验收。

精确退役工具先执行 `retire_demo_libraries.py --help`，必须显式绝对DB、证据目录和ID。默认仅计划；Apply另需内容绑定计划与确认。它不读取认证/provider表、不删除上传件，不做整库替换。真实删除仍须用户精确批准。

依赖约定见 `requirements.txt`；ONNX可选依赖另有Windows锁文件。本轮没有安装/升级依赖。运行边界见[启动说明](../启动说明.md)，当前验证状态见[检查点](../PROGRESS.md)。