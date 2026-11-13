---
name: dm40
description: >
  Use when the user asks to measure something with the Alientek DM40
  multimeter (DM40A/B/C), to read its display, to capture a scope waveform
  from it, to see what is on its screen, or to check that the meter is
  connected. Talks SCPI over USBTMC (VID:PID 1209:D040) directly from the
  shell — no MCP server, no background daemon, no vendor app. Read-only by
  policy: voltage, resistance, continuity, diode, capacitance, frequency,
  temperature, scope capture, screen snapshot and file reads only.
---

# DM40 multimeter (opendm40 firmware)

现场操作的万用表。一条命令一次调用，进程退出即结束，没有常驻服务。
当前机器上的表跑 **opendm40 0.4.1 中文版**（社区汉化，`sha256 455e0a4a…`），
SCPI 接口与英文版完全一致。

## 安全底线（硬编码，不可绕过）

脚本在发送前逐条检查 SCPI，命中就拒绝并退出：

- **不允许电流档**（`CURR`、功率计 `POW`）——防止把表留在 A 插孔，下次接表笔烧保险/分流器；
- **不允许任何固件/Flash 操作**（`UPDATE`、`UPLOAD`、`RECOVER`、`FLASH`、`*RST`、`SYST:PRES`）；
- **不允许写表内磁盘**（`MMEM:DEL/COPY`、`LOG:*`）；
- **不允许信号发生器输出**（`SOUR:*`、`OUTP`）；
- **不允许校准**（`CAL*`）；
- `DBG:` 前缀整体拒绝，**唯一例外**是只读的 `DBG:LCD?`（读屏，用于给 Agent 看界面）；
- 单条消息 ≤256 字节（设备限制）。

允许的最坏情况：切换显示功能、改示波器时基/量程/触发、读屏。下一次调用会重新 `CONF`。

> 本 skill 绝不刷固件。升级/回退属于维护操作，由人执行，不属于本 skill。

## 前置

- 表已刷 opendm40（≥0.4.x），USB 直连 Mac；`ioreg -p IOUSB -w0 | grep -i dm40` 应看到
  `DM40 multimeter (opendm40)`；出现 `28e9:028f ATK DM40` 说明表停在官方 loader；
- 首次使用跑一次 `scripts/setup.sh`（建 `.venv` 装 pyusb）。

## 用法

```sh
S=$HOME/.agents/skills/dm40
$S/.venv/bin/python $S/scripts/dm40.py idn
$S/.venv/bin/python $S/scripts/dm40.py status
$S/.venv/bin/python $S/scripts/dm40.py read -n 5 --func dcv
$S/.venv/bin/python $S/scripts/dm40.py measure res
$S/.venv/bin/python $S/scripts/dm40.py scope --points screen -o /tmp/wave.csv
$S/.venv/bin/python $S/scripts/dm40.py screen -o /tmp/meter.png
$S/.venv/bin/python $S/scripts/dm40.py raw 'CONF:VOLT:DC;READ?'
```

`--json` 是全局开关，要写在子命令**前面**：`dm40.py --json read -n 3`。

### 函数别名

`dcv acv acdcv autov res cont diode cap freq temp`（无 `curr`，刻意的）。

### 示波器

`scope` 先 `STOP` 并轮询 `TRIG:STAT?` 到 `STOP`，再取 `WAV:PRE?` + `WAV:DATA?`，
按 preamble 换算成 `time_s, volt` 写 CSV。`--points max` 取整条记录（USB 上限约 512 KiB），
`--points screen` 取屏上 400 点。注意 `xinc` 是屏上抽点间隔（≈2 ms/400 = 5 µs），
不是 ADC 的 50 MS/s。

### 看屏幕

`screen` 用只读的 `DBG:LCD?` 分 6 行一批读回 480×320 帧缓冲，写成 PNG（约 6–7 s）。
颜色按 RGB565 近似还原，可能与实际配色有偏差，但文字/布局可读——用来确认界面语言、
菜单状态、量程、告警提示足够。

## 时间预算（主机超时按这个给）

| 操作 | 典型 | 上限 |
|---|---|---|
| `*IDN?` | <50 ms | 1 s |
| 切功能后首读 | 0.5 s（DCV/DCA） | 4 s；高阻档 8 s |
| `READ?` | 0.1–0.9 s | 5 s（脚本用 15 s） |
| `scope --points screen` | 0.4 s | 5 s |
| `WAV:DATA?` MAX WORD | 1–3 s | 30 s |
| `screen` | 6–7 s | 30 s |

## 排障

| 现象 | 处理 |
|---|---|
| `no USBTMC device 1209:D040` | 表没开机/线是充电线/在 Hub 上；先 `ioreg` 确认枚举 |
| 出现 `28e9:028f ATK DM40` | 表在官方 loader 磁盘模式，不是应用态；关机再开 |
| 超时 | 脚本会发 `INITIATE_CLEAR` 再报错；重试一次，仍失败则重插 |
| `SafetyError` | 命令被白名单拒绝，读 `dm40.py` 顶部 `DENY`/`ALLOW_HEADER` |
| 大块回复错位 | 设备把 >6 KB 的块拆成多条 USB 消息；`read_block` 已按 IEEE 块长度拼装 |
| Finder/桌面出现一堆「DM40」磁盘 | 表的 U 盘每次重连都会重新挂载，设备中途掉线会留幽灵图标；`killall Finder` 即可（实测 50 → 1）。刷机/断电前先在 Finder 里推出，或关掉「外置磁盘显示在桌面/边栏」 |

## 已验证（2026-11-13，DM40B，opendm40 0.4.1 中文版）

- `*IDN?` → `Alientek,DM40B,<meter-uid>,opendm40 0.4.1`
- `status`：功能 VOLT、档位 6 V/10 µV、触发 AUTO
- `read -n 3 --func dcv`：无表笔时 7.3e-05 / 7.9e-05 / 7.9e-05 V（悬空）
- `scope --points screen`：400 点，200 kSa/s 屏上抽点，vpp ≈ 9.5 mV（悬空噪声），0.4 s 完成
- `screen`：480×320 PNG，6.5 s
- 离线测试 `tests/test_dm40.py`：guard / 帧格式 / preamble / 波形解码 / 读数解码 全过
