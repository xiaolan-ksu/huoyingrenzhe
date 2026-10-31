# 替身计时器

火影忍者手游的替身冷却计时器。定时截屏读取对面替身豆的状态，对手用掉替身时自动开始倒计时。

只适配了 MuMu 模拟器，雷电模拟器自行测试。不读内存、不注入进程、不 hook 游戏。双击 `替身计时器.exe` 运行（`config.json` 需放在同目录）。

思路参考了开源项目 [Naruto_Mobile_Game_Tool](https://github.com/K0NGCH4NG/Naruto_Mobile_Game_Tool) 与 [naruto-substitute-timer](https://github.com/fangxiaobaiqaq1/naruto-substitute-timer)（MIT）。代码独立实现，检测点坐标和颜色区间为本机实测值。

## 配置说明

所有配置都在 `config.json`。以 `_` 开头的字段是注释，程序会忽略。

### 基础

| 字段 | 默认 | 说明 |
|---|---|---|
| `player` | 1 | 自己在哪方：1=左侧 2=右侧，监测对面的豆 |
| `ask_player_on_start` | true | 启动时是否弹窗询问在哪方 |
| `countdown_seconds` | 14.5 | 替身冷却秒数 |
| `poll_interval_ms` | 100 | 采样间隔（毫秒） |
| `tolerance` | 4 | RGB 容差（1–5） |
| `dpi_unaware` | true | 强制忽略 Windows 显示缩放 |
| `window.x` / `window.y` | 1660 / 940 | 悬浮面板初始位置 |

### detection（检测）

| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | true | 是否启用自动检测 |
| `map_to_window` | `MuMu安卓设备` | 模拟器窗口标题关键词，用于确定画面区域 |
| `reference_size` | `[1920, 1080]` | 坐标基准尺寸，`points` 坐标按此填写 |
| `left_points` / `right_points` | 实测值 | 检测点坐标，每侧从屏幕外侧往里排列 |
| `require_foreground` | 见配置 | 前台窗口标题需包含其中任一关键词才检测，留空数组则不限 |
| `confirm_frames` | 2 | 连续几帧读到同一数量才采信 |
| `min_trigger_gap` | 3.0 | 两次触发的最小间隔（秒） |
| `prefix_check` | true | 豆的排列校验，日志频繁报「排列矛盾」时关掉 |
| `margin` | 4 | 检测点周围截取的边距 |

### detection.bead_sample（采样参数，1920 基准）

| 字段 | 默认 | 说明 |
|---|---|---|
| `diamond_w` / `diamond_h` | 9 / 13 | 菱形采样区尺寸，豆显示偏大时调大 |
| `min_samples` | 12 | 给出结论至少需要多少个已知色像素 |
| `majority_pct` | 65 | 某一方占比达到多少才给结论，调高更保守 |
| `search_radius` | 4 | 坐标搜索半径，检测点偏差大时调大 |
| `search_step` | 2 | 搜索步长 |
| `search_scale` | 1 | 搜索半径随画面缩放的倍率 |

### detection.colors（颜色区间）

| 键 | 含义 |
|---|---|
| `bright` | 亮青蓝 = 豆可用 |
| `gold` | 赤金 = 豆可用（部分形态豆全变赤金） |
| `dark` | 暗青 = 豆已用掉 |

每项为 `[min, max]` 的 RGB 三元组。区间宁窄勿宽，太宽会把其他 UI 误认成豆。

## 免责声明

- 本工具仅供**学习交流与个人辅助使用**，禁止用于商业用途或任何牟利行为。
- 本工具与《火影忍者》手游及其研发、运营方**无任何关联**，未获得任何官方授权或认可。
- 本工具仅通过截屏读取屏幕像素实现功能，不读取内存、不注入进程、不修改任何游戏数据；但使用任何第三方辅助工具仍可能违反游戏用户协议，由此产生的一切后果（包括但不限于账号警告、功能限制、封禁）**由使用者自行承担**。
- 本软件按「现状」提供，不作任何明示或默示的保证。作者不对因使用或无法使用本软件造成的任何直接或间接损失负责。
- 若本项目的任何内容侵犯了相关权利方的合法权益，请与作者联系，将及时删除或修正。
