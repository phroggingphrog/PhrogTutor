"""Run a code block (Python or R) in the bwrap sandbox: no network, time/memory limits."""

from __future__ import annotations

import base64
from pathlib import Path
import tempfile

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from deeptutor.services.sandbox.backends import BwrapBackend
from deeptutor.services.sandbox.spec import ExecRequest, Mount, ResourceLimits

router = APIRouter()

# Read-only numpy/matplotlib/pandas/scipy, installed once with pip --target (see TASK-89).
PYLIBS = Path.home() / ".local/share/pi-tutor-runcode/pylibs"

PY_PRELUDE = (
    "try:\n"
    "    import matplotlib, atexit\n"
    "    matplotlib.use('Agg')\n"
    "    import matplotlib.pyplot\n"
    "    def _save():\n"
    "        import matplotlib.pyplot as p\n"
    "        for i, n in enumerate(p.get_fignums()):\n"
    "            p.figure(n).savefig(f'/work/plot_{i:02d}.png', dpi=100)\n"
    "    atexit.register(_save)\n"
    "    matplotlib.pyplot.show = lambda *a, **k: None\n"  # figures are saved at exit instead
    "except ImportError:\n"
    "    pass\n"
)
R_PRELUDE = 'png("/work/plot_%02d.png", width=720, height=480)\n'


class RunCodeRequest(BaseModel):
    language: str
    code: str = Field(max_length=50_000)
    original: str = Field(default="", max_length=50_000)  # the tutor's code, to tell learner edits apart


SQL_RUNNER = (
    "import sqlite3\n"
    "con = sqlite3.connect(':memory:')\n"
    "src = open('/work/main.sql').read()\n"
    "buf = ''\n"
    "for line in src.splitlines(True):\n"
    "    buf += line\n"
    "    if sqlite3.complete_statement(buf):\n"
    "        cur = con.execute(buf); buf = ''\n"
    "        if cur.description:\n"
    "            cols = [d[0] for d in cur.description]; rows = cur.fetchall()\n"
    "            w = [max(len(str(x)) for x in [c, *[r[i] for r in rows]]) for i, c in enumerate(cols)]\n"
    "            print(' | '.join(c.ljust(w[i]) for i, c in enumerate(cols)))\n"
    "            print('-+-'.join('-' * n for n in w))\n"
    "            for r in rows: print(' | '.join(str(x).ljust(w[i]) for i, x in enumerate(r)))\n"
    "            print(f'({len(rows)} rows)\\n')\n"
)


@router.post("")
async def run_code(req: RunCodeRequest):
    lang = req.language.lower()
    if lang in ("python", "py", "python3"):
        name, prelude, cmd = "main.py", PY_PRELUDE, ["python3", "/work/main.py"]
    elif lang in ("r", "rscript"):
        name, prelude, cmd = "main.R", R_PRELUDE, ["Rscript", "--vanilla", "/work/main.R"]
    elif lang in ("sql", "sqlite"):  # in-memory SQLite; each SELECT prints a table
        name, prelude, cmd = "main.sql", "", ["python3", "-c", SQL_RUNNER]
    else:
        raise HTTPException(400, f"Run supports python, r and sql, not {req.language!r}")
    with tempfile.TemporaryDirectory(prefix="runcode-") as tmp:
        work = Path(tmp)
        (work / name).write_text(prelude + req.code, encoding="utf-8")
        argv = ["prlimit", "--as=3221225472", "--cpu=20", "--nproc=128", "--fsize=20000000", "--", *cmd]
        backend = BwrapBackend(inherit_virtualenv=False)
        result = await backend.exec(
            ExecRequest.of_argv(
                argv,
                workdir="/work",
                mounts=(Mount(str(work), "/work", read_only=False), Mount(str(PYLIBS), "/pylibs")),
                env={"HOME": "/work", "PYTHONPATH": "/pylibs", "MPLCONFIGDIR": "/work/.mpl", "MPLBACKEND": "Agg"},
                limits=ResourceLimits(timeout_s=25, max_output_chars=20_000),
            )
        )
        images = [
            "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()
            for p in sorted(work.glob("plot_*.png"))[:6]
            if p.stat().st_size > 0
        ]
    if req.original and req.code.strip() != req.original.strip():  # the learner changed it: mastery evidence
        from deeptutor.services import chat_mastery

        chat_mastery.record_code(lang, req.original, req.code, result.stdout, result.stderr, result.exit_code)
    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "error": result.error,
        "images": images,
    }
