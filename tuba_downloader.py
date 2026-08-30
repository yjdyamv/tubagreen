"""tuba-downloader —— 自动从官网下载硬件检测工具，解压绿色便携版，组成个人工具箱。

工作流：官网直链/GitHub/TechPowerUp 解析 → 断点续传下载 → 7-Zip ZS / innoextract
自动提取 → 绿色便携版目录（tools/<分类>/<工具名>/）→ 实时报告。

用法:
    uv run tubagreen                # 全量下载 + 提取（已提取的自动跳过）
    uv run tubagreen --list         # 列出软件清单
    uv run tubagreen -c 硬盘工具     # 只下载某分类
    uv run tubagreen -o CPU-Z,Ventoy  # 只下载指定工具
    uv run tubagreen --parallel 3   # 并发数（默认 2，防服务器限流）
    uv run tubagreen --force        # 强制重新下载
    uv run tubagreen --keep-archives  # 保留原始压缩包/安装器
"""

from __future__ import annotations

import argparse
import html as htmlmod
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml

# ---------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------
ROOT = Path(__file__).resolve().parent          # 脚本位于项目根目录
MANIFEST = ROOT / "software-list.yaml"          # 软件清单（核心数据源）
OUT_DIR = ROOT / "tools"                        # 绿色版输出目录
STATE_FILE = ROOT / ".download-state.yaml"      # 下载状态记录（去重/续传）
REPORT_FILE = OUT_DIR / "下载报告.txt"           # 每次运行的详细报告

UA = "curl/8.0 (tuba-downloader)"               # curl 风格 UA，可绕过 SourceForge 等的 Cloudflare 拦截
TIMEOUT = httpx.Timeout(60.0, connect=20.0)
MAX_RETRIES = 3                                 # 网络类错误自动重试次数

# GitHub 加速镜像（gh-proxy 系）。GitHub 资产下载直连失败时自动加前缀重试。
GITHUB_MIRRORS = [
    "https://gh-proxy.com/",
    "https://ghproxy.net/",
]

# 值得自动重试的网络类异常
RETRYABLE = (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError,
             httpx.ConnectTimeout, httpx.ReadTimeout, httpx.TransportError,
             httpx.WriteError, httpx.DecodingError)

_print_lock = threading.Lock()


def log(msg: str) -> None:
    """线程安全的实时日志。"""
    with _print_lock:
        print(msg, flush=True)


# ---------------------------------------------------------------
# 外部工具定位（7-Zip ZS 解压引擎 / innoextract 提取 Inno 安装器）
# ---------------------------------------------------------------
def find_sevenzip() -> Path:
    """定位 7-Zip ZS：优先项目自带 tools/_tools/7z/，其次 SEVENZIP 环境变量、常见安装路径。"""
    cands = [OUT_DIR / "_tools/7z/7z.exe"]  # 项目自带（ensure_dependencies 自动准备）
    env = os.environ.get("SEVENZIP", "").strip()
    if env:
        cands.append(Path(env))
    cands += [
        Path.home() / "Downloads/7-Zip-ZS-Portable/7z.exe",
        Path("C:/Program Files/7-Zip-Zstandard/7z.exe"),
        Path("C:/Program Files/7-Zip/7z.exe"),
    ]
    for c in cands:
        if c.exists():
            return c
    return Path("7z")


