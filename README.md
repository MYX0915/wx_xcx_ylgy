# 羊了个羊地图解析、求解与自动点击

这是一个 macOS 本地工具，用于从 Reqable 捕获的微信小游戏响应中还原《羊了个羊》每日挑战地图，按地图 `type` 和遮挡关系计算无道具消除顺序，并通过辅助功能在微信窗口发送鼠标点击。

项目在本机运行。Reqable 负责记录游戏请求；脚本通过 Reqable 自带的本地 MCP 服务读取记录，不需要把抓包文件复制到项目。求解只依赖地图中的数字 `type`，牌名只用于日志显示。

## 版本与验证状态

- 项目版本：开发版，尚未发布语义版本号或正式 release tag。
- 游戏服务路由版本：`/sheep/v1/...`；具体字段按抓到的响应校验，客户端/服务端不一定遵循独立的语义版本号。
- Reqable MCP 初始化协议：`2024-11-05`。
- Python：3.9 或更新版本；原生窗口助手使用当前 macOS SDK 编译。
- 牌型名称：type 2 至 15 按本项目名称表显示；type 1 按用户确认显示为“草”。
- 自动点击：点击位置由地图坐标和固定窗口布局计算。程序不会从屏幕识别牌面或读取真实槽位，也不会确认游戏的胜利弹窗。
- 协议事实与样本统计于 2026-10-08 核对。游戏服务端和 Reqable 版本更新后，接口行为可能变化。

## 功能流程

```mermaid
flowchart LR
    A[微信进入第二关] --> B[Reqable 捕获 map_info_ex]
    B --> C[本地 Reqable MCP 读取响应]
    C --> D[解压 match_data]
    D --> E[读取静态 .map 并按坐标合并]
    E --> F[校验 type、位置和数量]
    F --> G[求解并独立回放]
    G --> H[将地图坐标换算为窗口坐标]
    H --> I[通过 macOS 辅助功能发送点击]
```

完整运行命令是 `python run`。脚本执行前需要 Reqable 已开始抓包，微信小游戏已停在第二关初始画面，槽位为空。

## 安装环境

### 系统要求

- macOS 和微信桌面版中的目标小游戏窗口。
- Reqable for macOS，且安装包包含 `Contents/Helpers/mcp-server`。
- Python 3.9 或更新版本。
- Xcode Command Line Tools，提供 `swiftc` 编译原生窗口助手。
- 为启动脚本的终端应用或 Codex 授予 macOS 辅助功能权限。

### 安装步骤

在项目根目录执行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r scripts/requirements-click.txt
```

Reqable 本地 MCP 服务的默认路径写在 `scripts/map_capture.py`：

```text
/Applications/Reqable.app/Contents/Helpers/mcp-server
```

当前唯一的 Python 运行依赖为 `lzstring==1.0.4`。地图求解、protobuf wire-format 解析、坐标处理和 Reqable MCP 客户端使用 Python 标准库。窗口点击助手由 `scripts/game_window.swift` 编译；`python run` 会在二进制不存在或源码更新时自动编译。

如果当前终端没有 `python` 命令，可使用 `python3 run`。也可以在 zsh 中配置 `alias python=python3`。

### macOS 权限

在“系统设置 → 隐私与安全 → 辅助功能”中允许启动进程控制电脑：

- 从终端启动时，允许 Terminal、iTerm 等实际启动脚本的终端。
- 从 Codex 启动时，允许 Codex。

`scripts/game_window capture` 是可选的窗口截图命令，单独使用时需要屏幕录制权限；主流程不调用截图命令。

## 快速开始

1. 打开 Reqable，确认正在捕获流量。
2. 在微信打开《羊了个羊：星球》，进入第二关初始地图，不要先点击牌。
3. 在项目根目录执行：

   ```sh
   python run
   ```

4. 程序读取当前捕获的地图、计算解法并输出预检信息，然后倒数 3 秒开始点击。

运行中不要手动操作游戏或移动鼠标。移动鼠标超过 3 个屏幕逻辑单位、按 Ctrl-C 或进程遇到错误时，程序会停止。已发生点击的局面应从对应 `runs/` 记录继续，不能重新运行一局初始地图方案。

## 命令行参数

根目录 `run` 默认开启实际点击。它自动追加 `--execute`，底层脚本为 `scripts/play_game.py`。

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `--help` | | 显示参数帮助，不执行棋盘点击 |
| `--execute` | 根目录入口自动添加 | `scripts/play_game.py` 的实际点击开关；直接调用底层脚本时不指定则只预检 |
| `--resume PATH` | 无 | 从本机已有运行目录读取地图、解法和进度继续 |
| `--window-id ID` | 自动选择唯一窗口 | 同时检测到多个游戏窗口时，指定目标窗口 ID |
| `--max-clicks N` | `500` | 本次最多发送多少次点击；小于剩余牌数时运行后暂停 |
| `--seconds N` | `120` | 求解器运行时间上限 |
| `--delay SEC` | `0.5` | 点击后的固定等待时间，最小 `0.3` 秒 |

示例：

```sh
# 完整流程：捕获、求解、点击
python run

