# Flashing notes (DM40, opendm40)

> 维护记录，不是使用说明。刷机有风险，坏了自负；本项目不提供固件。

# DM40B 刷机记录（2026-11-12 夜）

目的：把应用层换成 opendm40 v0.4.1，以便 Agent 通过纯 Skill（USBTMC SCPI）直接读表和抓波形。
**不碰 bootloader / FS-loader / 校准**——官方升级通道只写 `0x08004000..0x080A0000`。

## 写入前的核对（全部通过）

| 项 | 值 |
|---|---|
| 镜像 | `dm40_opendm40_v0.4.1.bin`，632064 字节 |
| sha256 | `dfca87c78975aed785fafee9389a4f67c9b6d5c8fb01130666e4857d76b889e3`（与 release `SHA256SUMS` 逐字节一致） |
| 头校验 | `dm40fw.py info`：word0 `0xb33c4cc3`，length `0x9a4f4`，end `0x0809e4f4` < `0x080A0000` ✓ |
| 入口 | SP `0x2002f7f8`，Reset `0x08004121` |
| 分块 | 4938 × 128，整块对齐 ✓ |
| 目标盘 | `/Volumes/ATK-DM40`（16 MiB = W25Q128），根目录仅此一个 `dm40*.bin` ✓ |
| 复制后校验 | 目标盘上 sha256 一致 ✓ |
| 表笔 | 未接（用户确认） |
| 供电 | USB 连接中（TP4056 充电 + 供电） |

## 回退包（已就绪，未写入）

- `dm40_v_2_6.bin`，495140 字节
- sha256 `5c2efe345369238604b8001af99ab089f82ff405fa91d5b8368c0086484038ac`（与仓库 `stock/vendor/README.md` 记录一致）
- 结构：3868 × 128 + 36 字节尾块（厂商原始文件，短尾块安全）

回退步骤：开机进 `设置 > 其他设置 > USB 连接` → 把 `dm40_v_2_6.bin` 作为唯一的 `dm40*.bin` 拷进 U 盘 → 弹出 → 关机再开机。

## 救砖预案（按官方文档，未使用）

1. 坏镜像不会变砖：loader 会停在升级界面并挂出 U 盘（`28e9:028f`），把好文件拷进去即可。
2. 若连 loader 都进不去：拆后盖，短接调试口 **TX(PA2)–GND**，上电直到升级界面出现，再放好文件。
3. **不要**接 SWD 调 RDP：Level 1，一解锁就全片擦除（bootloader + 校准 + UID 数据全没）。

## 刷完必须立刻做的事

1. U 盘里 `INFO/` 整个目录拷到电脑（校准 + 启动数据，任何更新包都不含）；
2. `DEBUG/SPIFLASH.BIN` 也拷一份（16 MiB SPI Flash 快照）；
3. 确认 `ioreg -p IOUSB -w0 | grep -i dm40` 出现 `DM40 multimeter (opendm40)`，VID:PID `1209:D040`。

## 时间线

- 22:26 表进入官方 USB 模式（`28e9:028f ATK DM40`，U 盘挂载）
- 22:28 镜像拷入并校验、弹出
- 22:29 Skill 环境就绪（`~/.agents/skills/dm40`）
- 待办：用户关机再开机 → 首启安装（进度条，前 10 秒别断电）

## 2026-11-13 更新

- 07:5x 表重新枚举为 `1209:D040`，运行 opendm40 0.4.1（中文版未确认）。
- 08:1x 用 `DBG:UPLOAD` 通道装中文版 `opendm40-v0.4.1-zh.bin`
  （sha256 `455e0a4a…e8974ed7`，crc32 `09785939`，END? 回 `634240,158882105`）。
- 08:2x 表内 `INFO/FIRMWARE.BIN` 与 zh 镜像 sha256 完全一致 → 中文版生效。
- 08:2x 备份完成：`INFO/`（校准/引导/UID/OTP）+ `DEBUG/SPIFLASH.BIN` 16 MiB。
- 08:2x Skill 真机验证：`*IDN?`、`status`、`READ?`、`WAV:DATA?`（400 点）、`DBG:LCD?` 抓屏全通；
  抓屏可见中文菜单「示波器 / 万用表 / 信号发生器」。
