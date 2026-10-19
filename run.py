from street_skeleton.__main__ import main


if __name__ == "__main__":
    try:
        main()
    except ModuleNotFoundError as error:
        raise SystemExit(f"缺少依赖 {error.name}。请先运行：python -m pip install .") from None