# 只预检，不点击
scripts/run_game.command

# 最多点击 4 张牌
python run --max-clicks 4

# 接续一次已保存且状态明确的运行
python run --resume runs/your-run-id

# 调整点击间隔
python run --delay 0.4
```

直接调用底层脚本时，默认只预检：

```sh
.venv/bin/python scripts/play_game.py
.venv/bin/python scripts/play_game.py --execute --max-clicks 4
```

## 服务接口

本项目涉及两类接口：游戏服务端的只读 HTTP 响应，以及 Reqable 暴露给本机进程的 MCP 工具。自动点击通过 macOS 鼠标事件发送，不使用游戏动作 HTTP 接口。

### 游戏地图接口

| 用途 | 当前地址 | 使用方式 |
| --- | --- | --- |
| 获取地图初始化响应 | `https://cat-match.easygame2021.com/sheep/v1/game/map_info_ex` | 从 Reqable 历史记录中读取匹配主机和路径的响应 |
| 获取静态地图文件 | `https://cat-match-static.easygame2021.com/maps/{map_md5}.map` | 优先从 Reqable 记录读取；未捕获时用 Python `urllib` 发起 GET |

当前解析器按主机和 URL 路径筛选 `map_info_ex`，再从响应内容解出数据；解析器不保存完整请求 URL、请求头或会话 token。请求方法由客户端当前实现决定，代码没有用 HTTP 方法作筛选条件。

初始化响应按 JSON 读取，使用字段如下：

| JSON 字段 | 含义与校验 |
| --- | --- |
| `err_code` | 必须为 `0`，否则视为游戏接口失败 |
| `data.map_md5` | 当前每日挑战预期含两项；下标 `1` 指第二关静态地图标识 |
| `data.match_data` | LZ-string UTF-16 压缩的 JSON 字符串；为空时立即报错，不回退到旧地图 |

响应的结构示意如下。值用占位符表示，不应把 Reqable 中的完整 URL、请求头或账号凭据复制到公开文档：

```json
{
  "err_code": 0,
  "data": {
    "map_md5": ["<level-1-map-md5>", "<level-2-map-md5>"],
    "match_data": "<lz-string-utf16-compressed-json>"
  }
}
```

解压后的 `match_data` 当前要求恰好包含两个关卡状态。程序选择下标 `1`，并要求：

- `crushMapInfo.gameType == 3`，即当前实现支持的每日挑战类型。
- `fullSync == true`。
- `gameState.crushedBlockCount`、`crushAreaBlocks`、`moveOutAreaBlocks` 显示棋盘尚未操作。
- `gameState.allBlockRuntimeData` 中每张牌都能和静态地图节点匹配。

