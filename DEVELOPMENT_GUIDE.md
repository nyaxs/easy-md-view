# Markdown 工作站开发指南

## 背景

本项目用于通过 Docker Compose 部署一个在线 Markdown 预览与导出服务。初始版本可以预览 Markdown，但存在 Mermaid 图表不可见、PDF 导出失败、Word 导出图表缺失或失真等问题。后续需求扩展为支持历史记录、本地兜底存储，以及可选 Redis/MySQL 跨设备同步。

当前版本已经形成一个轻量级 Markdown 工作站：前端提供编辑、实时预览、历史记录管理和导出操作，后端负责 HTML/PDF/DOCX 文件生成，并针对 Word 导出中的 Mermaid 图表做了专门适配。

## 目标

- 在线编辑 Markdown，右侧实时预览。
- 支持 Mermaid 图表实时显示。
- 支持导出 HTML、PDF、Word。
- Word 导出时支持 Mermaid 图表，并可配置图片清晰度。
- 支持保存编辑历史，支持模糊搜索和标签过滤。
- 支持删除单条历史记录和一键清空历史记录。
- 历史记录默认保存在浏览器本地；配置 Redis 或 MySQL 后可进行网络同步。
- 历史记录弹窗提供策略说明 tips，解释倒序展示、返回条数和存储策略。
- 使用页面内 toast 和确认弹窗替代浏览器原生提示框，统一样式和位置。
- 支持镜像迁移部署，便于在其他服务器离线部署。

## 当前文件

- `main.py`：FastAPI 服务、前端页面、导出接口、历史记录接口。
- `Dockerfile`：运行镜像构建定义。
- `requirements.txt`：Python 依赖。
- `docker-compose.yml`：开发/构建部署配置。
- `docker-compose.deploy.yml`：迁移服务器使用的部署配置，不需要源码构建。
- `deploy.sh`：一键加载镜像并启动服务。
- `md-workspace-image.tar`：通过 `docker save` 导出的镜像文件。

## 功能设计

### 前端页面

页面由 `main.py` 内嵌 HTML 提供，主要区域包括：

- 顶部标题栏：历史记录入口、Word 图片清晰度选择、HTML/Word/PDF 导出按钮。
- 左侧编辑区：Markdown 文本输入、标签输入、保存按钮。
- 右侧预览区：实时显示 Markdown 和 Mermaid 渲染结果。
- 历史记录弹窗：搜索、标签过滤、策略 tips、卡片选择、删除、清空、确认覆盖。
- 全局反馈：顶部 toast 提示和居中确认弹窗。

### Markdown 预览

前端使用 `marked.js` 将 Markdown 渲染为 HTML。输入框内容变化后，右侧预览区按 300ms 防抖刷新。

### Mermaid 预览

