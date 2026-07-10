"""
tui.py — Rich TUI terminal dashboard for the Autonomous Agent (Google Forms).

Provides real-time visual feedback for form field detection, RAG mapping,
and per-record submission with screenshots.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich import box
from rich.columns import Columns
from rich.layout import Layout

console = Console()


class AgentTUI:
    """
    Live dashboard for the Google Forms agent.

    Updates in real-time as the agent:
      - Loads sheets data
      - Detects form fields
      - Maps fields to columns via Gemini
      - Fills and submits each record
    """

    def __init__(self) -> None:
        self._form_index = 0
        self._form_url = ""
        self._headless = True
        self._dry_run = False
        self._total_forms = 1
        self._total_records = 0
        self._detected_fields: list[str] = []
        self._mappings: list[dict[str, Any]] = []
        self._record_status: list[tuple[int, str]] = []  # (idx, status)
        self._errors: list[str] = []
        self._current_record = -1
        self._phase = "init"  # init | sheet | detect | map | fill | done
        self._phase_detail = ""
        self._start_time = time.time()
        self._live: Live | None = None

    # ------------------------------------------------------------------
    # Phase management
    # ------------------------------------------------------------------

    def _set_phase(self, phase: str, detail: str = "") -> None:
        self._phase = phase
        self._phase_detail = detail
        self._refresh()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(
        self,
        total_forms: int = 2,
        headless: bool = True,
        dry_run: bool = False,
    ) -> None:
        self._total_forms = total_forms
        self._headless = headless
        self._dry_run = dry_run
        self._start_time = time.time()
        self._live = Live(self._build_layout(), console=console, refresh_per_second=4, screen=False)
        self._live.__enter__()
        self._set_phase("init", "Inicializando agente...")

    def sheet_loaded(self, sheet_count: int, record_count: int) -> None:
        self._total_records = record_count
        self._set_phase("sheet", f"{sheet_count} hojas, {record_count} registros")

    def form_start(self, index: int, url: str) -> None:
        self._form_index = index
        self._form_url = url
        self._detected_fields = []
        self._mappings = []
        self._record_status = []
        self._errors = []
        self._set_phase("detect", f"Navegando a formulario {index + 1}...")

    def fields_detected(self, fields: list[str]) -> None:
        self._detected_fields = fields
        self._set_phase("detect", f"{len(fields)} campos detectados")

    def mapping_complete(self, mappings: list[dict[str, Any]]) -> None:
        self._mappings = mappings
        mapped = sum(1 for m in mappings if m.get("sheet_column"))
        total = len(mappings)
        self._set_phase("map", f"{mapped}/{total} campos mapeados")

    def record_start(self, idx: int, total: int) -> None:
        self._current_record = idx
        self._set_phase("fill", f"Registro {idx + 1}/{total}")

    def record_complete(self, idx: int, ok: bool, detail: str = "") -> None:
        status = "ok" if ok else "fail"
        self._record_status.append((idx, status))
        self._set_phase("fill", detail or f"Registro {idx + 1} {'enviado' if ok else 'fallido'}")

    def form_complete(self, sent: int, failed: int) -> None:
        self._set_phase("done", f"{sent} enviados, {failed} fallidos")

    def add_error(self, error: str) -> None:
        self._errors.append(error)

    def stop(self) -> None:
        if self._live and self._live.is_started:
            self._live.__exit__(None, None, None)
            self._live = None

    def show_final_report(self, report: dict[str, Any]) -> None:
        """Print final report in Rich format after Live is closed."""
        console.print()
        header = Panel(
            Text.assemble(
                ("  FADUA  ", "bold white on #2563eb"),
                (" Agente Autónomo  ", "bold"),
                ("  Reporte Final  ", "bold cyan"),
            ),
            box=box.HEAVY,
            border_style="blue",
        )
        console.print(header)

        elapsed = report.get("finished_at", datetime.now(timezone.utc).isoformat())
        start = report.get("started_at", "")
        if start and elapsed:
            try:
                s = datetime.fromisoformat(start)
                e = datetime.fromisoformat(elapsed)
                dur = (e - s).total_seconds()
            except Exception:
                dur = 0
        else:
            dur = time.time() - self._start_time

        t = Table.grid(padding=(1, 2))
        t.add_row(
            Text.assemble(("Formularios:  ", "bold"), (f"{report.get('forms_processed', 0)}", "cyan")),
            Text.assemble(("Registros:  ", "bold"), (f"{report.get('records_processed', 0)}", "green")),
        )
        t.add_row(
            Text.assemble(("Errores:  ", "bold"), (f"{len(report.get('errors', []))}", "red" if report.get('errors') else "green")),
            Text.assemble(("Duración:  ", "bold"), (f"{dur:.0f}s", "cyan")),
        )
        t.add_row(
            Text.assemble(("Headless:  ", "bold"), (f"{'Sí' if self._headless else 'No'}", "dim")),
            Text.assemble(("Dry run:  ", "bold"), (f"{'Sí' if self._dry_run else 'No'}", "dim")),
        )
        console.print(Panel(t, title=" Resumen ", box=box.SQUARE, border_style="bright_green"))

        for fr in report.get("form_results", []):
            panel_lines = []
            panel_lines.append(
                Text.assemble(("URL: ", "bold"), (f"{fr.get('form_url', '')[:70]}...", "dim"))
            )
            panel_lines.append(
                Text.assemble(("Campos detectados: ", "bold"), (f"{len(fr.get('mappings', []))}", "cyan"))
            )
            panel_lines.append(
                Text.assemble(("Enviados: ", "bold"), (f"{fr.get('records_sent', 0)}", "green"), ("  Fallidos: ", "bold"), (f"{fr.get('records_failed', 0)}", "red"))
            )

            # Mappings table
            mt = Table(box=box.SIMPLE, padding=(0, 1))
            mt.add_column("Campo Form", style="cyan")
            mt.add_column("→ Columna Sheet", style="green")
            mt.add_column("Confianza", style="yellow")
            for m in fr.get("mappings", []):
                if m.get("sheet_column"):
                    mt.add_row(
                        m.get("form_field", "?")[:30],
                        m.get("sheet_column", "—"),
                        f"{m.get('confidence', 0):.0%}",
                    )
            panel_lines.append(mt)

            console.print(Panel(Group(*panel_lines), title=f"  Form {fr['form_index'] + 1}  ", box=box.SQUARE, border_style="bright_blue"))

        console.print()
        console.print(Panel(
            Text("  Desarrollado por David Soler  ", style="dim"),
            box=box.SIMPLE,
            border_style="dim",
        ))

    # ------------------------------------------------------------------
    # Layout
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
        header_text.append(" Agente Autónomo  ", style="bold")
        header_text.append(f"  Form {self._form_index + 1}/{self._total_forms}  ", style="cyan")
        header_text.append(f"  {'◉ Visible' if not self._headless else '○ Headless'}  ", style="dim")
        if self._dry_run:
            header_text.append("  DRY RUN  ", style="bold yellow on red")
        layout["header"].update(
            Panel(header_text, box=box.HEAVY, border_style="blue")
        )

        # Body
        body_panels = []
        body_panels.append(self._phase_panel())
        if self._detected_fields:
            body_panels.append(self._fields_panel())
        if self._mappings:
            body_panels.append(self._mapping_panel())
        if self._record_status:
            body_panels.append(self._records_panel())
        if self._errors:
            body_panels.append(self._errors_panel())

        body = Group(*body_panels) if body_panels else Text("  Inicializando...", style="dim")
        layout["body"].update(body)

        # Footer
        elapsed = time.time() - self._start_time
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        footer_text = Text()
        footer_text.append(f"  {ts}  ", style="dim")
        footer_text.append(f"  ⏱ {elapsed:.0f}s  ", style="cyan")
        footer_text.append("  |  Desarrollado por David Soler  ", style="dim")
        layout["footer"].update(Panel(footer_text, box=box.SIMPLE, border_style="dim"))

        return layout

    def _phase_panel(self) -> Panel:
        icons = {
            "init": "⚙",
            "sheet": "📊",
            "detect": "🔍",
            "map": "🧠",
            "fill": "✏",
            "done": "✓",
        }
        icon = icons.get(self._phase, "?")
        text = Text.assemble(
            (f" {icon}  ", "bold"),
            (self._phase.upper(), "bold cyan"),
            (f"  —  {self._phase_detail}", ""),
        )
        return Panel(text, box=box.SQUARE, border_style="bright_blue")

    def _fields_panel(self) -> Panel:
        t = Table.grid(padding=(0, 2))
        for i, f in enumerate(self._detected_fields):
            icon = "✓" if any(m.get("form_field") == f and m.get("sheet_column") for m in self._mappings) else "○"
            color = "green" if icon == "✓" else "dim"
            t.add_row(Text(f"  {icon}  {f}", style=color))
        return Panel(t, title=f" Campos ({len(self._detected_fields)}) ", box=box.SQUARE, border_style="cyan")

    def _mapping_panel(self) -> Panel:
        t = Table(box=box.SIMPLE, padding=(0, 1))
        t.add_column("Campo Form", style="cyan", no_wrap=True)
        t.add_column("→ Columna", style="green", no_wrap=True)
        t.add_column("Conf.", style="yellow", justify="right")
        for m in self._mappings:
            if m.get("sheet_column"):
                t.add_row(
                    m.get("form_field", "?")[:28],
                    m.get("sheet_column", "—")[:20],
                    f"{m.get('confidence', 0):.0%}",
                )
        return Panel(t, title=" Mapeo RAG ", box=box.SQUARE, border_style="bright_green")

    def _records_panel(self) -> Panel:
        total = self._total_records
        done = len(self._record_status)
        ok_count = sum(1 for _, s in self._record_status if s == "ok")

        prog = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            console=console,
        )
        task = prog.add_task("Registros", total=total, completed=done)

        # Record status row
        status_chars = []
        for i in range(total):
            found = False
            for idx, st in self._record_status:
                if idx == i:
                    c = "✓" if st == "ok" else "✗"
                    status_chars.append(Text(c, style="green" if st == "ok" else "red"))
                    found = True
                    break
            if not found:
                if i == self._current_record:
                    status_chars.append(Text("◌", style="bold cyan"))
                else:
                    status_chars.append(Text("·", style="dim"))

        g = Group(
            prog,
            Text.assemble(
                ("  ", ""),
                *[ (c, "") for c in status_chars ],
            ),
        )
        summary = Text.assemble(
            (f"{ok_count}/{total} ", "bold green"),
            ("enviados", "dim"),
            (f"  ({done - ok_count} fallidos)", "red") if done > ok_count else Text(),
        )
        return Panel(Group(g, summary), title=" Registros ", box=box.SQUARE, border_style="bright_magenta")

    def _errors_panel(self) -> Panel:
        lines = [Text(f"  •  {e}", style="red") for e in self._errors[-5:]]
        return Panel(Group(*lines), title=" Errores ", box=box.SQUARE, border_style="red")

    def _refresh(self) -> None:
        if self._live and self._live.is_started:
            self._live.update(self._build_layout())