解压过程使用 LZ-string UTF-16 模式：每个压缩字符先取 `ord(character) - 32` 作为压缩单元，再由 `lzstring` 解码并交给 JSON parser。解析器只使用响应中的结构化数据，不解析或保存完整请求参数。

第二关状态的关键结构示意：

```json
{
  "crushMapInfo": {"gameType": 3},
  "fullSync": true,
  "gameState": {
    "crushedBlockCount": 0,
    "crushAreaBlocks": [],
    "moveOutAreaBlocks": [],
    "allBlockRuntimeData": [
      {
        "type": 11,
        "layerNum": 3,
        "colNum": 4,
        "rowNum": 52,
        "blockId": 123,
        "moldType": 1,
        "metaType": 0,
        "metaData": 0,
        "AreaType": 0
      }
    ]
  }
}
```

上述数字仅作字段格式示意，不代表固定牌或固定坐标。

运行时牌记录的关键字段：

| 字段 | 含义 |
| --- | --- |
| `type` | 牌型数字 ID；求解和三消匹配的依据 |
| `layerNum` | 所在层数 |
| `colNum` | 横向坐标，对应静态地图节点的 `rolNum` |
| `rowNum` | 纵向坐标，对应静态地图节点的 `rowNum` |
| `blockId` | 当前响应中的运行时牌编号，写入合并地图的 `cardId` |
| `moldType` | 牌堆或节点结构属性；与静态地图逐节点核对 |
| `metaType`、`metaData` | 特殊牌属性；当前求解器拒绝非零 `metaType` |
| `AreaType` | 牌所在区域；当前求解器只接受棋盘区域 `0` |

静态 `.map` 文件包含 `.map` 文件头及 protobuf 内容。当前解析器检查文件头、跳过前 21 字节，再解析剩余的 protobuf wire format。常用地图字段如下：

| 地图字段 | 用途 |
| --- | --- |
| `widthNum`、`heightNum` | 地图网格尺寸 |
| `levelKey` | 地图/关卡标识 |
| `blockTypeData` | 类型 ID 到“三张一组”数量的映射 |
| `levelData` | 分层节点；包含 `id`、`rolNum`、`rowNum`、`layerNum`、`moldType` |
| `layers` | 排序后的层号列表 |

当前解析器使用的静态 protobuf 字段编号：

| protobuf 消息 | 字段号 | 解析为 |
| --- | ---: | --- |
| `GameMap` | 1 | `widthNum` |
| `GameMap` | 2 | `heightNum` |
| `GameMap` | 3 | `levelKey` |
| `GameMap` | 4 | 重复的类型计数字典项；字典项子字段 1 为 type，子字段 2 为组数 |
| `GameMap` | 5 | 重复的层数据；层消息子字段 1 为层号、子字段 2 为节点列表 |
| 节点消息 | 1 | 节点 ID |
| 节点消息 | 2 | `type`；静态文件缺省时解析成 `0` |
| 节点消息 | 3 | `rolNum` |
| 节点消息 | 4 | `rowNum` |
| 节点消息 | 5 | `layerNum` |
| 节点消息 | 6 | `moldType` |

protobuf 字段语义应以当前本机代码和匹配的客户端协议为准；未知字段保留为 wire-format 字段，不据此猜测业务含义。

静态地图通常没有具体 `type`，解析器会先用 `0` 表示未赋型节点；运行时响应提供的牌型和 `blockId` 再按 `layerNum-colNum-rowNum` 组成的节点 ID 合并。合并后会核对静态与运行时节点集合、每张牌的 `moldType`，以及每类牌数量是否等于 `blockTypeData` 组数乘以 3。

### Reqable MCP 本地接口

`scripts/map_capture.py` 启动 Reqable 自带的 MCP server 子进程，并通过 stdin/stdout 使用行分隔 JSON-RPC。初始化协议版本为 `2024-11-05`，随后调用：

