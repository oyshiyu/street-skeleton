import argparse


def main():
    parser = argparse.ArgumentParser(description="OSM 城市道路骨架 · 无参数启动可视化界面")
    parser.add_argument("input", nargs="?", help="城市 .osm.pbf 文件；https://extract.bbbike.org/")
    parser.add_argument("--output", default="outputs/city", help="结果目录（须为空或不存在）")
    parser.add_argument("--planner", metavar="RESULTS", help="载入已生成的结果目录，打开电缆选线地图")
    parser.add_argument("--tolerance", type=float, default=8, help="交叉点合并距离，1–20 米")
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.input and args.planner:
        parser.error("input 与 --planner 不能同时使用")
    if args.input:
        from .engine import run
        run(args.input, args.output, args.tolerance)
    else:
        from .server import serve
        serve(args.port, not args.no_browser, args.planner)


if __name__ == "__main__":
    main()
