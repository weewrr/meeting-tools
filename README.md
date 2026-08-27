# 轻会议 LiteMeet

本地优先的多人音视频会议与智能纪要工具。无需账号、无需公网服务器，会议数据完全保存在本机；局域网内的电脑 / 手机通过 HTTPS 即可免跨域接入，使用麦克风、摄像头、屏幕共享与实时字幕。

## 功能特性

- **多人音视频会议**：基于 SFU（LiveKit）架构，单房间最多 50 人，高清编码（摄像头 2.5Mbps/30fps）
- **屏幕共享**：含屏幕声音，多端自适应布局，支持铺满 / 适应切换与全屏
- **会议录制**：两种模式——纯音频录音 / 录屏（视频 + 混合音频），结束后自动保存到本机并上传服务器供回放下载
- **实时转写字幕**：会议中边说边转写，生成完整文字记录（由本地 Whisper 转写服务提供，模型 / 密钥可在服务器管理器「Key 管理」与「转写服务」页配置）
- **AI 智能纪要**：会后一键生成结构化纪要（要点、决定、待办）（需配置 LLM 服务）
- **会议记录管理**：录音 / 录屏 / 转写 / 纪要统一管理，归属设备可见
- **隐私安全**：数据只存本机，纯局域网直连，不依赖公网

## 技术架构

| 层次 | 技术选型 | 说明 |
| --- | --- | --- |
| 前端 | Vue 3 + Vite（多页应用）+ livekit-client（vendored UMD） | 构建产物 `frontend/dist/`，由后端 / 前端服务托管 |
| 后端 | Spring Boot 3.3（Java 21） | REST API + WebSocket 信令 + LiveKit WSS 代理 + 静态文件托管 |
| 媒体 | LiveKit SFU 服务器（`livekit/livekit-server.exe`） | 多人音视频转发，单层编码，低延迟 |
| 存储 | MySQL + Redis + JSON 文件 | 会议生命周期（MySQL）、已结束会议号 TTL（Redis）、记录与配置（JSON） |
| 前端服务器 | `tools/FrontendServer.java`（JDK21 单文件） | 可选的本机独立静态服务（HTTP 3000 / HTTPS 3001） |

```
┌─────────────┐   HTTPS 5679（单端口同源：页面 / API / 信令WS / LiveKit代理）
│  电脑 / 手机  │ ─────────────────────────────────────────────► ┌──────────────────┐
│  (浏览器)    │                                                 │  Spring Boot 后端   │
└─────────────┘                                                 │  · REST API        │
      │                                                         │  · WS 信令 /ws      │
      └────────────── WebRTC 媒体（UDP 50000-60000） ────────►   │  · LiveKit WSS /livekit │
                                                                 │  · 静态页面托管      │
                                                                 └────────┬─────────┘
                                                                          │ 转发媒体
                                                              ┌──────────▼─────────┐
                                                              │  LiveKit SFU 7880    │
                                                              └────────────────────┘
```

## 目录结构

```
meeting-tools/
├── backend/                 Spring Boot 后端（Java 21 / Maven）
│   └── src/main/java/com/litemeet/backend/
│       ├── ai/              转写 / 摘要 AI 客户端
│       ├── api/             REST：会议记录、分享、系统配置
│       ├── config/          静态托管、CORS、HTTPS 证书、WebSocket 注册
│       ├── livekit/         JWT 签发、LiveKit WSS 代理
│       ├── signaling/       WebSocket 会议信令
│       └── store/           MySQL / JSON 存储与缓存
├── frontend/                前端（Vue 3 + Vite，多页应用，源文件）
│   ├── src/
│   │   ├── App.vue          首页（创建 / 加入会议、历史记录、会议码自动补-）
│   │   ├── components/      公共组件（AppTopbar 等）
│   │   ├── pages/           各页面组件
│   │   │   ├── room/        会议房间（Room.vue + roomMedia.js）
│   │   │   ├── records/     会议记录列表
│   │   │   ├── record/      记录详情（播放 / 下载 / 转写 / 纪要）
│   │   │   └── settings/    AI 服务配置
│   │   └── utils/common.js  通用工具（API、格式、身份标识）
│   ├── public/              静态资源（css/js/vendor/livekit-client UMD）
│   ├── index.html / room.html / records.html / record.html / settings.html  各页入口
│   ├── vite.config.js       多页应用构建配置
│   └── dist/                构建产物（手动 npm run build 生成，不入库）
├── livekit/                 LiveKit 服务器（livekit-server.exe + livekit.yaml）
├── transcribe-server/       本地语音转写网关（Python 标准库，端口 8300，驱动 whisper-server 引擎）
├── Release/                 whisper.cpp 本地构建产物（whisper-server.exe 等，不入库，可用环境管理页下载）
├── tools/FrontendServer.java 可选的本机前端静态服务器
├── server-manager/          服务器管理器（Python 图形界面：服务启停 / 环境自动下载 / 会议监听 / 日志）
├── data/                    运行时数据（录音 / 视频 / 证书 / 记录 / 配置）
├── start.bat                启动服务器管理器
└── README.md
```

