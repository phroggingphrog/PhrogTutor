"""Notebook kernels: one long-lived sandboxed Python or R process per note, so cells share variables.

Same bwrap sandbox and limits as /api/run-code (no network, memory cap). A kernel is stopped after
15 minutes idle, on Restart, or when a cell runs longer than 60 s. At most 4 kernels run at once.
"""

from __future__ import annotations

import asyncio
import base64
import shutil
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from deeptutor.api.routers.run_code import PYLIBS
from deeptutor.services.sandbox.backends import BwrapBackend
from deeptutor.services.sandbox.spec import ExecRequest, Mount

router = APIRouter()

_IDLE_S, _CELL_S, _MAX = 15 * 60, 60, 4
_DONE = "\x1eDONE"

# Each cell: code lines on stdin, then a line "\x1eEND". The driver writes the cell's output to
# /work/out.txt and /work/err.txt, figures to /work/cNNN_*.png, then prints "\x1eDONE" on stdout.
PY_DRIVER = r'''
import ast, sys, traceback, contextlib
try:
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    plt.show = lambda *a, **k: None
except ImportError:
    plt = None
g = {"__name__": "__main__"}
n = 0
while True:
    lines = []
    for line in sys.stdin:
        if line.rstrip("\n") == "\x1eEND":
            break
        lines.append(line)
    else:
        break
    n += 1
    with open("/work/out.txt", "w") as out, open("/work/err.txt", "w") as err, \
         contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            tree = ast.parse("".join(lines), "<cell>")
            last = tree.body.pop() if tree.body and isinstance(tree.body[-1], ast.Expr) else None
            exec(compile(tree, "<cell>", "exec"), g)
            if last is not None:  # like Jupyter: show the value of a final expression
                value = eval(compile(ast.Expression(last.value), "<cell>", "eval"), g)
                if value is not None:
                    print(repr(value))
        except BaseException:
            etype, exc, tb = sys.exc_info()
            traceback.print_exception(etype, exc, tb.tb_next)  # hide the driver's own frame
        if plt is not None:
            for i, num in enumerate(plt.get_fignums()):
                plt.figure(num).savefig(f"/work/c{n:03d}_{i:02d}.png", dpi=100)
            plt.close("all")
    print("\x1eDONE", flush=True)
'''

R_DRIVER = r'''
con <- file("stdin"); open(con)
n <- 0
repeat {
  lines <- character(0)
  repeat {
    l <- readLines(con, n = 1)
    if (length(l) == 0) quit(save = "no")
    if (l == "\x1eEND") break
    lines <- c(lines, l)
  }
  n <- n + 1
  png(sprintf("/work/c%03d_%%02d.png", n), width = 720, height = 480)
  out <- file("/work/out.txt", "w"); err <- file("/work/err.txt", "w")
  sink(out); sink(err, type = "message")
  tryCatch({
    exprs <- parse(text = lines)
    for (e in exprs) {
      r <- withVisible(eval(e, globalenv()))
      if (r$visible) print(r$value)
    }
  }, error = function(e) message("Error: ", conditionMessage(e)))
  sink(type = "message"); sink(); close(out); close(err)
  invisible(dev.off())
  cat("\x1eDONE\n"); flush(stdout())
}
'''


class _Kernel:
    def __init__(self, proc: asyncio.subprocess.Process, work: Path) -> None:
        self.proc, self.work = proc, work
        self.lock = asyncio.Lock()
        self.used = time.monotonic()
        self.cells = 0

    def stop(self) -> None:
        if self.proc.returncode is None:
            self.proc.kill()
        shutil.rmtree(self.work, ignore_errors=True)


_KERNELS: dict[tuple[str, str], _Kernel] = {}


def _lang(language: str) -> str:
    lang = language.lower()
    if lang in ("python", "py", "python3"):
        return "python"
    if lang in ("r", "rscript"):
        return "r"
    raise HTTPException(400, f"Notebook kernels support python and r, not {language!r}")


def _reap() -> None:
    now = time.monotonic()
    for key, k in list(_KERNELS.items()):
        if k.proc.returncode is not None or now - k.used > _IDLE_S:
            k.stop()
            _KERNELS.pop(key, None)
    while len(_KERNELS) >= _MAX:  # drop the least recently used
        key = min(_KERNELS, key=lambda x: _KERNELS[x].used)
        _KERNELS.pop(key).stop()


async def _start(lang: str) -> _Kernel:
    work = Path(tempfile.mkdtemp(prefix="nbkernel-"))
    if lang == "python":
        (work / "driver.py").write_text(PY_DRIVER)
        cmd = ["python3", "-u", "/work/driver.py"]
    else:
        (work / "driver.R").write_text(R_DRIVER)
        cmd = ["Rscript", "--vanilla", "/work/driver.R"]
    argv = BwrapBackend(inherit_virtualenv=False)._build_argv(
        ExecRequest.of_argv(
            ["prlimit", "--as=3221225472", "--cpu=900", "--nproc=128", "--fsize=50000000", "--", *cmd],
            workdir="/work",
            mounts=(Mount(str(work), "/work", read_only=False), Mount(str(PYLIBS), "/pylibs")),
            env={"HOME": "/work", "PYTHONPATH": "/pylibs", "MPLCONFIGDIR": "/work/.mpl", "MPLBACKEND": "Agg"},
        )
    )
    proc = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    return _Kernel(proc, work)


class CellRequest(BaseModel):
    note: str = Field(max_length=300)
    language: str
    code: str = Field(max_length=50_000)


class KernelRef(BaseModel):
    note: str = Field(max_length=300)
    language: str


@router.post("/cell")
async def run_cell(req: CellRequest):
    lang = _lang(req.language)
    key = (req.note, lang)
    _reap()
    kernel = _KERNELS.get(key)
    if kernel is None:
        kernel = _KERNELS[key] = await _start(lang)
    async with kernel.lock:
        kernel.used = time.monotonic()
        kernel.cells += 1
        for old in kernel.work.glob("c*.png"):
            old.unlink()
        body = req.code.replace(_DONE, "") + "\n\x1eEND\n"
        try:
            kernel.proc.stdin.write(body.encode())
            await kernel.proc.stdin.drain()
            while True:
                line = await asyncio.wait_for(kernel.proc.stdout.readline(), timeout=_CELL_S)
                if not line:
                    raise ConnectionError
                if line.decode(errors="replace").startswith(_DONE):
                    break
        except asyncio.TimeoutError:
            _KERNELS.pop(key, None)
            kernel.stop()
            return {"stdout": "", "stderr": f"Cell ran longer than {_CELL_S} s; the kernel was restarted.",
                    "images": [], "restarted": True}
        except (ConnectionError, BrokenPipeError):
            _KERNELS.pop(key, None)
            kernel.stop()
            return {"stdout": "", "stderr": "The kernel stopped (out of memory or crashed); it was restarted.",
                    "images": [], "restarted": True}
        read = lambda name: (kernel.work / name).read_text(errors="replace")[-20_000:] if (kernel.work / name).exists() else ""
        images = [
            "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()
            for p in sorted(kernel.work.glob("c*.png"))[:6]
            if p.stat().st_size > 0
        ]
        return {"stdout": read("out.txt"), "stderr": read("err.txt"), "images": images,
                "restarted": False, "cell": kernel.cells}


@router.post("/restart")
async def restart(ref: KernelRef):
    kernel = _KERNELS.pop((ref.note, _lang(ref.language)), None)
    if kernel is not None:
        kernel.stop()
    return {"ok": True}