| MCP 工具 | 输入 | 用途 |
| --- | --- | --- |
| `capture_live_filter` | `filters` | 按主机 `cat-match.easygame2021.com` 和关键字 `/map_info_ex` 筛选捕获记录；静态地图查找按完整 URL 筛选 |
| `capture_live_get_by_id` | `id` | 读取匹配记录的响应状态、编码和响应体 |

调用参数形状示意：

```json
{
  "name": "capture_live_filter",
  "arguments": {
    "filters": [
      {"type": "host", "hosts": ["cat-match.easygame2021.com"]},
      {"type": "keyword", "pattern": "/map_info_ex"}
    ]
  }
}
```

筛选返回记录 ID 后，客户端逐个调用 `capture_live_get_by_id` 并检查 URL 的 path 必须精确等于 `/sheep/v1/game/map_info_ex`。静态地图查找筛选完整的 `https://cat-match-static.easygame2021.com/maps/{map_md5}.map` URL。

响应体支持 Reqable 提供的 `base64` 和 `utf8` 两种编码。MCP 调用单次等待上限为 15 秒；失败、空响应、HTTP 非 200、无法识别的编码和空 `match_data` 都会终止本次流程。

Reqable MCP 与上述游戏 HTTP 接口不是同一个接口：前者是本机程序化读取抓包历史的工具服务，后者是微信小游戏访问的游戏服务端。

### 不会调用的接口

当前自动流程不会向 `/logic_server/v1` 等动作接口提交游戏操作，不会请求 seed 解密接口，也不会调用游戏内道具接口。点击是通过 `scripts/game_window.swift` 对指定微信窗口发送鼠标事件完成的。静态地图在 Reqable 中未捕获时，程序只对公开地图文件 URL 发起 GET。

## 地图数据和牌型

牌面 ID 使用服务端的数字 `type`，不是从 PNG 或截图推测出来的。类型名称仅用于显示：

| `type` | 显示名称 | `type` | 显示名称 | `type` | 显示名称 |
| ---: | --- | ---: | --- | ---: | --- |
| 1 | 草 | 6 | 白菜 | 11 | 水桶 |
| 2 | 胡萝卜 | 7 | 羊毛 | 12 | 手套 |
| 3 | 玉米 | 8 | 刷子 | 13 | 铃铛 |
| 4 | 树桩 | 9 | 剪刀 | 14 | 篝火 |
| 5 | 叉子（耙子） | 10 | 奶瓶 | 15 | 粉红线团 |

type 1 在代码中由用户确认显示为“草”；其他牌型名称由 `scripts/map_capture.py` 的 `NAMES` 映射。改名不会改变求解结果。

截至 2026-10-08 核对的本地每日挑战样本有 243 张牌、23 层、15 种 type。各 type 数量满足 3 的倍数，总计 81 组三消。这是当日样本快照，不是对后续地图固定数量的保证。

## 求解规则和算法

求解器位于 `scripts/solve_map.py`。输入地图必须有 `levelData`，且每张牌都包含唯一 ID、正整数 `type`、整数坐标与层数。程序拒绝重复 ID/位置、非棋盘区域、非零特殊牌类型、每类总数不能被 3 整除的地图，以及超过 500 张牌的输入。

### 遮挡判断

- 每张牌占据 `8 × 8` 个地图逻辑单位。
- 牌被任意更高层、且横纵坐标差都严格小于 8 的剩余牌遮挡。
- 横向或纵向距离恰好为 8 时不算遮挡。
- 遮挡会检查所有更高层，不只相邻层。

### 槽位和消除

- 槽位最多容纳 7 张牌。
- 每次选择一张当前可点击的牌，将其 `type` 加入槽位。
- 同 `type` 牌会在槽位逻辑中归组；同一 `type` 达到 3 张时立即消除。
- 第 7 张牌只有在这一步触发三消、消除后槽位少于 7 张时才合法。
- 成功条件是棋盘和槽位同时清空。

