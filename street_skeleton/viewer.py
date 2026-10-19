import json
from pathlib import Path


WEB = Path(__file__).parent / "web"


def payload(result):
    bounds = result.source.total_bounds
    origin = bounds[:2]

    def xy(point):
        return [round(point[0] - origin[0], 2), round(point[1] - origin[1], 2)]

    return {
        "stats": result.stats,
        "bounds": [0, 0, float(bounds[2] - origin[0]), float(bounds[3] - origin[1])],
        "source": [[xy(point) for point in line.coords] for line in result.source.geometry],
        "roads": [[xy(point) for point in line.coords] for line in result.roads.geometry],
        "intersections": [xy(row.geometry.coords[0]) + [int(row.node_id), int(row.degree)]
                          for row in result.nodes[result.nodes.degree >= 3].itertuples()],
        "review": [xy(point.coords[0]) for point in result.review.geometry],
    }


def write_viewer(result, path):
    html = (WEB / "index.html").read_text(encoding="utf-8")
    html = html.replace('<link rel="stylesheet" href="/style.css">',
                        "<style>" + (WEB / "style.css").read_text(encoding="utf-8") + "</style>")
    data = json.dumps(payload(result), ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    script = "window.PRELOADED=" + data + ";\n" + (WEB / "app.js").read_text(encoding="utf-8")
    html = html.replace('<script src="/app.js" defer></script>', "<script>" + script + "</script>")
    Path(path).write_text(html, encoding="utf-8")
