"""一键打包入口（Windows 双击即用，等价于 uv run pack）。

命令行运行也可带参数：
    python 打包.py --download     先全量下载/更新再打包
    python 打包.py --mx 9         高压缩
    python 打包.py --name mybox   自定义包名
    python 打包.py -c 硬盘工具     只打包某分类
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pack  # noqa: E402

if __name__ == "__main__":
    try:
        rc = pack.main()
    except Exception as e:  # 双击运行时把异常打出来，避免窗口一闪而过
        print(f"✗ 打包出错: {e}")
        rc = 1
    try:
        input("\n按回车键退出...")
    except EOFError:  # 管道/CI 等无交互场景不阻塞
        pass
    sys.exit(rc)
