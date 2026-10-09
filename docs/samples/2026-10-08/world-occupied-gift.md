# 正在挑战的地块样本

核实时间：2026-10-08，Asia/Shanghai。

## 抓包结果

Reqable 记录 `62391` 中第 22 帧（从 0 开始）是 Opcode `1120` 的附近礼物推送。按此帧截取的历史快照，人物界面坐标为 `(12561,6484)`，其下方 `(12561,6483)` 的字段如下：

| 字段 | 值 |
| --- | --- |
| 协议坐标 | `(12561,9117)` |
| province | 9 |
| discovery | 0 |
| giftType | 2，客户端枚举 Skin1 |
| giftId | 3221 |
| giftState | 2，Occupied |
| hasOwner | true |
| forbidHelp | false |

客户端 `SheepMoveItem.setGiftData` 在 `Occupied` 分支显示其他玩家“在挑战中”的文字。该状态不是地区编号、地图事件编号或消除关卡牌型。`forbidHelp=false` 不代表可作为空岛导航；是否进入帮助另有客户端与服务端条件。

## 导航检查

对上述历史记录执行新的状态合并后：

```json
{
  "terrainMoveCandidate": true,
  "giftStatusKnown": true,
  "navigationBlock": "gift_occupied",
  "navigationCandidate": false
}
```

规划器拒绝直接前往该目标。脱敏快照保存在本机 `runs/world-verification-20261008/occupied-gift.json`，不含玩家昵称、UID、头像 URL 或连接凭据。此次检查为读取与历史回放，没有实际点击挑战地块。

30 项周边地图与导航测试通过，包含全量礼物刷新、增量状态变化、未知状态拒绝、移动后等待新状态、规划绕过挑战地块、点击前状态变化停止及完整模拟导航循环。实时读取另一次最新记录时因重叠移动请求而拒绝解析，没有绕过该检查继续导航。

## 代码依据

本机版本 500 解包副本的 `world-code-bundle/game.js`：`setGiftData` 的 `Occupied` 分支约在 2333 行，附近礼物全量更新约在 3638 行，增量更新约在 3647 行。`script-bundle/game.js` 的 `proto_wss` 定义 `GiftData.GiftState`、`AllGiftNearByNtfInfo` 和 `GiftStateChangeNtfInfo`。
