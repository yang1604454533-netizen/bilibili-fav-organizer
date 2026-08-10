"""B站收藏夹整理助手 - 程序入口"""

import sys


def main() -> None:
    try:
        from app.gui import run
    except ImportError:
        # 兼容从项目根目录运行时 app 包不在路径的情况
        sys.path.insert(0, __file__.rsplit("/", 1)[0])
        from app.gui import run
    run()


if __name__ == "__main__":
    main()