Markdown 中的 ` ```mermaid ` 代码块会先被转换为 `<div class="mermaid">`，再由 Mermaid 渲染为 SVG。预览区直接显示浏览器渲染后的 SVG。

### HTML 导出

前端将 Markdown 转为完整 HTML 文档发送到后端，后端直接返回 HTML 文件。

### PDF 导出

后端使用 `wkhtmltopdf` 将 HTML 转为 PDF。HTML/PDF 路径直接保留 Mermaid SVG，因此显示效果接近浏览器预览。

### Word 导出

Word 对 SVG 支持不稳定，因此最终方案是：

1. 前端生成包含 Mermaid SVG 的 HTML。
2. 后端导出 DOCX 时识别 SVG。
3. 将 SVG 中的 `foreignObject` 文本转换为原生 SVG `<text>`。
4. 为 SVG 添加白底和安全边距，避免文字、线条、marker 被裁切。
5. 使用 `rsvg-convert` 按用户选择的清晰度倍率生成 PNG。
6. 将 PNG 嵌入 HTML，再由 pandoc 转为 DOCX。

页面提供 Word 图片清晰度：

- 标准 1x
- 清晰 2x
- 高清 3x
- 超清 4x

显示尺寸保持一致，倍率只影响嵌入图片像素密度。

## 历史记录

### 本地模式

如果没有配置 Redis/MySQL，前端会使用浏览器 `localStorage` 保存历史记录。

保存内容包括：

- id
- title
- summary
- content
- tags
- createdAt
- updatedAt

### 远端同步模式

后端自动检测环境变量：

Redis：

```bash
HISTORY_BACKEND=redis
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_PASSWORD=your_password
REDIS_DB=0
```

也可以使用：

```bash
REDIS_URL=redis://:password@host:6379/0
```

MySQL：

```bash
HISTORY_BACKEND=mysql
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your_password
MYSQL_DATABASE=markdown_workspace
HISTORY_MYSQL_TABLE=markdown_history
```

未配置或远端不可用时，页面自动回落到浏览器本地历史。

### 搜索与标签

历史弹窗支持：

- 模糊搜索标题、摘要、正文、标签。
- 标签过滤，多个标签用逗号、中文逗号或空格分隔。
- 选择历史卡片后，点击“确认覆盖”替换当前编辑区内容。
- 删除单条历史记录。
- 一键清空历史记录。

展示策略：

- 历史记录按更新时间倒序展示，最近保存或更新的记录排在最前。
- Redis 默认读取最近 200 条；搜索或标签过滤时扫描最近 1000 条，再返回匹配结果，最多 200 条。
- MySQL 在数据库侧按搜索条件过滤，再按更新时间倒序返回最多 200 条。
- Redis/MySQL 保存记录本身不限制总量，需要通过删除、清空或外部存储策略控制容量。

远端同步性能优化：

- Redis 保存使用 pipeline，列表查询使用 `zrevrange` + `mget` 批量读取，删除同步清理 item key 和 sorted set index。
- MySQL 查询将模糊搜索和标签过滤下推到 SQL，避免每次取回 200 条后再在 Python 中过滤。
- MySQL 表结构创建结果在进程内缓存，避免每次保存、查询、删除都重复执行建表逻辑。

## 坑点记录

### Python 字符串导致前端 JS 正则断裂

早期版本中 JS 正则含有换行，经过 Python 三引号字符串展开后变成非法 JS，导致整段脚本中断，预览区空白。解决方式是避免在内联脚本中写会被 Python 展开的正则换行，并使用 raw string 返回 HTML。

### Mermaid 在 Word 中不显示

Pandoc/Word 对内联 SVG 支持不稳定，直接把 Mermaid SVG 放进 DOCX 会出现图表缺失。

### SVG 转 PNG 丢文字

`rsvg-convert` 对 Mermaid SVG 中的 `foreignObject` 支持不完整，会出现有框线但无文字。解决方式是将 `foreignObject` 转成 SVG 原生 `<text>` 后再转换。

### classDiagram/erDiagram 在 Word 中不完整

前端 Canvas 转 PNG 对 Mermaid 的 `foreignObject` 兼容不稳定，`sequenceDiagram` 这类以原生 SVG 文本为主的图通常正常，但 `classDiagram`、`erDiagram` 容易出现文字缺失、线条丢失或内容裁切。当前方案是 Word 导出统一把原始 SVG 发送到后端，由后端转换为 PNG。

### 浏览器原生 alert/confirm 样式不可控

原生提示框位置和样式由浏览器控制，不利于作为工作站工具统一体验。当前方案是实现页面内 toast 和确认弹窗：toast 固定在页面顶部居中，确认弹窗固定在页面上方居中区域，删除、清空、覆盖等危险操作统一通过自定义确认框处理。

### wkhtmltoimage 裁切线条和文字

`wkhtmltoimage` 能显示部分 HTML 文本，但对 Mermaid SVG 的路径、marker 和边界处理不稳定，出现缺线和裁切。最终保留为非主路径，DOCX 主路径使用 `rsvg-convert`。

### 100% 尺寸误判

Mermaid SVG 常见 `width="100%"`。如果直接解析数字，会误认为宽度是 100px，导致 Word 中图片分辨率极低。解决方式是百分比尺寸回退到 `viewBox`。

### 内部节点属性覆盖 SVG 尺寸

如果用正则扫描整个 SVG，内部 `<rect width="...">` 会覆盖 `<svg>` 的尺寸。解决方式是只解析 `<svg ...>` 开始标签。

### 裁切边界

Mermaid 图表元素可能贴近 viewBox 边缘，转换 PNG 时文字或线条会被裁掉。解决方式是给 DOCX 专用 SVG 增加白底和安全边距。

## 亮点

- 单容器部署，迁移简单。
- 默认无依赖历史模式，浏览器本地即可使用。
- 可选 Redis/MySQL 后端，适合多端同步。
- Word 导出支持 Mermaid 图表，并支持清晰度配置。
- HTML/PDF/DOCX 采用不同导出策略，分别适配目标格式能力。
- 历史记录支持标签、模糊搜索、单条删除和一键清空，适合复用常用文档模板。
- 历史记录策略说明直接放在弹窗内，用户可以了解当前查询范围和排序方式。

## 二次开发建议

### 代码结构

当前项目刻意保持单文件应用形态，方便部署和迁移：

- 后端 API、导出逻辑和历史记录后端适配集中在 `main.py`。
- 前端 HTML/CSS/JavaScript 也内嵌在 `main.py` 的首页返回值中。
- 系统依赖集中在 `Dockerfile`，Python 依赖集中在 `requirements.txt`。

如果后续功能继续增多，建议逐步拆分：

- `app.py` / `api.py`：FastAPI 路由。
- `history.py`：Redis/MySQL/local 历史记录适配。
- `exporters.py`：HTML/PDF/DOCX 导出逻辑。
- `templates/` 或前端工程：页面模板与静态资源。

### Word Mermaid 导出维护点

Word 导出是本项目最需要谨慎维护的部分。修改时重点验证：

- `sequenceDiagram`
- `flowchart`
- `classDiagram`
- `erDiagram`
- 包含中文文本的图表
- 边线、marker、标签是否被裁切
- 不同 Word 图片清晰度选项下的显示尺寸和清晰度

### 历史记录后端维护点

Redis 适合轻量同步和快速访问，默认不设置过期时间。作为长期存储时需要 Redis 开启持久化，并避免使用会淘汰 key 的内存策略。

MySQL 更适合长期保存。当前查询限制为最多返回 200 条，保存总量不限制。生产环境可以继续扩展：

- 按用户或空间隔离历史记录。
- 增加保留天数或最大条数配置。
- 增加全文索引或专门的搜索字段。
- 将标签独立成关联表，提升复杂标签查询性能。

## 开源发布前检查

- 移除 `docker-compose.yml` 中的真实 Redis/MySQL 地址、账号和密码，改成空值或 `.env.example`。
- 不建议把 `md-workspace-image.tar` 作为源码仓库默认内容提交；可以放到 Release 附件。
- 确认 `README.md` 中的镜像名、端口和环境变量与发布方式一致。
- 补充许可证文件，例如 `LICENSE`。
- 可选：增加截图、演示 GIF、CI 构建和镜像发布流程。

## 本地开发

```bash
docker compose build
docker compose up -d
```

访问：

```text
http://localhost:23333/
```

## 迁移部署

将以下文件拷贝到目标服务器同一目录：

- `md-workspace-image.tar`
- `docker-compose.deploy.yml`
- `deploy.sh`

执行：

```bash
chmod +x deploy.sh
./deploy.sh
```

指定端口：

```bash
MD_WORKSPACE_PORT=8088 ./deploy.sh
```

启用 Redis：

```bash
HISTORY_BACKEND=redis \
REDIS_HOST=redis.example.com \
REDIS_PASSWORD=your_password \
./deploy.sh
```

启用 MySQL：

```bash
HISTORY_BACKEND=mysql \
MYSQL_HOST=mysql.example.com \
MYSQL_USER=markdown \
MYSQL_PASSWORD=your_password \
MYSQL_DATABASE=markdown_workspace \
./deploy.sh
```