def ensure_dependencies(out_root: Path) -> None:
    """首次运行自动准备解压依赖：7-Zip ZS 便携版 + innoextract。
    7-Zip ZS 官方无 portable 版，这里自动下载安装包并用系统已有 7z 提取。"""
    sz_dir = out_root / "_tools/7z"
    sz_local = sz_dir / "7z.exe"
    if not sz_local.exists():
        print("未找到内置 7-Zip ZS，尝试自动准备 ...")
        # 1) 下载安装包（GitHub 最新 Release）
        installer = out_root / "_tools/7z26.02-zstd-x64.exe"
        installer.parent.mkdir(parents=True, exist_ok=True)
        if not installer.exists():
            try:
                with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": UA}, follow_redirects=True) as c:
                    url, _ = resolve_github(c, "mcmilk/7-Zip-zstd", r"7z26\.02-zstd-x64\.exe$")
                    log("[准备] 下载 7-Zip ZS 安装包 ...")
                    _fetch_to_part(c, url, installer)
            except Exception as e:
                print(f"  ⚠ 7-Zip ZS 安装包下载失败: {type(e).__name__}: {e}")
        # 2) 用系统已有 7z（或已下载的安装包自举）提取成便携版
        extractor = find_sevenzip()
        if not extractor.exists():
            print("  ⚠ 系统中未找到任何 7-Zip。请先安装 7-Zip 或将 SEVENZIP 指向 7z.exe。")
        else:
            sz_dir.mkdir(parents=True, exist_ok=True)
            try:
                r = subprocess.run([str(extractor), "x", "-y", f"-o{sz_dir}", str(installer)],
                                   capture_output=True, text=True, timeout=300)
                if r.returncode == 0 and sz_local.exists():
                    installer.unlink(missing_ok=True)
                    _clean_dir(sz_dir)  # 清掉安装器残留（Uninstall.exe 等）
                    log(f"[准备] 7-Zip ZS 便携版就绪: {sz_local}")
                else:
                    print("  ⚠ 7-Zip ZS 提取失败，安装包已保留在 _tools/，可手动解压。")
            except Exception as e:
                print(f"  ⚠ 7-Zip ZS 提取失败: {e}")
    # 3) innoextract（提取 Inno 安装器，7-Zip 新版不支持）—— 独立于 7z 就绪检查
    ie_dir = out_root / "_tools/innoextract"
    if not (ie_dir / "innoextract.exe").exists():
        try:
            with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": UA}, follow_redirects=True) as c:
                url, fname = resolve_github(c, "dscharrer/innoextract", r"innoextract-\d+[\w.-]*windows\.zip$")
            log("[准备] 下载 innoextract ...")
            zip_p = out_root / "_tools" / fname
            with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": UA}, follow_redirects=True) as c2:
                _fetch_to_part(c2, url, zip_p)
            subprocess.run([str(find_sevenzip()), "x", "-y", f"-o{ie_dir}", str(zip_p)],
                           capture_output=True, text=True, timeout=300)
            exe = next(ie_dir.rglob("innoextract.exe"), None)
            if exe:
                shutil.move(str(exe), ie_dir / "innoextract.exe")
                zip_p.unlink(missing_ok=True)
                log(f"[准备] innoextract 就绪: {ie_dir / 'innoextract.exe'}")
        except Exception as e:
            print(f"  ⚠ innoextract 自动获取失败: {type(e).__name__}: {e}")


def find_innoextract() -> Path | None:
    """定位 innoextract（7-Zip 新版已移除 Inno Setup 支持，用它兜底）。"""
    p = OUT_DIR / "_tools/innoextract/innoextract.exe"
    return p if p.exists() else None


INNOEXTRACT = find_innoextract()


