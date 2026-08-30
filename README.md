# 我的硬件工具箱 —— 官方源自动下载器

自动从**各软件官方网站 / 官方 GitHub Release** 下载硬件检测、烤机、磁盘、内存等维护工具，
下载后自动解压成**绿色便携版**，按分类组织 —— 一个全官方源、永远最新版的个人工具箱。

## 特性

- 🚀 **一键全量下载**：`uv run tubagreen` 下载 + 自动提取
- 📦 **自动绿色化**：用 7-Zip ZS 解压 zip/SFX/NSIS/Inno 安装器，产出免安装目录
- 🔄 **智能解析**：GitHub（HTML 解析，无 API 限流）、TechPowerUp 三步下载、官网直链
- 📡 **实时报告**：每个文件下载/提取完成即时显示，结束生成 `tools/下载报告.txt`
- ♻️ **断点续传 + 自动重试**：网络中断自动续传，错误重试 3 次
- ✅ **去重跳过**：已提取的目录自动跳过，不重复下载

## 用法

```bash
cd C:\Users\yuan\Desktop\tubagreen

uv run tubagreen              # 全量下载 + 提取
uv run tubagreen --list       # 查看软件清单
uv run tubagreen -c 硬盘工具   # 只下某分类
uv run tubagreen -o CPU-Z,Ventoy   # 只下指定工具
uv run tubagreen --check-updates    # 检查各工具最新版本（不下载）
uv run tubagreen --parallel 3 # 并发数
uv run tubagreen --keep-archives  # 保留原始压缩包
```

## 软件清单

`software-list.yaml` 是核心数据源。每个条目支持：

| 字段 | 说明 |
|---|---|
| `url` | 官网稳定直链；支持 `{ver}` 占位（配合 `version_pattern` 自动取最新） |
| `version_url` / `version_pattern` | 官网版本页 + 提取最新版号的正则（默认版本页 = homepage） |
| `github` / `github_latest` | GitHub 自动解析最新 Release |
| `tpu` | TechPowerUp 三步下载（自动取最新版 + 选镜像） |
| `installer: true` | 安装版，无法提取（保留安装程序） |
| `no_extract: true` | 单文件绿色工具，跳过提取 |
| `ua` | 自定义下载 UA（个别站点封 curl UA，如 MemTest86/HWiNFO 需浏览器 UA） |

**收录原则**：只收官方 / 官方 GitHub 来源；无官方可脚本化来源的工具直接注释停用
（见 `software-list.yaml` 中对应 note 的停用原因与替代品说明）。

## 版本自动更新

- **自动跟随**：GitHub / TechPowerUp 条目每次运行解析最新 Release，天然最新；
- **官网版本解析**：`url` 含 `{ver}` 占位的条目，下载前自动抓取 `version_url` 页面、
  语义排序取最大版本号生成最新直链（如 CPU-Z 自动从 2.14 跟进到 3.01）；解析失败回退固定 URL；
- **版本去重**：`.download-state.yaml` 记录已下载版本，版本未变则跳过、变了则自动重下并替换绿色目录；
- **更新检查**：`uv run tubagreen --check-updates` 只解析不下载，输出
  `工具 | 当前版本 | 最新版本 | 状态` 对比表。

## 当前状态（63 个条目，其中 12 个已注释停用）

- ✅ **48 个自动下载**：CPU-Z、Prime95、y-cruncher、ThrottleStop、GPU-Z、FurMark、nvidiaInspector、
  CrystalDiskInfo、CrystalDiskMark、DiskGenius、WizTree、WinDirStat、SpaceSniffer、Ventoy、MediaTester、
  MemTest86+、RAMMap、HCI MemTest、MemTest64、ZenTimings、AIDA64、LibreHardwareMonitor、LatencyMon、
  HWiNFO、HWMonitor、Speccy、RWEverything、MouseTester、Everything、Rufus、7-Zip ZS、WinRAR、Dism++、BleachBit、
  Geek、BlueScreenView、BatteryInfoView、Autoruns、TCPView、System Informer、Process Explorer、DesktopOK、gifcam、
  VC++ 运行库合集、.NET Framework 4.8.1 等
- 📦 **免安装单文件**：GPU-Z、MemTest64、Rufus、UltraISO、MediaTester、LatencyMon 等
- 🔧 **安装版**（保留安装程序，需运行安装）：OCCT、VC++ 运行库合集、.NET Framework 4.8.1
- ⏸️ **12 个已注释停用**（无官方可脚本化来源，或功能已被清单内工具覆盖/替代）：DDU、LinX、wPrime、
  SuperPi、dxvachecker、urwtest、SSD-Z、TxBENCH、AS SSD、MemTest86、Thaiphoon Burner、TM5

## 体积瘦身

已删除/停用的大体积冗余项：MemTest86（USB 镜像 1GB，被 MemTest86+ 替代）等。

## 输出结构

```
tools/
├── 处理器工具/CPU-Z/           # 绿色目录（zip 自动解压）
├── 硬盘工具/CrystalDiskInfo/
├── 综合检测/AIDA64 Business/
├── 其他工具/7-Zip ZS/          # 便携版
├── 其他工具/WinRAR/            # 便携版
├── 下载报告.txt                # 每次运行后的详细报告
```

## 依赖

- [uv](https://docs.astral.sh/uv/)（Python 3.12）
- 7-Zip ZS 便携版（解压引擎，默认找 `Downloads\7-Zip-ZS-Portable\7z.exe`，可用 `SEVENZIP` 环境变量指定）

## 维护提示

清单中带 `note: 版本号过期需核对` 的 URL 是固定版本直链，作者更新版本后需手动更新；
GitHub/TPU 类条目自动跟随最新版，无需维护。
