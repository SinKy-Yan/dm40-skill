# DM40B 验证记录 — 2026-11-13

目标：Agent 用纯 Skill（无 MCP、无守护进程）直接操作万用表与示波器，且只读、绝不刷机。

## 环境

| 项 | 值 |
|---|---|
| 主机 | macOS（本机） |
| 表 | 正点原子 DM40B，UID `<meter-uid>` |
| 固件 | opendm40 0.4.1 **中文版**（社区汉化 v0.4.1-zh） |
| 固件 sha256 | `455e0a4a51490c1b6bd22f574ae57d12ca2e99edca146babf79a00d6e8974ed7` |
| 表内 `INFO/FIRMWARE.BIN` | 与上述 sha256 **逐字节一致**（安装成功的硬证据） |
| USB | `1209:d040`，USBTMC（interface 0）+ 大容量存储（interface 1） |
| Python | 3.14 + pyusb 1.3.1（skill 私有 venv） |

## Skill

`~/.agents/skills/dm40/`：`SKILL.md` + `scripts/dm40.py`（USBTMC over libusb，纯命令行）
+ `scripts/setup.sh`（建 venv）+ `scripts/wait_for_meter.py`。

安全白名单（硬编码）：只允许电压/电阻/通断/二极管/电容/频率/温度、示波器读写、
`MMEM:CAT?/DATA?`、只读 `DBG:LCD?`；拒绝 `CURR`、`POW`、`SOUR/OUTP`、`CAL`、
`DBG:UPLOAD/UPDATE/RECOVER`、`MMEM:DEL`、`LOG:*`、`*RST`、任何 `*.bin`/`DM40_` 字样。

## 实测输出

```
$ dm40.py idn
idn: Alientek,DM40B,<meter-uid>,opendm40 0.4.1

$ dm40.py status
idn: Alientek,DM40B,<meter-uid>,opendm40 0.4.1
function: "VOLT"
configure: "VOLT +6.000000E+00,+1.000000E-05"
trigger: AUTO
rate_sps: +1.250000E+07

$ dm40.py read -n 3 --func dcv
7.27939e-05
7.88236e-05
7.86157e-05          # 没接表笔，悬空 ≈ 0

$ dm40.py --json scope --points screen -o wave.csv
{"points": 400, "format": "word", "xinc_s": 5e-06, "sample_rate_sps": 200000.0,
 "vmin": -0.1192647, "vmax": -0.1097859, "vpp": 0.0094787, "vmean": -0.1102599}

$ dm40.py screen -o meter.png
png: meter.png  480x320   (~6.5 s)

$ tests/test_dm40.py
ok: test_guard / test_preamble / test_reading_decode / test_usbtmc_framing / test_wave_decode
```

界面语言已确认：抓屏可读「示波器 / 万用表 / 信号发生器」等中文菜单项
（`dm40_menu.png`；颜色按 RGB565 近似，偏色但文字可读）。

## 本次固件变更（人执行，不在 skill 范围内）

1. 官方 opendm40 v0.4.1 → 中文版 v0.4.1-zh：`DBG:UPLOAD:BEGIN` + 232 字节/包
   + `DBG:UPLOAD:END?` 回 `634240,158882105`（本地 crc32 `09785939` 一致）
   + `DBG:UPDATE`；
2. 安装后表一度不在 USB 上枚举，重新插拔后恢复；`INFO/FIRMWARE.BIN` 与 zh 镜像
   sha256 一致，确认中文版已生效；
3. 备份：`backup/INFO/`（CALIB/BOOTDATA/LOADER/ROMBOOT/OPTBYTES/OTP/UID/FIRMWARE）
   + `backup/DEBUG/SPIFLASH.BIN`（16 MiB 全量，48 s）。

## 回退

| 目标 | 文件 | 方式 |
|---|---|---|
| 官方英文 0.4.1 | `fw/opendm40_v0.4.1.bin` | 官方升级通道（U 盘 `dm40*.bin` / `DBG:UPLOAD`）|
| 官方 V2.6 | `fw/dm40_v_2_6.bin` | 同上，sha256 与仓库记录一致 |
| 救砖 | — | 关机长按电源进 loader，或拆机短接 TX(PA2)–GND；**不要**动 SWD/RDP |
