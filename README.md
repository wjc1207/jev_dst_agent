# JEV DST Agent 1.0.3——状态感知、有限控制与 JEV 决策

[English README](README.md)

本项目通过一个《饥荒联机版》客户端 Mod，将游戏状态提供给 JEV，并执行 JEV 选择的有限动作。控制器只能执行本文明确列出的动作。

>上一个milestone: [活过第一晚](https://bilibili.com/video/BV1aehE6DEji/)
>
>当前milestone: 制作炼金引擎

## 输出的游戏状态

客户端 Mod 每秒向 `client_log.txt` 写入一条 JSON 记录，内容包括：

- 天数、昼夜阶段和世界时间；
- 生命值、饥饿值和理智值；
- 背包与已装备物品；
- 当前能否制作火把或营火；
- 附近的有用物品、资源、光源和威胁；
- 持久化的前沿导航状态、目标点和当前路段。

每条记录都以 `[JEV_DST_STATE]` 开头，因此配套的状态监视器可以忽略游戏日志中的其他内容。

第 1 天时，通过 JEV API 的 `state` 字段发送的压缩 JSON 还会包含纯英文文字组成的 `knowledge` 数组。它会说明第一晚的黑暗伤害、火把配方（2 个树枝和 2 个草）、材料来源、黄昏的紧迫性、火把耐久以及营火方案。最后一条知识会根据实时状态报告现有材料、尚缺的火把材料、当前能否制作火把，以及第一晚的光源是否已准备好。

这些信息只用于帮助 JEV 判断，不会删除候选项或强制执行某个动作。第 2 天起不再发送 `knowledge` 字段。

## 安装 Mod

将整个 `jev_dst_agent` 文件夹复制到《饥荒联机版》的 `mods` 目录。本机检测到的目标位置是：

```text
C:\Program Files (x86)\Steam\steamapps\common\Don't Starve Together\mods\jev_dst_agent
```

启动游戏，打开“模组”，启用 **JEV DST Agent**，应用更改，然后进入本地或私人世界。

## 查看实时状态

在本文件夹中打开 PowerShell，然后运行：

```powershell
C:\Users\16425\anaconda3\python.exe .\watch_state.py
```

如需查看完整 JSON 记录：

```powershell
C:\Users\16425\anaconda3\python.exe .\watch_state.py --full
```

## 有限控制

`controller.py` 支持移动、采集、制作、劳动、进食和战斗操作：

```powershell
C:\Users\16425\anaconda3\python.exe .\controller.py status
C:\Users\16425\anaconda3\python.exe .\controller.py move up --seconds 0.25
C:\Users\16425\anaconda3\python.exe .\controller.py interact
C:\Users\16425\anaconda3\python.exe .\controller.py collect grass
```

`collect` 会选择观测范围内最近的匹配 prefab，通过短促的 WASD 输入接近目标，每次移动后等待新状态，然后按普通动作键。若程序运行被中断，`stop` 会释放所有移动键。JEV 的安全散落资源候选项包括燧石、木头和石头。

采集时，人物会先接近到目标 1 个世界单位以内，再按空格键。若资源来源失去 `pickable` 标记，或散落物从世界中消失，即判定采集成功；如果交互没有完成，控制器会重试，不会直接进入下一轮 JEV 循环。

草丛、树苗和浆果丛会提供明确的实时 `pickable` 值，资源耗尽时不会作为 JEV 候选项。控制器在按空格键前会等待更新的状态，并再次检查相同的值。如果背包中的草、树枝或浆果数量增加，即使采集后的植物实体仍留在世界中，也能证明采集已经完成。

控制器会把所选采集目标的 GUID 保留到执行阶段，避免更近但已耗尽的植物替换原来的有效目标。如果该 GUID 在执行前已不可用，控制器会改选同一 prefab 中当前最近且仍可采集的植物。

## 离线自动测试

无需启动游戏、调用 JEV 或发送任何键盘输入，即可运行完整回归测试：

```powershell
C:\Users\16425\anaconda3\python.exe .\run_tests.py
```

测试使用模拟状态和模拟游戏输入，覆盖已耗尽与可采集资源的选择、GUID 传递、后备目标选择，以及植物实体仍存在但背包物品增加时的采集完成判断。

采集移动采用按距离缩放的按键时长：远距离时最长为 1 秒，接近交互范围时缩短到 0.18 秒。5 个世界单位内出现敌人会立即中断采集。附近有敌人时，没有武器的角色只会获得逃跑动作；背包中有武器时可以先装备，只有手上已经装备武器时才会出现攻击动作。逃跑在所有情况下都可用，包括夜晚。

一次逃跑不是短按一次方向键，而是一个有上限的连续过程：每次移动 1.2 秒后，控制器读取最新状态，并重新计算远离追击者的方向。连续两次观测不到附近可攻击敌人时停止；最多执行 8 段，作为安全上限。

光源管理根据昼夜阶段工作：白天和黄昏不会提供装备火把动作；如果火把已经装备，卸下火把仍是 JEV 可以选择的普通候选项之一。夜晚只有在 8 个世界单位内没有燃烧中的营火或石篝火时，才会提供装备火把动作。营火只能在夜晚建造；如果身边已有燃烧中的营火或石篝火，则隐藏装备火把动作，但仍提供卸下火把动作，不会强制卸下。除非正在逃离迫近的敌人，否则夜晚没有装备火把时不能离开光源移动。

火把操作是带有执行后验证的语义化 Mod 动作：

```powershell
C:\Users\16425\anaconda3\python.exe .\controller.py craft-torch
C:\Users\16425\anaconda3\python.exe .\controller.py equip-torch
C:\Users\16425\anaconda3\python.exe .\controller.py unequip-torch
```

只有状态数据表明当前可以制作火把，并且背包或手上没有火把时，JEV 候选列表才会包含 `craft_torch`。只有在夜晚、已有火把、火把未装备且附近没有燃烧中的营火或石篝火时，才会出现 `equip_torch`。白天或黄昏装备着火把时，以及夜晚身边有燃烧中的营火或石篝火时，会出现 `unequip_torch`。

其他有限动作：

```powershell
C:\Users\16425\anaconda3\python.exe .\controller.py craft-axe
C:\Users\16425\anaconda3\python.exe .\controller.py craft-pickaxe
C:\Users\16425\anaconda3\python.exe .\controller.py chop
C:\Users\16425\anaconda3\python.exe .\controller.py mine
C:\Users\16425\anaconda3\python.exe .\controller.py eat
C:\Users\16425\anaconda3\python.exe .\controller.py build-campfire
C:\Users\16425\anaconda3\python.exe .\controller.py attack
C:\Users\16425\anaconda3\python.exe .\controller.py equip-weapon
C:\Users\16425\anaconda3\python.exe .\controller.py flee
```

只有状态数据发现有效的敌对或怪物目标，并且武器已经装备时，才会提供攻击动作。攻击使用游戏原生的 `Ctrl+F`，不依赖鼠标坐标或 Mod 的 F10 桥接。营火建造遵循游戏原生的两阶段流程：先缓冲或制作配方，再放置营火。

砍树和采矿会持续到选中的目标被完整采集。只有状态数据证明对应的基础工具有足够耐久完成整项工作时，JEV 才会看到相应动作：普通树木按最多砍 15 下保守计算；常见矿石使用游戏定义的 6、4 或 2 次敲击。进食采用保守的前期安全食物白名单。

## 候选项裁剪原则

候选项裁剪不会替 JEV 选择动作。它只会移除当前不可用、在当前情境中没有意义，或被明确安全约束禁止的动作。JEV 仍会对所有剩余的有意义动作进行排序并选择。

例如，白天或黄昏装备着火把时，会提供 `unequip_torch`，因为收起火把可以节省燃料；没有装备火把时则不会提供 `equip_torch`，因为白天举着点燃的火把没有实际收益。夜晚且没有其他光源时，两者关系反转；站在燃烧中的营火或石篝火旁时，装备火把又会因为重复而被省略。

同样，资源耗尽的植物、不可用的配方、耐久不足的工具，以及被世界地图 API 判定不可通行的前沿路段，都不会作为候选项出现。裁剪层只判断哪些操作当前有意义，不判断哪个有意义的操作最好。

## JEV 决策

将 TypeSafe API 密钥写入 `.env`，格式参见 `.env.example`。默认只进行试运行，不发送游戏输入：

```powershell
C:\Users\16425\anaconda3\python.exe .\jev_agent.py --show-probabilities
```

确认试运行结果后，再添加 `--execute`：

```powershell
C:\Users\16425\anaconda3\python.exe .\jev_agent.py --execute
```

持续自动控制使用：

```powershell
C:\Users\16425\anaconda3\python.exe .\jev_agent.py --loop --execute
```

循环默认没有冷却时间，每个动作完成后会立即重新感知。可使用 `--interval 1` 或其他不超过 60 秒的值增加冷却时间。发生错误时仍会使用至少 1 秒的指数退避，避免形成高速失败循环。

砍树、采矿、采集和建造等长动作会先执行完毕，再开始下一轮，因此动作不会重叠。进入第 2 天不会结束任务：循环会持续运行，仅在角色死亡或按 `Ctrl+C` 中断时停止。瞬时错误会采用有上限的指数退避后重试。

## 前沿探索

探索是单一的语义动作 `explore`，而不是上、下、左、右四个方向选项。Mod 将世界划分为边长 4 个单位的网格，并记住状态扫描半径内已经观测到的格子。一个已知可通行、且至少与一个未知格子相邻的格子称为“前沿”。当前基础评分公式为：

```text
score = information_gain * 10 - distance * 0.5 - visits * 6
```

分数最高且可到达的前沿会成为持久目标。即使中间执行了采集、制作或其他动作，该目标也会保留，直到抵达目标或下一个路径段失效。失效目标会进入 30 秒黑名单，然后系统再选择其他前沿。

每次执行 `explore` 只会朝目标移动下一个 4 单位路段。Mod 每隔 0.75 单位使用 `TheWorld.Map:IsPassableAtPoint` 检查该路段；无效路段不会提供给 JEV。每段结束后控制器都会停止，Mod 输出新状态，然后由 JEV 决定继续探索还是执行其他有意义的动作。

方向键只是根据目标向量推导出的执行细节。可使用 `--move-seconds` 调整走完一个 4 单位路段的时长；默认值为 1 秒，可用范围为 0.2–2.0 秒。

装备火把采用与手动点击背包火把相同的物品格动作，而不是动作系统的自动装备辅助功能。

## 执行阈值与安全规则

安全资源采集使用 `0.25` 置信度阈值。JEV 提示还明确建议白天和黄昏卸下火把，以节省耐久。

默认执行阈值按风险区分：

- 短距离探索：`0.15`；
- 采集已观测到的安全资源：`0.25`；
- 等待：`0.00`；
- 制作火把：`0.45`；
- 建造营火：`0.45`；
- 攻击：`0.45`，且没有装备武器时不可用。

可通过 `--min-confidence` 统一覆盖这些阈值。夜晚没有装备火把时，硬性安全规则会阻止移动或采集；逃离迫近敌人是唯一例外。Git 会忽略 `.env`，发布压缩包也会刻意排除该文件。

> 特别感谢[TerraBlind](https://github.com/Reisenbug/TerraBlind)对本项目的启发