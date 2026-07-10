"""
tui.py — Rich TUI terminal dashboard for the WooCommerce → Sheets pipeline.

Provides real-time visual feedback for each pipeline step using Rich Live display.
Shows product counts, timing, and status indicators in a clean panel layout.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich import box

console = Console()

_STATE = "idle"  # idle | running | done | error


class StepIndicator:
    """A single pipeline step with status and timing."""

    ICONS = {"pending": "○", "running": "◌", "ok": "✓", "fail": "✗", "skip": "—"}

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.status = "pending"
        self.detail = ""
        self.elapsed = 0.0

    @property
    def icon(self) -> str:
        return self.ICONS.get(self.status, "?")

    def render(self) -> Text:
        style_map = {"pending": "dim", "running": "bold cyan", "ok": "bold green", "fail": "bold red", "skip": "dim"}
        color = style_map.get(self.status, "dim")
        label = f"  {self.icon}  {self.name}"
        if self.description:
            label += f" ({self.description})"
        t = Text(label, style=color)
        if self.detail:
            t.append(f"  —  {self.detail}", style="italic cyan" if self.status == "ok" else "italic red")
        if self.elapsed:
            t.append(f"  {self.elapsed:.1f}s", style="dim")
        return t


class PipelineTUI:
    """
    Rich Live dashboard for the WooCommerce → Sheets pipeline.

    Usage:
        tui = PipelineTUI()
        tui.start_cycle(cycle_num=3)
        # ... run pipeline steps ...
        tui.step_ok("dlt", "5 products loaded")
        tui.step_ok("sheets", "5 rows written")
        tui.step_skip("email", "SMTP not configured")
        tui.end_cycle(result_dict)
    """

    def __init__(self) -> None:
        self._steps: dict[str, StepIndicator] = {}
        self._cycle_num = 0
        self._mode = "single"
        self._product_count = 0
        self._previous_count = 0
        self._errors: list[str] = []
        self._layout = Layout()
        self._live: Live | None = None
        self._countdown_active = False

    # ------------------------------------------------------------------
    # Step tracking
    # ------------------------------------------------------------------

    def _get(self, name: str) -> StepIndicator:
        if name not in self._steps:
            self._steps[name] = StepIndicator(name)
        return self._steps[name]

    def step_running(self, name: str, description: str = "") -> None:
        s = self._get(name)
        s.status = "running"
        s.description = description
        s.detail = ""
        s.start_time = time.time()
        self._refresh()

    def step_ok(self, name: str, detail: str = "") -> None:
        s = self._get(name)
        s.status = "ok"
        s.detail = detail
        s.elapsed = time.time() - getattr(s, "start_time", time.time())
        self._refresh()

    def step_fail(self, name: str, detail: str = "") -> None:
        s = self._get(name)
        s.status = "fail"
        s.detail = detail
        s.elapsed = time.time() - getattr(s, "start_time", time.time())
        self._errors.append(f"{name}: {detail}")
        self._refresh()

    def step_skip(self, name: str, detail: str = "") -> None:
        s = self._get(name)
        s.status = "skip"
        s.detail = detail
        self._refresh()

    def set_product_count(self, count: int, previous: int = 0) -> None:
        self._product_count = count
        self._previous_count = previous
        self._refresh()

    # ------------------------------------------------------------------
    # Layout rendering
    # ------------------------------------------------------------------

    def _build_layout(self) -> Layout:
        layout = Layout(size=console.height or 24)
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="footer", size=1),
        )

        # Header
        header_text = Text()
        header_text.append(" FADUA  ", style="bold white on #2563eb")
        header_text.append(" WooCommerce → Google Sheets  ", style="bold")
        header_text.append(f"  Ciclo #{self._cycle_num}  ", style="cyan")
        header_text.append(f"  Modo: {self._mode}  ", style="dim")
        layout["header"].update(
            Panel(header_text, box=box.HEAVY, border_style="blue")
        )

        # Body: step indicators + metrics
        body = Group(
            self._steps_panel(),
            Text(),
            self._metrics_panel(),
        )
        if self._errors:
            body.renderables.append(Text())
            body.renderables.append(self._errors_panel())
        layout["body"].update(Panel(body, box=box.ROUNDED, border_style="dim"))

        # Footer
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        footer = Text(f"  {ts}  |  Desarrollado por David Soler  ", style="dim")
        layout["footer"].update(Panel(footer, box=box.SIMPLE, border_style="dim"))

        return layout

    def _steps_panel(self) -> Panel:
        t = Table.grid(padding=(0, 1))
        for name in ["dlt", "sheets", "email"]:
            s = self._get(name)
            t.add_row(s.render())
        title = Text(" Pipeline Steps ", style="bold")
        return Panel(t, title=title, box=box.SQUARE, border_style="bright_blue")

    def _metrics_panel(self) -> Panel:
        diff = self._product_count - self._previous_count
        diff_text = Text()
        if diff > 0:
            diff_text.append(f"+{diff}", style="green")
        elif diff < 0:
            diff_text.append(f"{diff}", style="red")
        else:
            diff_text.append("sin cambios", style="dim")

        t = Table.grid(padding=(1, 2))
        t.add_row(
            Text.assemble(("Productos:  ", "bold"), (f"{self._product_count}", "cyan")),
            Text.assemble(("vs anterior:  ", "bold"), diff_text),
        )
        t.add_row(
            Text.assemble(("Errores:  ", "bold"), (f"{len(self._errors)}", "red" if self._errors else "green")),
            Text.assemble(("Estado:  ", "bold"), (f"{_STATE.upper()}", "bold green" if _STATE == "done" else "bold cyan" if _STATE == "running" else "dim")),
        )
        return Panel(t, title=" Metrics ", box=box.SQUARE, border_style="bright_green")

    def _errors_panel(self) -> Panel:
        lines = [Text(f"  •  {e}", style="red") for e in self._errors]
        return Panel(Group(*lines), title=" Errors ", box=box.SQUARE, border_style="red")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        if self._live and self._live.is_started:
            self._live.update(self._build_layout())

    def start_cycle(self, cycle_num: int = 1, mode: str = "single") -> None:
        global _STATE
        _STATE = "running"
        self._cycle_num = cycle_num
        self._mode = mode
        self._steps = {}
        self._errors = []
        self._product_count = 0
        self._previous_count = 0

        self._live = Live(self._build_layout(), console=console, refresh_per_second=4, screen=False)
        self._live.__enter__()

    def end_cycle(self, result: dict[str, Any] | None = None) -> None:
        global _STATE
        if result:
            _STATE = "done" if result.get("status") != "degraded" else "error"
            self._product_count = result.get("products_count", self._product_count)
            self._previous_count = result.get("previous_count", self._previous_count)
        else:
            _STATE = "done"
        self._refresh()
        if self._live:
            self._live.__exit__(None, None, None)
            self._live = None

    def countdown(self, seconds: int) -> None:
        """Display a countdown timer on screen (daemon mode)."""
        if self._live and self._live.is_started:
            self._live.stop()
        self._countdown_active = True
        while seconds > 0 and self._countdown_active:
            m, s = divmod(seconds, 60)
            console.clear()
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            panel = Panel(
                Text.assemble(
                    ("\n\n  ◌  Próximo ciclo en  ", "bold cyan"),
                    (f"{m:02d}:{s:02d}", "bold white on blue"),
                    ("  ◌  \n\n", "bold cyan"),
                    (f"  Última ejecución: Ciclo #{self._cycle_num}  ", "dim"),
                    (f"\n  {ts}  ", "dim"),
                    ("\n  Desarrollado por David Soler  ", "dim"),
                ),
                box=box.DOUBLE,
                border_style="bright_blue",
                width=50,
            )
            console.print(panel, justify="center")
            time.sleep(1)
            seconds -= 1
        self._countdown_active = False

    def cancel_countdown(self) -> None:
        self._countdown_active = False
