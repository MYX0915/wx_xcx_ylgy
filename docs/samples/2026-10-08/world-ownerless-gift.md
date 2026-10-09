# 无人挑战的奖励地块样本

核实时间：2026-10-08，Asia/Shanghai。

## 抓包结果

用户截图顶部位置为 `(12568,6530)`，右侧显示带倒计时的奖励。Reqable 记录 `62391`，UID `567e52d7-bf52-4624-8fa6-e4b9a732c869`，第 729 帧为成功移动回包，第 731 帧为 Opcode `1120` 的附近礼物推送。帧索引从 0 开始。

该移动回包位置为界面 `(12568,6530)`、协议 `(12568,9070)`。右侧奖励地块的数据如下：

| 字段 | 值 |
| --- | --- |
| 界面坐标 | `(12569,6530)` |
| 协议坐标 | `(12569,9070)` |
| province | 9 |
| discovery | 0 |
| giftType | 4，客户端枚举 Skin2 |
| giftId | 122 |
| giftState | 1，OwnerLess，无人占用 |
| hasOwner | false |
| forbidHelp | false |
| endTime | 1791452782，Unix 秒 |

礼物推送时间为 Unix 毫秒 `1791452193075`。客户端 `setGiftData` 的 `OwnerLess` 分支显示礼物与倒计时，以 `endTime` 减去服务器时间计算剩余秒数。它与 `Occupied=2` 的“正在挑战”状态不同，但两者都不是可直接当作空岛的地块。

## 避让验证

将上述移动回包的 9 格地形与礼物推送合并，奖励地块输出：

```json
{
  "terrainMoveCandidate": true,
  "giftStatusKnown": true,
  "navigationBlock": "gift_ownerless",
  "navigationCandidate": false
}
```

已有动态避让规则覆盖 `giftState=1`，无需为该坐标增加固定黑名单。规划器拒绝直接前往该目标；单元测试覆盖把无人占用奖励从路线候选中排除。30 项周边地图与导航测试通过。本次没有进行实际移动、领取或挑战。

本连接完整历史存在重叠移动请求，正常导航解析器仍拒绝执行。这里仅对指定回包和推送进行独立只读样本验证，没有绕过完整历史检查启动导航。

## 本地证据

脱敏地形与状态合并结果保存在 `runs/world-verification-20261008/ownerless-gift.json`，记录来源帧及验证范围，不含玩家资料或连接凭据。该目录被 Git 忽略。

客户端代码依据为本机版本 500 解包副本 `world-code-bundle/game.js` 的 `setGiftData`，约 2330 行；枚举与消息字段见 `script-bundle/game.js` 的 `proto_wss` 和 `sheep-world-data-manager` 模块。
