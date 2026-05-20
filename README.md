# Markdown 工作站

一个基于 FastAPI 和 Docker Compose 的在线 Markdown 预览与导出服务。它提供左右分栏编辑体验，支持 Mermaid 图表实时预览，并支持导出 HTML、PDF、Word。

本项目特别处理了 Word 对 Mermaid SVG 图表支持不稳定的问题：导出 DOCX 时会将 Mermaid SVG 转换为适合 Word 嵌入的 PNG 图片，并支持配置图片清晰度。

## 功能特性

- 在线 Markdown 编辑，右侧实时预览。
- 支持 Mermaid 图表预览，包括 `sequenceDiagram`、`flowchart`、`classDiagram`、`erDiagram` 等常用类型。
- 支持导出 HTML、PDF、Word。
- Word 导出支持 Mermaid 图表，并提供 1x、2x、3x、4x 图片清晰度选项。
- 支持保存编辑历史。
- 历史记录支持模糊搜索、标签过滤、单条删除、一键清空。
- 未配置后端存储时，历史记录自动使用浏览器 `localStorage` 兜底。
- 可选 Redis 或 MySQL 作为历史记录同步存储。
- 单容器部署，支持离线镜像迁移。

## 在线体验

启动服务后访问：

```text
http://localhost:23333/
```

页面包含：

- 左侧 Markdown 编辑区。
- 右侧实时预览区。
- 顶部导出按钮：HTML、Word、PDF。
- 顶部历史记录入口。
- Word 图片清晰度选择器。

## 快速启动

### 使用 Docker Compose 构建启动

```bash
cp .env.example .env
docker compose build
docker compose up -d
```

访问：

```text
http://localhost:23333/
```

停止服务：

```bash
docker compose down
```

如果服务器使用旧版独立命令，也可以把上面的 `docker compose` 替换为 `docker-compose`。

### 构建并导出镜像

如果构建环境访问中国大陆以外网络不稳定，建议使用项目内置脚本构建。脚本默认使用清华 Debian 和 PyPI 镜像源，并会导出离线部署用的 `md-workspace-image.tar`：

```bash
./build-image.sh
```

自定义镜像名或导出文件名：

```bash
IMAGE=easy-md-view:local IMAGE_TAR=easy-md-view.tar ./build-image.sh
```

使用自定义镜像名离线部署时，需要让部署脚本使用相同的镜像名和 tar 文件：

```bash
MD_WORKSPACE_IMAGE=easy-md-view:local IMAGE_TAR=easy-md-view.tar ./deploy.sh
```

如果基础镜像无法从 Docker Hub 拉取，可以先在 Docker 中配置可用的 registry mirror，或通过 `BASE_IMAGE` 指定已经可访问或已预先加载的 Python 基础镜像：

```bash
BASE_IMAGE=python:3.11-slim-bullseye ./build-image.sh
```

仅构建不导出 tar：

```bash
EXPORT_TAR=0 ./build-image.sh
```

### 使用 GitHub Container Registry 镜像启动

```bash
docker run -d \
  --name md-workspace \
  --restart always \
  -p 23333:8080 \
  ghcr.io/nyaxs/easy-md-view:latest
```

如需启用 Redis 或 MySQL 历史同步，可以复制 `.env.example` 为 `.env` 后修改配置，再使用 Docker Compose 启动。

### 使用离线镜像部署

如果你已经拿到导出的镜像文件 `md-workspace-image.tar`，将以下文件放到目标服务器同一目录：

- `md-workspace-image.tar`
- `docker-compose.deploy.yml`
- `deploy.sh`

执行：

```bash
chmod +x deploy.sh
./deploy.sh
```

指定访问端口：

```bash
MD_WORKSPACE_PORT=8088 ./deploy.sh
```

也可以直接使用已经发布到 GitHub Container Registry 的镜像：

```bash
MD_WORKSPACE_IMAGE=ghcr.io/nyaxs/easy-md-view:latest ./deploy.sh
```

`deploy.sh` 会自动优先使用 `docker compose`，如果服务器没有 Compose v2 插件，会回退到 `docker-compose`。
离线更新时请确保 `md-workspace-image.tar` 中包含 `docker-compose.deploy.yml` 使用的镜像 tag。脚本会在启动时强制重建容器，避免继续运行旧镜像。

然后访问：

```text
http://<server-ip>:8088/
```

## 历史记录存储

历史记录默认保存在浏览器本地，不需要额外配置。适合单机、个人临时使用。

如果需要跨浏览器或跨设备同步，可以配置 Redis 或 MySQL。

### Redis

```bash
HISTORY_BACKEND=redis
REDIS_HOST=redis.example.com
REDIS_PORT=6379
REDIS_USERNAME=
REDIS_PASSWORD=your_password
REDIS_DB=0
HISTORY_REDIS_PREFIX=md-workspace:history
```

也可以使用连接串：

```bash
REDIS_URL=redis://:your_password@redis.example.com:6379/0
```

Redis 存储说明：

- 程序不会主动给历史记录设置过期时间。
- 如果 Redis 开启了内存淘汰策略，数据仍可能被 Redis 自动淘汰。
- 如果需要长期保存，建议开启 AOF/RDB 持久化，并使用 `noeviction` 策略。

### MySQL

```bash
HISTORY_BACKEND=mysql
MYSQL_HOST=mysql.example.com
MYSQL_PORT=3306
MYSQL_USER=markdown
MYSQL_PASSWORD=your_password
MYSQL_DATABASE=markdown_workspace
HISTORY_MYSQL_TABLE=markdown_history
```