def sevenzip(args_list: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    """调用 7-Zip ZS 命令行。"""
    return subprocess.run([str(find_sevenzip()), *args_list],
                          capture_output=True, text=True, timeout=timeout)


# ---------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------
@dataclass
class Tool:
    """软件清单条目。下载方式按优先级：github_latest > github > tpu > url。"""
    name: str
    category: str
    homepage: str = ""
    # 下载源
    url: str = ""                      # 官网稳定直链（可含 {ver} 占位，配合 version_pattern 自动取最新）
    version_url: str = ""             # 版本信息页（默认回退 homepage）
    version_pattern: str = ""         # 从版本页提取最新版号的正则（取捕获组/整体，多版本取最大）
    github: str = ""                   # GitHub 仓库（自动解析最新 Release，需 asset_pattern）
    asset_pattern: str = ""
    github_latest: str = ""            # GitHub 仓库（用 /releases/latest/download/ 永久链接）
    github_latest_file: str = ""
    tpu: str = ""                      # TechPowerUp 下载页 slug（自动三步解析）
    tpu_pattern: str = ""              # TPU 版本标题匹配（如 "TechPowerUp GPU-Z" 排除定制皮肤版）
    mediafire: str = ""                # MediaFire 页面链接（官方网盘托管，一般需手动）
    ua: str = ""                       # 自定义 UA（默认 curl/8.0；个别站点封 curl UA 需改浏览器 UA）
    # 提取行为
    no_extract: bool = False           # 单文件工具，跳过提取（如 GPU-Z、Rufus）
    installer: bool = False            # 安装版，保留原安装程序（如 OCCT）
    post_fix_encoding: str = ""        # 提取后把 GBK 文本转 UTF-8（相对工具目录，如 Language/lang_cn.txt）
    # 运行时填充
    note: str = ""
    resolved_url: str = ""
    filename: str = ""
    resolved_version: str = ""        # 版本解析得到的最新版号（version_pattern 命中时）


@dataclass
class Result:
    tool: Tool
    status: str  # ok / extracted / skip / manual / fail
    size: int = 0
    detail: str = ""


def load_manifest() -> list[Tool]:
    """读取 software-list.yaml 为 Tool 列表。"""
    data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    tools: list[Tool] = []
    for cat, items in data["categories"].items():
        for it in items:
            tools.append(Tool(name=it["name"], category=cat,
                              **{k: v for k, v in it.items() if k != "name"}))
    return tools


# ---------------------------------------------------------------
# 下载地址解析（全部避开 GitHub API，杜绝限流 403）
# ---------------------------------------------------------------
def resolve_github(client: httpx.Client, repo: str, pattern: str) -> tuple[str, str]:
    """HTML 方式解析最新 Release：/releases/latest 302 → tag → expanded_assets 页。
    返回 (下载URL, 文件名)。"""
    r = client.get(f"https://github.com/{repo}/releases/latest", follow_redirects=False)
    if r.status_code not in (302, 301, 303):
        raise RuntimeError(f"GitHub latest 未重定向 (HTTP {r.status_code})")
    tag = r.headers.get("location", "").rstrip("/").rsplit("/", 1)[-1]
    if not tag:
        raise RuntimeError("GitHub latest 未返回 tag")
    r2 = client.get(f"https://github.com/{repo}/releases/expanded_assets/{tag}")
    r2.raise_for_status()
    pat = re.compile(pattern or r".*")
    for name in re.findall(rf'href="/{re.escape(repo)}/releases/download/{re.escape(tag)}/([^"]+)"', r2.text):
        name = htmlmod.unescape(name)
        if pat.search(name):
            return f"https://github.com/{repo}/releases/download/{tag}/{name}", name
    raise RuntimeError(f"GitHub {repo} 无匹配资产（pattern={pattern}）")


def resolve_tpu(client: httpx.Client, slug: str, pattern: str = "") -> tuple[str, str]:
    """TechPowerUp 三步下载：GET 取版本 id → POST 选镜像 → POST 拿直链。
    返回 (下载URL, 文件名)。pattern 用于排除定制皮肤版（如 ASUS ROG）。"""
    base = f"https://www.techpowerup.com/download/{slug}/"
    r = client.get(base)
    r.raise_for_status()
    # 提取 (版本标题, id) 对
    pairs = [(re.sub(r'<[^>]+>', '', h).strip(), i)
             for h, i in re.findall(r'<h3[^>]*>(.*?)</h3>.*?name="id" value="(\d+)"', r.text, re.S)]
    if pairs:
        pat = re.compile(pattern) if pattern else None
        fid = next((i for h, i in pairs if pat and pat.search(h)), pairs[0][1])
    else:  # 页面结构异常时退回最大 id（通常是最新版）
        ids = [int(m) for m in re.findall(r'name="id" value="(\d+)"', r.text)]
        if not ids:
            raise RuntimeError("TPU 页面未找到下载 id")
        fid = str(max(ids))
    # 选镜像服务器
    r2 = client.post(base, data={"id": fid})
    r2.raise_for_status()
    servers = re.findall(r'name="server_id" value="(\d+)"', r2.text)
    if not servers:
        raise RuntimeError("TPU 镜像页未找到服务器")
    # 拿最终直链
    r3 = client.post(base, data={"id": fid, "server_id": servers[0]})
    r3.raise_for_status()
    url = str(r3.url)
    return url, url.split("/")[-1].split("?")[0]


def _ver_key(v: str) -> tuple:
    """版本号语义排序键：'3.01' / '1.4.1.1032' / '30.19b20' 等 → 可比较元组。"""
    parts = re.split(r"(\d+|[^\d.]+)", v.lower())
    key: list = []
    for p in parts:
        if not p:
            continue
        if p.isdigit():
            key.append((0, int(p)))
        else:
            key.append((1, p))
    return tuple(key)


def _fetch_page(client: httpx.Client, page_url: str, ua: str = UA) -> str:
    """抓取页面文本：httpx 优先，失败/被拦时 curl 直连兜底（代理差异场景）。"""
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            r = client.get(page_url)
            r.raise_for_status()
            return r.text
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(2 ** attempt)
    try:
        curl = shutil.which("curl") or str(Path("C:/Windows/System32/curl.exe"))
        r = subprocess.run([curl, "-fsL", "-A", ua, "--max-time", "60", page_url],
                           capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and r.stdout:
            return r.stdout
        last_err = RuntimeError(f"curl exit {r.returncode}")
    except Exception as e2:
        last_err = e2
    raise RuntimeError(f"版本页获取失败: {type(last_err).__name__}: {last_err}")


def resolve_version(client: httpx.Client, page_url: str, pattern: str, ua: str = UA) -> str:
    """抓取版本页，用正则提取所有版本号，语义排序取最大（最新）。返回最新版号。
    网络抖动/反爬时自动重试并 curl 兜底。"""
    pat = re.compile(pattern, re.I)
    text = _fetch_page(client, page_url, ua)
    found: list[str] = []
    for m in pat.finditer(text):
        v = m.group(1) if m.lastindex else m.group(0)
        v = htmlmod.unescape(v).strip()
        if v and re.search(r"\d", v):
            found.append(v)
    if not found:
        raise RuntimeError(f"版本页未匹配到版本号 (pattern={pattern})")
    return max(set(found), key=_ver_key)


# ---------------------------------------------------------------
# 提取引擎（把下载文件解压成绿色便携版）
# ---------------------------------------------------------------
# 安装器残留特征（NSIS 插件目录、卸载程序等）
INSTALLER_JUNK = re.compile(r"^(unins\d+\.exe|uninst\.exe|uninstall\.exe|uninstaller\.exe|\$PLUGINSDIR|\$[A-Z]+)$", re.I)


def try_innoextract(archive: Path, tmp: Path) -> bool:
    """Inno Setup 安装器提取（7-Zip 新版不支持 Inno，需 innoextract）。"""
    if not INNOEXTRACT:
        return False
    try:
        r = subprocess.run([str(INNOEXTRACT), "--extract", "--output-dir", str(tmp), str(archive)],
                           capture_output=True, text=True, timeout=600)
        return r.returncode == 0 and any(tmp.iterdir())
    except Exception:
        return False


def probe_type(archive: Path) -> str:
    """探测文件类型：archive / nsis / pe-sfx（含内嵌数据） / pe（纯） / unknown。"""
    try:
        r = sevenzip(["l", str(archive)])
    except Exception:
        return "unknown"
    types = re.findall(r"^Type = (\w+)", r.stdout, re.M)
    has_embed = bool(re.search(r"^Path = \[0\]", r.stdout, re.M))
    if "Nsis" in types or "Inno" in types:
        return "nsis"
    if any(t in types for t in ("zip", "7z", "rar", "Rar5", "tar", "gzip", "bzip2", "xz", "zstd")):
        return "archive"
    if "PE" in types:
        return "pe-sfx" if has_embed else "pe"
    return "unknown"


def _clean_dir(d: Path) -> None:
    """清理安装器残留（卸载程序、NSIS 插件目录、空目录）。"""
    for p in list(d.rglob("*")):
        if p.is_file() and (INSTALLER_JUNK.match(p.name) or p.suffix.lower() == ".msi"):
            try:
                p.unlink()
            except OSError:
                pass
    for p in list(d.rglob("*")):
        if p.is_dir() and INSTALLER_JUNK.match(p.name):
            try:
                shutil.rmtree(p)
            except OSError:
                pass
    for p in sorted(d.rglob("*"), reverse=True):
        if p.is_dir() and not any(p.iterdir()):
            try:
                p.rmdir()
            except OSError:
                pass


def extract_one(tool: Tool, archive: Path, out_root: Path) -> str:
    """把下载文件提取成绿色版目录 tools/<分类>/<工具名>/。返回 extracted / skip / failed。"""
    dest = out_root / tool.category / tool.name
    if tool.no_extract or tool.installer:
        return "skip"
    t = probe_type(archive)
    if t == "pe" and not INNOEXTRACT:
        return "skip"  # 纯 PE 单文件且无 innoextract → 无需提取
    tmp = archive.parent / f".tmp_{tool.name}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        # 按类型选择提取方式
        if t == "pe-sfx":
            ok = sevenzip(["x", "-t#", "-y", "-o" + str(tmp), str(archive)]).returncode == 0
        elif t in ("pe", "unknown"):
            ok = try_innoextract(archive, tmp)  # Inno 安装器兜底
        else:
            ok = sevenzip(["x", "-y", "-o" + str(tmp), str(archive)]).returncode == 0
        if not ok:
            shutil.rmtree(tmp, ignore_errors=True)
            return "skip" if t == "pe" else "failed"
        # 分层安装器（OCCT 等）：内嵌编号 zip 分块 → 全部解压合并
        inner = sorted(tmp.glob("*.zip")) + sorted(tmp.glob("*.7z"))
        if len(inner) >= 3:
            for z in inner:
                sevenzip(["x", "-y", "-o" + str(tmp), str(z)])
                z.unlink(missing_ok=True)
        # 提升单顶层目录
        items = [p for p in tmp.iterdir() if p.name != "[0]"]
        src = tmp
        if len(items) == 1 and items[0].is_dir():
            src = items[0]
        elif len(items) == 1 and items[0].name == "[0]":
            src = tmp / "[0]"
        # 二次提取：结果只有单个安装器 exe（如 RWEverything 官网 zip 内含 Inno Setup）
        setup_exes = [p for p in src.rglob("*.exe") if p.is_file()
                      and p.name.lower().startswith(("setup", "install", "rwsetup"))]
        if len(setup_exes) == 1 and len([p for p in src.rglob("*") if p.is_file()]) <= 2:
            sub = src / ".sub"
            sub.mkdir(exist_ok=True)
            if try_innoextract(setup_exes[0], sub):
                for f in sub.rglob("*"):
                    if f.is_file():
                        tgt = src / f.relative_to(sub)
                        tgt.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(f), str(tgt))
                shutil.rmtree(sub, ignore_errors=True)
                setup_exes[0].unlink(missing_ok=True)
        _clean_dir(src)
        if dest.exists():
            shutil.rmtree(dest)
        src.rename(dest)
        return "extracted"
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        return "failed"


def fix_gbk_to_utf8(path: Path) -> bool:
    """GBK 编码文本转 UTF-8（系统 ACP=65001 时修复中文乱码）。返回是否转换。"""
    try:
        data = path.read_bytes()
    except OSError:
        return False
    gbk_hits = len(re.findall(rb"[\xb0-\xf7][\x40-\xfe]", data))
    utf8_hits = len(re.findall(rb"[\xe4-\xe9][\x80-\xbf][\x80-\xbf]", data))
    if gbk_hits < 3 or utf8_hits > gbk_hits // 2:
        return False  # 无 GBK 中文或已是 UTF-8
    try:
        text = data.decode("gbk")
    except UnicodeDecodeError:
        text = data.decode("gb18030", errors="replace")
    path.write_text(text, encoding="utf-8")
    return True


def post_fix(tool: Tool, out_root: Path) -> None:
    """提取后的编码修复钩子（AIDA64 中文语言文件等）。"""
    if not tool.post_fix_encoding:
        return
    target = out_root / tool.category / tool.name / tool.post_fix_encoding
    if target.exists() and fix_gbk_to_utf8(target):
        log(f"[编码修复] {tool.category}/{tool.name}  {tool.post_fix_encoding}: GBK -> UTF-8")


# ---------------------------------------------------------------
# 下载核心（httpx 流式 + 断点续传 + 重试 + curl 兜底）
# ---------------------------------------------------------------
def _stream_to_part(client: httpx.Client, url: str, part: Path, start: int, ua: str = UA) -> None:
    """httpx 流式下载到 .part（支持 Range 续传）。"""
    headers = {"User-Agent": ua}
    if start > 0:
        headers["Range"] = f"bytes={start}-"
    with client.stream("GET", url, headers=headers) as r:
        if r.status_code == 416:  # Range 无效 → 整文件重下
            with client.stream("GET", url, headers={"User-Agent": ua}) as r2:
                r2.raise_for_status()
                with open(part, "wb") as f:
                    for chunk in r2.iter_bytes(65536):
                        f.write(chunk)
            return
        r.raise_for_status()
        with open(part, "ab" if start else "wb") as f:
            for chunk in r.iter_bytes(65536):
                f.write(chunk)


def mirror_urls(url: str):
    """返回下载候选序列：[原始 URL, *GitHub 镜像 URL]。仅对 GitHub 资产启用镜像。"""
    yield url
    if "github.com/" in url or "githubusercontent.com" in url:
        for m in GITHUB_MIRRORS:
            yield m + url


def _fetch_to_part(client: httpx.Client, url: str, part: Path, start: int = 0, ua: str = UA) -> None:
    """统一下载：httpx 流式（续传）→ curl 兜底 → GitHub 镜像回退。
    httpx 报 HTTP 错误时也交给 curl 再试一次（代理差异下 curl 直连往往可成功）。"""
    last_err: Exception | None = None
    for u in mirror_urls(url):
        try:
            _stream_to_part(client, u, part, start, ua)
            return
        except RETRYABLE as e:
            last_err = e
            start = part.stat().st_size if part.exists() else 0
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (403, 404, 451) and u != url:
                continue  # 镜像不可用则换下一个
            last_err = e
        # httpx 直连失败时用 curl 再试一次（部分服务器/网络路径只吃 curl）
        try:
            _curl_to_part(u, part, ua)
            return
        except Exception as e2:
            last_err = e2
            if u != url:
                log(f"  ↻ 镜像失败，换下一个: {u}")
            continue
    raise RuntimeError(f"所有下载源均失败: {type(last_err).__name__}: {last_err}")


def _curl_to_part(url: str, part: Path, ua: str = UA) -> None:
    """curl 兜底下载（部分服务器对 httpx 流式连接限流断连，如 TechPowerUp 大文件）。"""
    curl = shutil.which("curl") or str(Path("C:/Windows/System32/curl.exe"))
    r = subprocess.run([curl, "-fsL", "-C", "-", "-A", ua, "--retry", "2",
                        "-o", str(part), url], timeout=900)
    if r.returncode != 0:
        raise RuntimeError(f"curl 下载失败 (exit {r.returncode})")


def download_one(client: httpx.Client, tool: Tool, out_dir: Path, force: bool,
                 state: dict, lock: threading.Lock) -> Result:
    """下载单个工具（含地址解析、断点续传、重试）。"""
    res = Result(tool=tool, status="fail")
    try:
        # ---- 解析下载地址 ----
        if tool.github:
            tool.resolved_url, tool.filename = resolve_github(client, tool.github, tool.asset_pattern)
        elif tool.github_latest:
            tool.filename = tool.github_latest_file
            tool.resolved_url = f"https://github.com/{tool.github_latest}/releases/latest/download/{tool.filename}"
        elif tool.tpu:
            tool.resolved_url, tool.filename = resolve_tpu(client, tool.tpu, tool.tpu_pattern)
        elif tool.url:
            tool.resolved_url = tool.url
            # filename 可在清单中显式指定（如 fwlink 跳转链接无法从 URL 推导文件名）
            tool.filename = tool.filename or tool.url.split("/")[-1].split("?")[0]
            # 版本自动更新：抓版本页取最新版，套 {ver} 模板生成直链；失败不回退含占位符的 URL
            if tool.version_pattern:
                try:
                    v = resolve_version(client, tool.version_url or tool.homepage, tool.version_pattern, tool.ua or UA)
                    tool.resolved_version = v
                    if "{" in tool.resolved_url:
                        tool.resolved_url = tool.resolved_url.format(ver=v, ver_nodot=v.replace(".", "_"))
                        if "{ver}" in tool.filename:
                            tool.filename = tool.filename.format(ver=v, ver_nodot=v.replace(".", "_"))
                        else:
                            tool.filename = tool.resolved_url.split("/")[-1].split("?")[0]
                        log(f"[版本] {tool.category}/{tool.name}  最新 {v} -> {tool.filename}")
                except Exception as e:
                    if "{" in tool.resolved_url:
                        res.detail = f"版本解析失败，无法生成直链: {type(e).__name__}: {e}"
                        return res
                    log(f"[版本解析失败] {tool.name}: {type(e).__name__}: {e}（回退固定 URL）")
            if not tool.filename:
                res.detail = "URL 无文件名"
                return res
        elif tool.mediafire:
            res.status = "manual"
            res.detail = "官方用 MediaFire 托管（部分文件被标记，需手动）"
            return res
        else:
            res.status = "manual"
            res.detail = "需手动从官网下载" + (f"（{tool.note}）" if tool.note else "")
            return res

        dest = out_dir / tool.category
        dest.mkdir(parents=True, exist_ok=True)
        fpath = dest / tool.filename
        part = fpath.with_suffix(fpath.suffix + ".part")

        with lock:
            prev = state.get(tool.name)
        # 已提取为绿色目录 → 版本一致才跳过；版本可跟踪且不一致 → 重下更新
        ext_dir = out_dir / tool.category / tool.name
        if not force and ext_dir.exists() and any(ext_dir.iterdir()):
            if not tool.resolved_version:
                res.status = "skip"
                res.detail = "已提取"
                return res
            prev_ver = prev.get("version") if prev else None
            if prev_ver == tool.resolved_version:
                res.status = "skip"
                res.detail = "已提取"
                return res
            log(f"[更新] {tool.name}  绿色目录为旧版本{prev_ver or '?'}，重新下载 {tool.resolved_version}")
        # 已下载且大小一致 → 跳过（有版本解析时须版本一致才跳过）
        if not force and fpath.exists() and prev and prev.get("url") == tool.resolved_url \
                and prev.get("size") == fpath.stat().st_size \
                and (not tool.resolved_version or prev.get("version") == tool.resolved_version):
            res.status = "skip"
            res.size = fpath.stat().st_size
            res.detail = "已存在"
            return res
        # 版本变了但已下载旧版文件在根目录 → 直接重下
        if not force and fpath.exists() and tool.resolved_version \
                and prev and prev.get("version") and prev.get("version") != tool.resolved_version:
            log(f"[更新] {tool.name}: {prev.get('version')} -> {tool.resolved_version}（重新下载）")
        start = part.stat().st_size if part.exists() and not force else 0

        log(f"[下载中] {tool.category}/{tool.name}  {tool.filename}"
            + (f"  (续传 {start/1048576:.1f}MB)" if start else ""))
        last_err: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            if attempt:
                backoff = 3 ** attempt + random.uniform(0, 1)
                log(f"  ↻ 第 {attempt} 次重试（{backoff:.0f}s 后）: {type(last_err).__name__}")
                time.sleep(backoff)
            try:
                _fetch_to_part(client, tool.resolved_url, part, start, tool.ua or UA)
                break
            except RETRYABLE as e:
                last_err = e
                start = part.stat().st_size if part.exists() else 0
                continue
            except RuntimeError as e:
                # _fetch_to_part 所有源均失败（含 curl 兜底瞬时失败）→ 外层重试
                if attempt < MAX_RETRIES:
                    last_err = e
                    start = part.stat().st_size if part.exists() else 0
                    continue
                res.detail = f"{type(e).__name__}: {e}"
                return res
            except httpx.HTTPStatusError as e:
                # 瞬态状态码（限流/服务端抖动）重试，永久性错误（404 等）直接失败
                if e.response.status_code in (403, 429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
                    last_err = e
                    start = part.stat().st_size if part.exists() else 0
                    continue
                res.detail = f"HTTP {e.response.status_code}: {e.response.url}"
                return res
            except Exception as e:
                res.detail = f"{type(e).__name__}: {e}"
                return res
        else:  # 重试耗尽
            res.detail = f"重试 {MAX_RETRIES} 次仍失败: {type(last_err).__name__}"
            return res

        part.replace(fpath)
        with lock:
            rec = {"url": tool.resolved_url, "size": fpath.stat().st_size}
            if tool.resolved_version:
                rec["version"] = tool.resolved_version
            state[tool.name] = rec
        res.status = "ok"
        res.size = fpath.stat().st_size
        res.detail = f"{res.size / 1048576:.1f} MB"
        log(f"[完成] {tool.category}/{tool.name}  {res.size/1048576:.1f} MB -> {fpath.relative_to(out_dir)}")
        return res
    except Exception as e:
        res.detail = f"{type(e).__name__}: {e}"
        return res


# ---------------------------------------------------------------
# 报告与主流程
# ---------------------------------------------------------------
def write_report(results: list[Result]) -> tuple[int, int, int, int]:
    """生成 tools/下载报告.txt。返回 (成功, 跳过, 手动, 失败) 数量。"""
    ok = [r for r in results if r.status in ("ok", "extracted")]
    skip = [r for r in results if r.status == "skip"]
    manual = [r for r in results if r.status == "manual"]
    fail = [r for r in results if r.status == "fail"]
    lines = [
        "=" * 50,
        "硬件工具箱软件自动下载报告",
        f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 50,
        f"\n【绿色便携版 {len(ok)} 个】",
    ]
    for r in sorted(ok, key=lambda x: x.tool.category):
        lines.append(f"  ✅ {r.tool.category}/{r.tool.name}  {r.detail}")
    if skip:
        lines.append(f"\n【已存在跳过 {len(skip)} 个】")
        for r in skip:
            lines.append(f"  ⏭️  {r.tool.category}/{r.tool.name}")
    if manual:
        lines.append(f"\n【需手动下载 {len(manual)} 个】")
        for r in manual:
            lines.append(f"  📋 {r.tool.category}/{r.tool.name}  {r.detail}")
    if fail:
        lines.append(f"\n【下载失败 {len(fail)} 个】")
        for r in fail:
            lines.append(f"  ❌ {r.tool.category}/{r.tool.name}  {r.detail}")
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text("\n".join(lines), encoding="utf-8")
    return len(ok), len(skip), len(manual), len(fail)


def check_updates() -> int:
    """--check-updates：只解析各工具最新版本，与本地已下载版本对比，输出报告。
    不发下载，也不写状态文件。"""
    tools = load_manifest()
    state: dict = {}
    if STATE_FILE.exists():
        state = yaml.safe_load(STATE_FILE.read_text(encoding="utf-8")) or {}
    print("正在检查各工具最新版本 ...\n")
    rows: list[tuple[str, str, str, str]] = []  # (分类/名称, 当前, 最新, 状态)
    with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": UA}, follow_redirects=True) as client:
        for t in tools:
            cur = str(state.get(t.name, {}).get("version", "-"))
            if t.github or t.github_latest or t.tpu:
                latest = "自动跟随"
                status = "✓"
            elif t.version_pattern:
                try:
                    v = resolve_version(client, t.version_url or t.homepage, t.version_pattern, t.ua or UA)
                    latest = v
                    status = "✓" if cur != "-" and cur == v else ("⚠️ 有新版本" if cur != "-" else "未下载")
                except Exception as e:
                    latest = f"解析失败({type(e).__name__})"
                    status = "?"
            elif t.url:
                latest = "固定URL"
                status = "→"
            else:
                latest = "手动"
                status = "·"
            rows.append((f"{t.category}/{t.name}", cur, latest, status))
    w = max(len(r[0]) for r in rows) + 2
    print(f"{'工具':<{w}}{'当前版本':<14}{'最新版本':<16}状态")
    print("-" * (w + 46))
    for name, cur, latest, status in rows:
        print(f"{name:<{w}}{cur:<14}{latest:<16}{status}")
    upd = sum(1 for r in rows if r[3] == "⚠️ 有新版本")
    print(f"\n共 {len(rows)} 个工具：{upd} 个可更新（运行下载命令即自动取最新版）")
    return 0


def run(args: argparse.Namespace) -> int:
    """主流程：准备依赖 → 过滤工具 → 并发下载 → 提取绿色化 → 报告。"""
    if args.check_updates:
        return check_updates()
    ensure_dependencies(OUT_DIR)  # 首次运行自动准备 7-Zip ZS 便携版等依赖
    tools = load_manifest()
    if args.list:
        cur = ""
        for t in tools:
            if t.category != cur:
                cur = t.category
                print(f"\n== {cur} ==")
            src = t.github or t.github_latest or t.url or t.tpu or t.mediafire or "(手动)"
            print(f"  {t.name:<28} {src[:65]}")
        return 0

    if args.category:
        tools = [t for t in tools if t.category == args.category]
    if args.only:
        names = set(x.strip() for x in args.only.split(","))
        tools = [t for t in tools if t.name in names]
    if not tools:
        print("没有匹配的工具。用 --list 查看清单。")
        return 1

    state: dict = {}
    if STATE_FILE.exists():
        state = yaml.safe_load(STATE_FILE.read_text(encoding="utf-8")) or {}
    lock = threading.Lock()

    print(f"开始下载 {len(tools)} 个工具，并发 {args.parallel} ...\n")
    results: list[Result] = []

    # ---- 阶段 1：并发下载 ----
    with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": UA}, follow_redirects=True) as client:
        with ThreadPoolExecutor(max_workers=args.parallel) as pool:
            futs = {pool.submit(download_one, client, t, OUT_DIR, args.force, state, lock): t for t in tools}
            for fut in as_completed(futs):
                results.append(fut.result())

    with lock:
        STATE_FILE.write_text(yaml.safe_dump(state, allow_unicode=True, sort_keys=False), encoding="utf-8")

    # ---- 阶段 2：提取绿色化 ----
    print("\n----- 提取压缩包为绿色便携版 -----")
    for d in OUT_DIR.rglob(".tmp_*"):  # 清理上次残留的临时目录
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
    for res in results:
        if res.status not in ("ok", "skip"):
            continue
        post_fix(res.tool, OUT_DIR)  # 编码修复钩子（如 AIDA64 中文语言文件）
        tool, fpath = res.tool, OUT_DIR / res.tool.category / res.tool.filename
        if not fpath.exists():
            continue
        if tool.installer:
            sub = OUT_DIR / tool.category / tool.name
            sub.mkdir(parents=True, exist_ok=True)
            shutil.move(str(fpath), sub / tool.filename)
            log(f"[安装版] {tool.category}/{tool.name}  保留安装程序（{tool.note or '无法提取'}）")
            res.detail = "安装版，需运行安装"
            continue
        if tool.no_extract:
            sub = OUT_DIR / tool.category / tool.name
            sub.mkdir(parents=True, exist_ok=True)
            shutil.move(str(fpath), sub / tool.filename)
            log(f"[单文件] {tool.category}/{tool.name}  免安装单文件 -> {tool.name}/{tool.filename}")
            res.detail = f"单文件 {tool.name}/{tool.filename}"
            continue
        st = extract_one(tool, fpath, OUT_DIR)
        if st == "extracted":
            if not args.keep_archives:
                fpath.unlink(missing_ok=True)
            log(f"[已提取] {tool.category}/{tool.name}  ->  {tool.category}/{tool.name}/")
            res.status = "extracted"
            res.detail = f"绿色版 {tool.category}/{tool.name}"
        elif st == "skip":
            log(f"[免提取] {tool.category}/{tool.name}  已是可执行文件")
        else:
            log(f"[提取失败] {tool.category}/{tool.name}  保留原文件")
            res.detail += "（提取失败，保留原文件）"

    # ---- 阶段 3：汇总报告 ----
    n_ok, n_skip, n_manual, n_fail = write_report(results)
    ext = sum(1 for r in results if r.status == "extracted")
    print(f"\n===== 汇总：绿色版 {n_ok}（其中新提取 {ext}），已存在 {n_skip}，需手动 {n_manual}，失败 {n_fail} =====")
    if n_ok:
        print("刚刚完成下载：")
        for r in sorted([r for r in results if r.status in ("ok", "extracted")], key=lambda x: x.tool.category):
            print(f"  ✅ {r.tool.category}/{r.tool.name}  {r.detail}")
    if n_fail:
        print("失败项：")
        for r in results:
            if r.status == "fail":
                print(f"  ❌ {r.tool.name}: {r.detail}")
    if n_manual:
        print(f"需手动（详见 {REPORT_FILE.name}）：")
        for r in results:
            if r.status == "manual":
                print(f"  📋 {r.tool.name}")
    print(f"\n详细报告已写入: {REPORT_FILE}")
    return 0 if n_fail == 0 else 2


def main() -> None:
    p = argparse.ArgumentParser(description="硬件工具箱软件官网自动下载器")
    p.add_argument("--list", action="store_true", help="列出软件清单")
    p.add_argument("--check-updates", action="store_true", help="检查各工具最新版本（不下载）")
    p.add_argument("-c", "--category", help="只下载指定分类")
    p.add_argument("-o", "--only", help="只下载指定工具（逗号分隔）")
    p.add_argument("--parallel", type=int, default=2, help="并发下载数（默认 2，防服务器限流）")
    p.add_argument("--force", action="store_true", help="强制重新下载")
    p.add_argument("--keep-archives", action="store_true", help="保留原始压缩包/安装器（默认提取后删除）")
    args = p.parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