## 环境要求

- **Windows**（目标部署环境为局域网）
- **Python 3.10+**（仅使用服务器管理器方式需要，含 tkinter）
- **JDK 21+**（后端与前端服务器均依赖；[Adoptium](https://adoptium.net) 下载）
- **Maven 3.6+**（仅首次构建后端需要，管理器一键启动时会自动构建）
- **Node.js 18+ 与 npm**（构建前端 Vue 工程所需；首次运行前执行 `npm install`，管理器会自动安装依赖并以 dev 模式启动）
- **MySQL 8**：`127.0.0.1:3306`，用户 `root` / 密码 `root`（库 `litemeet` 自动创建，见 `application.yml`）
- **Redis**：`127.0.0.1:6379`
- **LiveKit**：需自行下载二进制到 `livekit/`（见下方「LiveKit 二进制」说明）
- **防火墙放行**：TCP `7880`/`7881`、UDP `50000-60000`（媒体）、TCP `5678`/`5679`/`3000`/`3001`（服务）

> 以上除 Windows / Python 外均可通过服务器管理器「环境管理」页自动下载安装，本机已有的环境直接设置路径即可。

## 快速开始

### 方式一：服务器管理器（推荐，无需预装任何环境）

克隆后只需 Python，其余环境（JDK / Maven / Node.js / MySQL / Redis / LiveKit）由管理器自动下载：

1. **启动管理器**：双击根目录 `start.bat`（需要 [Python 3.10+](https://www.python.org/downloads/)，安装时勾选 tkinter）
2. **安装环境**：进入「环境管理」页，未安装的组件点「下载安装」（自动装到 `server-manager\envs`，不污染系统）；
   本机已装过的组件点「设置本地路径」直接指定即可
3. **一键启动**：回到「首页」点「一键启动」——自动按依赖顺序启动 MySQL → Redis → LiveKit → 后端 → 前端 → 转写服务，
   首次启动会自动 Maven 构建后端、npm 安装前端依赖、初始化 MySQL（root 密码自动设为 `root`）
4. 打开 `http://localhost:5173` 即可使用

> **转写服务（实时字幕）**：转写网关（端口 8300）随一键启动自动拉起。首次使用请到「环境管理」页下载
> **Whisper Server** 引擎（或手动把 whisper.cpp 构建产物放入 `Release/`），再到「转写服务」页下载模型（如 `small`）
> 并设为当前。会议房间开启「录音」即可实时转写字幕；访问密钥在「Key 管理」页生成。

> 管理器以前端 **Vite 开发模式**运行（热更新）：改 `frontend/src` 下任意源码，浏览器即时生效。
> API 与信令 WebSocket 由 Vite 代理转发到后端（5678）；局域网设备也可访问 `http://<本机IP>:5173`
> （但 HTTP 非安全上下文，手机浏览器无法调用麦克风/摄像头）。
> 手机需要完整音视频时，按下方「方式二」手动构建部署（HTTPS 5679）。

### 方式二：命令行手动部署（本机已装好环境，供局域网 HTTPS 访问）

1. **安装 LiveKit 二进制**：从 LiveKit 官方 [Releases](https://github.com/livekit/livekit/releases) 下载 Windows 版（`livekit-server.exe`），解压后放到项目 `livekit/` 目录（与 `livekit.yaml` 同级）。该二进制体积较大，未纳入 Git，需自行下载。
2. **配置局域网 IP**：编辑 `livekit/livekit.yaml`，将 `rtc.node_ip` 改为本机 WLAN IPv4 地址
   （`ipconfig` 查看）。WiFi 被重新分配 IP 后需同步更新此值。
3. **构建并启动**（依次执行）：

   ```bat
   cd backend && mvn -DskipTests package && cd ..
   cd frontend && npm install && npm run build && cd ..
   start "" livekit\livekit-server.exe --config livekit\livekit.yaml
   start "" java -jar backend\target\litemeet-backend.jar
   ```

4. **本机访问**：`http://localhost:5679`（后端单端口托管 dist，功能完整）。
5. **局域网设备访问**：手机 / 其他电脑打开 `https://<本机IP>:5679`
   （例如 `https://192.168.31.220:5679`），首次访问需在浏览器中"高级 → 继续访问"信任自签名证书。

## 访问方式说明

| 场景 | 地址 | 说明 |
| --- | --- | --- |
| 本机 | `http://localhost:3000` | 独立前端服务，推荐本机使用 |
| 本机（单端口） | `https://localhost:5679` | 后端托管页面，与 API / 信令 / 媒体同源 |
| 局域网手机 / 电脑 | `https://<本机IP>:5679` | 单端口同源，首次需信任证书，音视频完整可用 |

> 单端口同源（5679）是手机端可用的关键：页面、API、信令 WebSocket、LiveKit 代理全部同源，
> 手机只需信任一个证书即可处于安全上下文，正常调用麦克风 / 摄像头。

## 配置说明

- **`livekit/livekit.yaml`**：媒体端口范围、房间人数上限、API Key/Secret、`node_ip`（重要，见快速开始）
- **`backend/src/main/resources/application.yml`**：后端端口（5678）、HTTPS 端口（5679）、MySQL / Redis 连接、LiveKit 密钥与地址
- **`transcribe-server/config.json`**：本地转写网关的密钥列表 / 限时开放时段 / 当前模型，由服务器管理器「Key 管理」「转写服务」页维护（模型文件在 `transcribe-server/models/`）
- **`data/config.json`**：LLM 服务的 `apiKey` / `baseUrl` / `model`，可在前端"设置"页填写与测试；转写服务的连接信息（指向本地网关 `http://127.0.0.1:8300/v1`）也在此维护

## 常见问题

- **手机无法使用麦克风 / 摄像头**：必须走 HTTPS（`https://<IP>:5679`）且信任证书；同时确认防火墙已放行媒体端口。
- **其他设备连不上 / 画面不显示**：确认 `livekit.yaml` 的 `node_ip` 是本机当前 WLAN IPv4，且局域网同一网段。
- **改代码后现象不变**：浏览器缓存了旧 JS，请强制刷新（`Ctrl+Shift+R` / `Ctrl+F5`）。
- **会议号无法复用**：会议解散后会议号默认保留 1 天（`ended-room-ttl`），到期自动释放。
- **后端重启影响**：后端重启会清空内存中的会议房间与进行中的会议（开发期频繁重启可能导致"变成创建者/被夺舍"的错觉，属正常现象，请重新创建会议）。

## 相关服务端口速查

| 端口 | 服务 |
| --- | --- |
| 5678 | 后端 REST API（HTTP），Swagger：`/swagger-ui.html` |
| 5679 | 后端 HTTPS（单端口同源，局域网设备访问入口） |
| 3000 / 3001 | 独立前端服务（HTTP / HTTPS） |
| 7880 | LiveKit 信令（WebSocket，经后端 `/livekit` 代理） |
| 7881 / 50000-60000 | LiveKit 媒体（TCP 回退 / UDP） |
| 8300 | 本地语音转写网关（Whisper 兼容 API，`/v1/audio/transcriptions`） |
| 8301 | whisper-server 引擎（由转写网关按需拉起） |