MySQL 表会由服务自动创建。当前字段包括标题、摘要、正文、标签、创建时间和更新时间。

## 历史记录查询策略

历史记录弹窗中的结果按更新时间倒序展示，最近保存或更新的记录排在最前。

- 浏览器本地历史：最多保留 200 条。
- Redis：默认读取最近 200 条；搜索或标签过滤时扫描最近 1000 条，再返回匹配结果，最多 200 条。
- MySQL：在数据库侧过滤搜索条件，再按更新时间倒序返回最多 200 条。
- Redis/MySQL 保存总量不由程序限制，需要通过删除、清空或存储服务策略控制容量。

页面中的历史记录标题旁提供 tips 图标，可以直接查看当前策略说明。

## Word 导出 Mermaid 图表

Word 对 SVG 的兼容性并不稳定，直接把 Mermaid SVG 写入 DOCX 常见问题包括：

- 图表缺失。
- 文字缺失。
- `classDiagram`、`erDiagram` 内容不完整。
- 线条、箭头或标签被裁切。
- 图片分辨率过低。

本项目的 DOCX 导出流程是：

1. 前端将 Markdown 渲染为 HTML。
2. Mermaid 代码块先在浏览器中渲染为 SVG。
3. 导出 Word 时，前端把 SVG 原样提交给后端。
4. 后端将 SVG 中的 `foreignObject` 文本转换为原生 SVG `<text>`。
5. 后端为图表增加白底和安全边距，减少裁切。
6. 后端使用 `rsvg-convert` 按用户选择的清晰度倍率生成 PNG。
7. 最后由 pandoc 生成 DOCX。

这使 Word 导出能够较好支持 Mermaid 图表，尤其是包含复杂文本和关系线的 `classDiagram`、`erDiagram`。

## 配置项

| 变量 | 说明 | 默认值 |
|---|---|---|
| `TZ` | 容器时区 | `Asia/Shanghai` |
| `MD_WORKSPACE_PORT` | 部署脚本和 `docker-compose.deploy.yml` 使用的宿主机端口 | `23333` |
| `HISTORY_BACKEND` | 历史记录后端，可选 `redis`、`mysql`，留空使用浏览器本地 | 空 |
| `REDIS_URL` | Redis 连接串，优先级高于拆分配置 | 空 |
| `REDIS_HOST` | Redis 地址 | 空 |
| `REDIS_PORT` | Redis 端口 | `6379` |
| `REDIS_USERNAME` | Redis 用户名 | 空 |
| `REDIS_PASSWORD` | Redis 密码 | 空 |
| `REDIS_DB` | Redis 数据库编号 | `0` |
| `HISTORY_REDIS_PREFIX` | Redis key 前缀 | `md-workspace:history` |
| `MYSQL_HOST` | MySQL 地址 | 空 |
| `MYSQL_PORT` | MySQL 端口 | `3306` |
| `MYSQL_USER` | MySQL 用户名 | 空 |
| `MYSQL_PASSWORD` | MySQL 密码 | 空 |
| `MYSQL_DATABASE` | MySQL 数据库名 | `markdown_workspace` |
| `HISTORY_MYSQL_TABLE` | MySQL 历史记录表名 | `markdown_history` |

## 项目结构

```text
.
├── main.py                  # FastAPI 服务、前端页面、导出逻辑、历史记录接口
├── Dockerfile               # 容器镜像构建定义
├── build-image.sh           # 构建并导出容器镜像
├── docker-compose.yml       # 本地构建运行配置
├── docker-compose.deploy.yml # 离线迁移部署配置
├── deploy.sh                # 一键加载镜像并启动服务
├── requirements.txt         # Python 依赖
└── DEVELOPMENT_GUIDE.md     # 开发背景、实现过程、坑点和维护说明
```

## 二次开发

### 本地开发流程

```bash
docker compose build
docker compose up -d
docker logs -f md-workspace
```

旧版 Compose 环境可使用：

```bash
docker-compose build
docker-compose up -d
docker logs -f md-workspace
```

修改 `main.py` 后，重新构建或将文件同步到容器并重启：

```bash
docker cp main.py md-workspace:/app/main.py
docker restart md-workspace
```

### 主要依赖

- FastAPI：后端 Web 服务。
- Uvicorn：ASGI 服务运行器。
- marked.js：浏览器端 Markdown 渲染。
- Mermaid：浏览器端图表渲染。
- pandoc：HTML/Markdown 到 DOCX 的转换。
- wkhtmltopdf：PDF 导出。
- librsvg2-bin：`rsvg-convert`，用于 Mermaid SVG 转 PNG。
- Redis / PyMySQL：可选历史记录同步存储。

### 建议的扩展方向

- 用户登录和多用户历史隔离。
- 历史记录最大条数或保留天数配置。
- 文档模板管理。
- 导出主题和样式模板配置。
- 前后端拆分，把当前内嵌页面迁移到独立前端工程。
- 镜像 CI 构建和 Release 自动发布。

## 开源发布注意事项

发布到公开仓库前，请检查：

- 不要提交真实数据库地址、账号和密码。
- 不建议把 `md-workspace-image.tar` 直接提交到源码仓库，可以放到 GitHub Release 附件。
- 建议提供 `.env.example`，把私有配置放到本地 `.env`。
- 建议补充 `LICENSE`。
- 建议补充截图或演示 GIF。

## 镜像发布

仓库包含 GitHub Actions 工作流，会在 `main` / `master` 分支和 `v*.*.*` 标签推送时构建并发布镜像到：

```text
ghcr.io/nyaxs/easy-md-view
```

## 许可证

本项目基于 MIT License 开源。
