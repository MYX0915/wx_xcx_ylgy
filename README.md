# 羊了个羊地图求解与自动点击

读取 Reqable 捕获的地图数据，按牌型和遮挡关系计算消除顺序，并通过 macOS 辅助功能点击微信小游戏窗口。

## 准备

- macOS、微信小游戏和 Reqable。开始运行前，在 Reqable 中开启抓包，并进入第二关初始地图。
- 在“系统设置 → 隐私与安全 → 辅助功能”中为启动脚本的终端或 Codex 授予辅助功能权限。窗口助手提供可选截图命令，使用该命令时还需屏幕录制权限。
- 安装 Python 3.9+ 和 Xcode Command Line Tools（提供 `swiftc`）。

在项目目录建立环境并安装依赖：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r scripts/requirements-click.txt
```

如果终端没有 `python` 命令，可用 `python3 run` 启动。

## 运行

```sh
python run
```

脚本读取最新地图、计算并回放方案，再执行点击。开始点击前会倒数 3 秒；运行时保持游戏窗口位置和鼠标不动。移动鼠标或按 Ctrl-C 会停止。运行日志、截图与进度保存在本机 `runs/`，不会提交到 Git。

只预检不点击：

```sh
scripts/run_game.command
```

限制点击步数或继续已保存的运行：

```sh
python run --max-clicks 4
python run --resume /path/to/runs/your-run-id
```

## 范围

地图 `type` 是牌型和求解的依据。界面图案核验用于检查当前画面是否与地图状态一致。脚本不使用道具，也不自动进入关卡或确认胜利弹窗。
