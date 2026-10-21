# 前端 · 论文阅读工作区

Vue 3 / Vite / Pinia / Vue Router / Element Plus。统计作为次级工具使用 ECharts；依赖沿用 `package-lock.json`，不为视觉改造升级整套框架。

## 开发与验证

项目根：

```powershell
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run build
npm.cmd --prefix frontend run dev
```

开发默认5173，预览4173，`/api`代理8001；`VITE_API_PROXY`用于已核验的本机隔离后端，不指向模型服务。代理改变后重启Vite。

```powershell
$env:VITE_API_PROXY = 'http://127.0.0.1:18001'
npm.cmd --prefix frontend run dev -- --port 15173
```

## 布局与数据作用域

- `WorkspaceShell`：紧凑导航，论文问答/知识库/导入任务为主，系统设置次级收纳。
- `ResizableWorkspace`：拖栏与键盘调节、宽度持久化、专注模式、窄屏抽屉与焦点管理。
- `ChatView`：`?kb_id=`选择指定知识库（兼容旧`?kb=`）；切库清空当前会话/引用，列表与过时响应按库隔离。
- `CitationReader`：引用片段、授权原文、Blob释放和切库竞态处理。正文来源编号可点击。
- `KbList/KbDetail`：创建私人库、显式共享、真实上传/解析状态、原文和片段。
- `MarkdownContent`：唯一模型HTML渲染入口；转义原始HTML，拒绝危险链接与远程图片跟踪。PDF只允许签名验证后的Blob嵌入。

设计变量集中在`src/styles.css`：冷灰背景、白面板、墨蓝正文、蓝色操作；默认系统无衬线。回答16px，可调14–20px。表格/代码局部横滚，长文独立滚动，输入区不遮末段。减少动效遵从系统。

## 可重现的隔离浏览器实例

先用绑定解释器创建新的合成夹具（不能使用正式库目录）：

```powershell
$py = Join-Path $env:USERPROFILE 'miniconda3\envs\myenv12\python.exe'
& $py -X utf8 -B .\backend\scripts\paper_workspace_fixture.py --output .\storage\refactor-validation\paper-ui-new
.\启动本机.cmd -Instance paper-ui -ConfigPath .\storage\refactor-validation\paper-ui-new\fixture.yaml -BackendPort 18001 -FrontendPort 15173
```

夹具包含自己的合成测试账号，未启用生成模型；没有显式`--model-dir`时只做关键词检索。不继承APP凭据配置、不读取正式库，已有输出目录拒绝覆盖。账号仅供此夹具，不能用于正式业务库。

根 `scripts/smoke.mjs`使用已安装Tabbit稳定CLI，不下载浏览器：

```powershell
node .\scripts\smoke.mjs --check
$env:SMOKE_BASE_URL = 'http://127.0.0.1:15173'
$env:SMOKE_ISOLATED = '1'
node .\scripts\smoke.mjs
```

脚本先检查隔离应用标记，再用夹具账号验证登录/库页/任务页读取。只允许同源读取和登录，不发送问题、不上传、不探测模型。真正RAG/拖拽/PDF验收需另外执行，不能用smoke或构建替代。

`frontend/tests`的纯函数、实际Vue脚本与SSR测试不等于真实浏览器；当前证据与未测边界见[PROGRESS.md](../PROGRESS.md)。停止隔离实例使用 `停止本机.cmd -Instance paper-ui`，保留数据以复核。
## UI-R2 使用变化（2026-10-01）

- 问答页默认64px紧凑导航，管理页默认216px；“展开/收起”分别记忆。没有重复的外层问答页头。
- 空会话展示“资料 → 当前知识库 → 提问”，只有一个输入框；发送后转入文档式阅读。顶部“阅读设置”内调字号、专注及恢复，低频导出在“更多”。
- 来源默认按需打开，桌面360px；空间不足时临时收起资料栏，保护760px阅读面板，手机采用弹层。自动收栏不覆盖用户偏好；旧v1尺寸在合法范围内迁入v2，保留旧键。
- 知识库支持卡片/列表与访问范围筛选；文档详情使用可局部横滚的语义表格。只读用户不显示上传/维护动作，既有API仍负责鉴权。
- 翻译桌面双列、手机上下排列；原文与完成的译文保留，外发提示继续显示。生成只显示阶段和完整结果，不展示未完成正文。
- 来源采用深青绿提示；蓝灰主色、字号、焦点、控件沿用统一令牌。不会因“模型已配置”就声称实际可用。

验证证据与已知边界见 [UI重构交付与验收](../docs/ui-implementation-20261001/交付与验收.md)。运行时使用原有启动入口；这次没有启动正式后端或发起真实模型调用。隔离Chrome验证包括原生译文复制/下载，不等于真实论文PDF或供应商能力验收。
