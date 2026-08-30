# tubagreen —— 图吧工具箱软件官网自动下载器

自动从**各软件官方网站**下载图吧工具箱收录的硬件检测工具，
下载后自动解压成**绿色便携版**，按分类组织 —— 效果等同图吧工具箱，
但全部来自官方源、永远最新版、且**优先 Pro 试用版**（如 AIDA64、HDTune Pro、DiskGenius 专业版）。

## 特性

- 🚀 **一键全量下载**：`uv run tubagreen` 下载 + 自动提取
- 📦 **自动绿色化**：用 7-Zip ZS 解压 zip/SFX/NSIS/Inno 安装器，产出免安装目录
- 🔄 **智能解析**：GitHub（HTML 解析，无 API 限流）、TechPowerUp 三步下载、官网直链
- 📡 **实时报告**：每个文件下载/提取完成即时显示，结束生成 `tools/下载报告.txt`
- ♻️ **断点续传 + 自动重试**：网络中断自动续传，错误重试 3 次
- ✅ **去重跳过**：已提取的目录自动跳过，不重复下载

## 用法

```bash
cd C:\Users\yuan\projects\tubagreen

uv run tubagreen              # 全量下载 + 提取
uv run tubagreen --list       # 查看软件清单
uv run tubagreen -c 硬盘工具   # 只下某分类
uv run tubagreen -o CPU-Z,Ventoy   # 只下指定工具
uv run tubagreen --parallel 3 # 并发数
uv run tubagreen --keep-archives  # 保留原始压缩包
```

## 软件清单

`software-list.yaml` 是核心数据源。每个条目支持：

| 字段 | 说明 |
|---|---|
| `url` | 官网稳定直链（版本更新时手动改，note 有提示） |
| `github` / `github_latest` | GitHub 自动解析最新 Release |
| `tpu` | TechPowerUp 三步下载（自动取最新版 + 选镜像） |
| `installer: true` | 安装版，无法提取（保留安装程序） |
| `no_extract: true` | 单文件绿色工具，跳过提取 |

## 当前状态（52 个条目，其中 10 个已注释停用）

- ✅ **41 个自动下载**：CPU-Z、Prime95、ThrottleStop、GPU-Z、FurMark、nvidiaInspector、
  CrystalDiskInfo、CrystalDiskMark、DiskGenius、WizTree、WinDirStat、SpaceSniffer、Ventoy、MediaTester、
  MemTest86、HCI MemTest、MemTest64、ZenTimings、AIDA64、HWiNFO、HWMonitor、Speccy、
  RWEverything、MouseTester、Everything、Rufus、7-Zip ZS、WinRAR、Dism++、Geek、BlueScreenView、
  BatteryInfoView、Process Explorer、DesktopOK、gifcam 等
- 📦 **免安装单文件**：GPU-Z、MemTest64、Rufus、UltraISO、MediaTester 等
- 🔧 **安装版**（分层加密结构，自动解压内嵌分块）：OCCT
- ⏸️ **10 个已注释停用**（无官方/镜像可脚本化来源，功能已被清单内工具覆盖，见
  software-list.yaml 中对应 note）：DDU、LinX、wPrime、SuperPi、dxvachecker、SSD-Z、TxBENCH、
  AS SSD、Thaiphoon Burner、TM5

## 输出结构

```
tools/
├── 处理器工具/CPU-Z/           # 绿色目录（zip 自动解压）
├── 硬盘工具/CrystalDiskInfo/
├── 综合检测/AIDA64 Extreme/
├── 下载报告.txt                # 每次运行后的详细报告
```

## 依赖

- [uv](https://docs.astral.sh/uv/)（Python 3.12）
- 7-Zip ZS 便携版（解压引擎，默认找 `Downloads\7-Zip-ZS-Portable\7z.exe`，可用 `SEVENZIP` 环境变量指定）

## 维护提示

清单中带 `note: 版本号过期需核对` 的 URL 是固定版本直链，作者更新版本后需手动更新；
GitHub/TPU 类条目自动跟随最新版，无需维护。
