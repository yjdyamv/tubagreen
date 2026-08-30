"""本地打包脚本 —— 把 tools/ 绿色工具箱打成 7z 发布包。

与 CI（.github/workflows/build-release.yml）保持同一套规则：
  - 排除 tools/_tools/（内部依赖，7z/innoextract）
  - 排除 tools/下载报告.txt
  - 排除 software-list.yaml 中 pack:false 的工具（体积大/版权/重复）

用法:
    uv run pack                  # 打包（自动取 7z，压缩等级 mx=5）
    uv run pack --download       # 先全量下载/更新，再打包（等价于 CI 流程）
    uv run pack --mx 9           # 指定压缩等级
    uv run pack --name mybox     # 自定义包名（默认 toolbox-YYYY.MM.N）
    uv run pack --dry-run        # 只打印将执行的 7z 命令，不实际打包
    uv run pack -c 硬盘工具       # 只打包某个分类（其他分类不进包）
"""

from __future__ import annotations

import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 延迟导入：--help / 语法错误时不要被 tuba_downloader 的依赖拖累
def _import_core():
    import tuba_downloader as core
    return core.find_sevenzip(), core.load_manifest, core.OUT_DIR


def next_archive_name(out_dir: Path) -> str:
    """toolbox-YYYY.MM.N.7z；N = 已有同名包数量 + 1（本地构建号）。"""
    ym = datetime.date.today().strftime("%Y.%m")
    prefix = f"toolbox-{ym}"
    n = 0
    for f in ROOT.glob(f"{prefix}*.7z"):
        m = re.search(rf"{re.escape(prefix)}\.(\d+)\.7z$", f.name)
        if m:
            n = max(n, int(m.group(1)))
    return f"{prefix}.{n + 1}"


def collect_excludes(only_cat: str | None) -> list[str]:
    """返回相对 tools/ 的排除模式列表（_tools、报告、pack:false 工具）。"""
    _, load_manifest, _ = _import_core()
    excludes = ["tools/_tools", "tools/下载报告.txt"]  # _tools 整目录剔除（连空目录条目也不留）
    for t in load_manifest():
        if only_cat and t.category != only_cat:
            continue  # 只打分类时，其余分类直接不进包，无需逐条排除
        if not t.pack:
            excludes.append(f"tools/{t.category}/{t.name}")
    return excludes


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--download", action="store_true", help="打包前先运行 uv run tubagreen 全量下载/更新")
    p.add_argument("--mx", type=int, default=5, help="7z 压缩等级（1-9，默认 5，与 CI 一致）")
    p.add_argument("--name", default="", help="自定义包名（不含 .7z，默认 toolbox-YYYY.MM.N）")
    p.add_argument("--dry-run", action="store_true", help="只打印命令，不实际打包")
    p.add_argument("-c", "--category", default="", help="只打包某分类（如 硬盘工具）")
    args = p.parse_args(argv)

    sevenzip, _, out_dir = _import_core()
    if not Path(sevenzip).exists():
        print(f"✗ 找不到 7-Zip：{sevenzip}")
        print("  请安装 7-Zip 或设置 SEVENZIP 环境变量指向 7z.exe。")
        return 1
    if not out_dir.is_dir():
        print(f"✗ 没有 {out_dir}，先运行 uv run tubagreen 下载，或用 --download。")
        return 1

    # 可选：先下载再打包（与 CI 流程一致）
    if args.download:
        print("▶ 先全量下载/更新 ...\n")
        if subprocess.run([sys.executable, "-m", "tuba_downloader"], cwd=ROOT).returncode != 0:
            print("✗ 下载过程出错，已中止打包。")
            return 1

    name = args.name or next_archive_name(out_dir)
    if not name.endswith(".7z"):
        name += ".7z"
    archive = ROOT / name

    # 归档源：全量 tools/*，或仅某分类 tools/<分类>/*
    scope = f"tools/{args.category}/*" if args.category else "tools/*"
    if args.category and not (out_dir / args.category).is_dir():
        print(f"✗ 分类不存在：{args.category}（可选：{'、'.join(sorted(d.name for d in out_dir.iterdir() if d.is_dir() and not d.name.startswith('_'))) }）")
        return 1

    excludes = collect_excludes(args.category)
    cmd = [str(sevenzip), "a", "-t7z", f"-mx={args.mx}", str(archive), scope]
    cmd += [f"-xr!{e}" for e in excludes]

    print(f"7-Zip  : {sevenzip}")
    print(f"压缩等级: mx={args.mx}")
    print(f"输出   : {archive.name}")
    print(f"排除   : {len(excludes)} 项")
    for e in excludes:
        print(f"  -xr!{e}")
    print()
    print("命令: " + " ".join(cmd) + "\n")

    if args.dry_run:
        return 0

    print("▶ 打包中（耗时取决于体积，mx=5 约需几分钟）...\n")
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        print(f"\n✗ 打包失败（7z 退出码 {r.returncode}）")
        return r.returncode

    size_mb = archive.stat().st_size / 1024 / 1024
    raw_scope = out_dir / args.category if args.category else out_dir
    raw_mb = sum(f.stat().st_size for f in raw_scope.rglob("*") if f.is_file()) / 1024 / 1024
    print(f"\n✔ 打包完成：{archive.name}  ({size_mb:.1f} MB，原始 {raw_mb:.0f} MB，压缩率 {size_mb/raw_mb*100:.0f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