求解器使用带启发式排序、失败状态缓存和受限重启的深度优先搜索。搜索只负责找候选顺序；`replay()` 再以显式剩余牌集合和槽位列表独立检查每步遮挡、重复点击、槽位容量、三消和终局。没有通过回放的顺序不会进入实际点击。

## 点击坐标与执行模型

当前窗口布局基准为 350 × 665 macOS 逻辑像素。地图中的横向 `rolNum` / `colNum` 和纵向 `rowNum` 经固定布局比例转换，再加上微信游戏窗口左上角坐标：

```text
scale_x = window_width / 350
scale_y = window_height / 665
screen_x = window_origin_x + (50 + 4.5 × colNum) × scale_x
screen_y = window_origin_y + (146 + 4.5 × rowNum) × scale_y
```

窗口横纵缩放比例相差超过 0.02 时，程序停止。窗口需保持前台，目标位置不能被其他窗口遮挡。原生助手会检查窗口 ID、进程、边界、前台状态和辅助功能权限，再发送鼠标移动、按下和松开事件；按下持续 70 毫秒。

程序每步在日志中打印“已点击牌名”和按解法预先推算的“当前槽位”，例如：

```text
已点击【草】；当前槽位【草、胡萝卜】（26/243）
```

该槽位是根据地图 `type` 和求解步骤计算的预期值，不是游戏画面的实时读取值。程序也不会确认点击是否真的被游戏接收，`nextStep` 表示已发送并记入记录的点击次数，不等同于服务端确认。

## 本地运行文件

每次运行在 `runs/<record-id>-<timestamp>/` 创建独立目录，通常包含：

| 文件 | 内容 |
| --- | --- |
| `map.json` | 本次合并后的完整棋盘和来源标识 |
| `solution.json` | 求解状态、点击顺序、逐步槽位变化和回放结果 |
| `coordinate-model.json` | 本次窗口坐标缩放与偏移 |
| `preflight.json` | 地图来源、窗口、步数和执行模式 |
| `progress.json` | 下一步序号、点击是否处理中、运行状态 |

`progress.json` 的 `inFlight=true` 表示在点击事件前已经写入“点击处理中”，但尚未写入该步完成状态。若程序恰在此阶段退出，不能盲目恢复；先人工核对当前游戏画面和运行状态，再决定处理方式。`sequence_completed` 只表示计划中的点击都已发送，不代表游戏确认通关；`winScreenConfirmed` 当前始终为 `false`。

运行 JSON 的主要字段：

| 文件 | 字段 | 含义 |
| --- | --- | --- |
| `map.json` | `levelData` | 每层牌节点列表；节点包含位置、`type`、`cardId` 和结构属性 |
| `map.json` | `_source` | Reqable 的 `recordId`、`recordUid`、捕获时间和静态地图 MD5；不包含 URL query/header |
| `solution.json` | `status` | `solved` 表示搜索到解，且该解通过独立回放 |
| `solution.json` | `operations` | 按顺序排列的节点 ID |
| `solution.json` | `steps[]` | 每步的节点 ID、`cardId`、type、位置、前后槽位及是否消除 |
| `solution.json` | `verified`、`triples` | 回放验证结果和计划中的三消次数 |
| `preflight.json` | `coordinateModel` | 当前窗口使用的坐标缩放与偏移 |
| `preflight.json` | `clickingEnabled` | 本次是否允许发送鼠标事件 |
| `progress.json` | `nextStep` | 已写入进度的点击事件数量 |
| `progress.json` | `inFlight` | 是否停在一次尚未写入完成状态的点击中 |
| `progress.json` | `status` | `prepared`、`running`、`paused`、`stopped` 或 `sequence_completed` |

运行数据可能含 Reqable 记录 ID、UID、地图和点击进度，因此 `.gitignore` 排除了 `captures/`、`decoded/`、`runs/` 和 `.venv/`。不要把带请求头的原始抓包、完整 URL 查询参数、会话凭据、微信用户加密密钥或 IV 提交到公开仓库。

## 错误与停止行为

| 情况 | 处理方式 |
| --- | --- |
| Reqable 没有匹配的 `map_info_ex` 记录 | 提示先启动抓包并重新进入挑战；不使用旧记录代替 |
| `match_data` 为空、格式错误或解压失败 | 终止当前运行 |
| 地图数量、坐标、结构或类型计数不一致 | 终止当前运行，不点击 |
| 地图运行时状态显示已有点击/消除 | 拒绝作为新局求解输入 |
| 找不到唯一的游戏窗口 | 停止；可用 `--window-id` 选择窗口 |
| 游戏窗口位置、尺寸、焦点或覆盖状态不满足要求 | 点击前停止 |
| 用户移动鼠标超过阈值 | 停止，保留进度文件 |
| Reqable MCP 单次请求超过 15 秒 | 报超时并停止 |

要恢复只能指定对应的本机运行目录：

```sh
python run --resume runs/your-run-id
```

仅在同一局仍处于脚本预期状态、且没有额外手动点击时恢复。不要用旧运行目录去操作新地图。

## 深入文档

- [文档索引](docs/README.md)：按使用指南、研究记录、历史分析和地图样本分类浏览。
- [地图求解与点击](docs/guides/click-pipeline.md)：点击前置条件、进度文件和坐标模型。
- [求解器规则](docs/guides/solver.md)：输入契约、遮挡规则、搜索与验证。
- [地图字段分析](docs/research/map-field-analysis.md)：逐牌类型与静态地图的来源及核验要求。
- [2026-10-08 样本记录](docs/samples/2026-10-08/82136-new-game-verification.md)：固定地图快照，仅用于参考。

## 项目结构

```text
.
├── README.md
├── run                         # 单命令完整流程入口
├── docs/
│   ├── README.md               # 文档索引
│   ├── guides/                 # 当前点击与求解说明
│   ├── research/               # 字段分析及历史研究
│   └── samples/                # 固定日期地图样本，不可用于新局
├── experiments/legacy/         # 不属于当前主流程的实验脚本
├── scripts/
│   ├── play_game.py            # 流程编排、坐标和运行记录
│   ├── map_capture.py          # Reqable MCP、响应解码、地图合并
│   ├── analyze_captures.py     # 静态 .map 与 protobuf wire-format 解析
│   ├── solve_map.py            # 搜索、独立回放和求解器 CLI
│   ├── game_window.swift       # macOS 窗口检查和鼠标事件
│   ├── run_game.command        # 预检/底层入口
│   └── requirements-click.txt  # Python 运行依赖
├── tests/
│   └── test_solve_map.py       # 求解器边界测试
├── captures/                   # 本机 Reqable 输入，Git 忽略
├── decoded/                    # 本机解码数据，Git 忽略
└── runs/                       # 本机每次运行输出，Git 忽略
```

## 开发与验证

运行默认测试套件：

```sh
python3 -m unittest discover -s tests -v
```

独立处理本机 `captures/` 下的 Reqable 响应与 `.map` 文件：

```sh
.venv/bin/python scripts/analyze_captures.py
```

分析器只递归解析 protobuf wire format 并生成本机 `decoded/capture_report.json`；原始响应本身应留在本机，不应直接推送到仓库。

## 项目边界

- 支持输入：初始化状态、完整每日挑战第二关地图、普通固定 type 牌、空槽位、不使用道具。
- 不支持：局中变化地图、特殊动态牌、非棋盘区域牌、洗牌/撤回/移出等道具的状态建模。
- 不处理微信登录、进入小游戏、点击关卡入口、广告或胜利弹窗。
- 不调用游戏动作接口，不保证客户端/服务端会接受每个鼠标事件。
- 游戏接口可能调整。出现空响应、格式变化或类型校验失败时，应先检查当前 Reqable 记录与接口结构，不应移除校验后继续点击。
