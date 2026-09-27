"""Demo projects: real benchlog projects with history, branches and PRs, to browse in the UI.

`benchlog demo seed` builds them in a projects folder (default ~/benchlog-projects). They're real
git repos made with the same code as everything else, so every screen works on them: timeline,
diff, checks, branches, PRs. Only the project you build on the bench is live; these are to browse.

    pot-led          the demo circuit; branch move-sensor has an open PR that fails its checks
                     (the pot moved onto GPIO12, a strapping pin)
    weather-station  ESP32 + BME280 over I2C; the wiring came in through a PR, tested and merged
    led-bar          three LEDs; branch third-led has an open PR that passes and is ready to merge
"""

import json
import os
import shutil
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from benchlog.core import branches, prs
from benchlog.core.models import Circuit, Component, ComponentType, Wire
from benchlog.core.parts import esp32_devkit_v1_30_pins
from benchlog.core.project import STATE_DIR, Project, ProjectError
from benchlog.core.repo import Repo

DEFAULT_WORKSPACE = Path.home() / "benchlog-projects"
MARKER = STATE_DIR / "demo.json"  # marks a folder as a generated demo project (safe to replace)
AUTHOR = ("benchlog demo", "demo@benchlog.local")


# ── Circuits ──────────────────────────────────────────────────────────────────


def _esp32() -> Component:
    return Component(
        id="esp32", type=ComponentType.ESP32_DEVKIT_V1_30, model="DOIT ESP32 DevKit V1", pins=esp32_devkit_v1_30_pins(1)
    )


def _power() -> list[Wire]:
    return [
        Wire(id="w1", a="J15", b="R+15", color="red", label="3V3 to + rail"),
        Wire(id="w2", a="J14", b="R-14", color="black", label="GND to - rail"),
    ]


def _circuit(components: list[Component], wires: list[Wire]) -> Circuit:
    return Circuit(components=components, wires=wires)


def _led(n: int, gpio_row: int, top: int, color: str) -> tuple[list[Component], list[Wire]]:
    """An LED on the GPIO in `gpio_row` (left side), through a 220Ω resistor, to the - rail."""
    w_signal, w_ground = 2 * n + 1, 2 * n + 2
    return (
        [
            Component(id=f"r{n}", type=ComponentType.RESISTOR, value="220Ω", pins={"1": f"C{top}", "2": f"C{top + 4}"}),
            Component(id=f"led{n}", type=ComponentType.LED, value=color, pins={"anode": f"E{top + 4}", "cathode": f"F{top + 4}"}),
        ],
        [
            Wire(id=f"w{w_signal}", a=f"A{gpio_row}", b=f"A{top}", color="yellow", label=f"LED {n} signal"),
            Wire(id=f"w{w_ground}", a=f"J{top + 4}", b=f"R-{top + 4}", color="black", label=f"LED {n} ground"),
        ],
    )


# ── Projects ──────────────────────────────────────────────────────────────────


@dataclass
class DemoProject:
    folder: str
    name: str
    description: str
    build: Callable[["_Builder"], None]


class _Builder:
    """Makes commits, branches and PRs with plausible dates, oldest first."""

    def __init__(self, project: Project, days: int) -> None:
        self.project = project
        self.when = datetime.now(timezone.utc) - timedelta(days=days)

    @contextmanager
    def _dated(self) -> Iterator[None]:
        self.when += timedelta(hours=21, minutes=13)
        stamp = self.when.isoformat(timespec="seconds")
        env = {
            "GIT_AUTHOR_NAME": AUTHOR[0], "GIT_AUTHOR_EMAIL": AUTHOR[1],
            "GIT_COMMITTER_NAME": AUTHOR[0], "GIT_COMMITTER_EMAIL": AUTHOR[1],
            "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp,
        }  # fmt: skip
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            yield
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def commit(self, message: str, circuit: Circuit) -> None:
        self.project.save_circuit(circuit)
        root = self.project.repo.root
        # The first commit also carries the README and .gitignore, so the repo starts clean.
        extra = [p for p in (root / "README.md", root / ".gitignore") if p.exists()] if self.project.repo.head() is None else []
        with self._dated():
            self.project.commit(message, firmware=extra)

    def branch(self, name: str) -> None:
        branches.create_branch(self.project, name)
        branches.checkout(self.project, name)

    def checkout(self, name: str) -> None:
        branches.checkout(self.project, name)

    def pr(self, branch: str, title: str, description: str) -> int:
        with self._dated():
            return prs.create(self.project, branch, title=title, description=description).id


def _pot_led(b: _Builder) -> None:
    base = [_esp32()]
    pot = Component(id="pot1", type=ComponentType.POTENTIOMETER, value="10kΩ", pins={"1": "H20", "wiper": "H21", "3": "H22"})
    pot_wires = [
        Wire(id="w3", a="J20", b="R+20", color="red", label="pot high"),
        Wire(id="w4", a="J22", b="R-22", color="black", label="pot low"),
        Wire(id="w5", a="F21", b="A4", color="yellow", label="pot signal"),
    ]
    led = [
        Component(id="r1", type=ComponentType.RESISTOR, value="220Ω", pins={"1": "A14", "2": "A18"}),
        Component(id="led1", type=ComponentType.LED, value="red", pins={"anode": "E18", "cathode": "F18"}),
    ]
    led_wire = [Wire(id="w6", a="J18", b="R-18", color="black", label="LED ground")]
    b.commit("Place ESP32 and power rails", _circuit(base, _power()))
    b.commit("Add 10k pot on GPIO34", _circuit(base + [pot], _power() + pot_wires))
    working = _circuit(base + [pot] + led, _power() + pot_wires + led_wire)
    b.commit("Add status LED on GPIO13", working)
    b.branch("move-sensor")
    moved = [w.model_copy(update={"b": "A12"}) if w.id == "w5" else w for w in working.wires]
    b.commit("Move sensor to GPIO12", working.model_copy(update={"wires": moved}))
    b.pr(
        "move-sensor",
        "Move sensor to GPIO12",
        "Frees GPIO34 for a second sensor. GPIO12 is a strapping pin though; see the checks.",
    )
    b.checkout("main")


def _weather_station(b: _Builder) -> None:
    bme = Component(
        id="bme1", type=ComponentType.I2C_MODULE, model="BME280", value="0x76",
        pins={"VCC": "J30", "GND": "J31", "SCL": "J32", "SDA": "J33"},
    )  # fmt: skip
    bme_power = [
        Wire(id="w3", a="G30", b="R+30", color="red", label="BME280 power"),
        Wire(id="w4", a="G31", b="R-31", color="black", label="BME280 ground"),
    ]
    i2c = [
        Wire(id="w5", a="G32", b="J2", color="blue", label="SCL to GPIO22"),
        Wire(id="w6", a="G33", b="J5", color="green", label="SDA to GPIO21"),
    ]
    b.commit("Place ESP32 and power rails", _circuit([_esp32()], _power()))
    b.commit("Add BME280 breakout", _circuit([_esp32(), bme], _power() + bme_power))
    b.branch("i2c-wiring")
    b.commit("Wire BME280 to I2C (GPIO21/22)", _circuit([_esp32(), bme], _power() + bme_power + i2c))
    pr_id = b.pr("i2c-wiring", "Connect the BME280 over I2C", "SDA on GPIO21, SCL on GPIO22, powered from 3V3.")
    with b._dated():
        prs.mark_tested(b.project, pr_id, "Reads 21.4 °C / 48% RH on the serial monitor")
        prs.merge(b.project, pr_id)


def _led_bar(b: _Builder) -> None:
    parts, wires = [_esp32()], _power()
    b.commit("Place ESP32 and power rails", _circuit(parts, wires))
    for n, (gpio_row, top, color) in enumerate([(8, 20, "red"), (9, 26, "yellow")], start=1):
        more_parts, more_wires = _led(n, gpio_row, top, color)
        parts, wires = parts + more_parts, wires + more_wires
        b.commit(f"LED {n} ({color}) on GPIO{25 + n - 1}", _circuit(parts, wires))
    b.branch("third-led")
    more_parts, more_wires = _led(3, 10, 32, "green")
    b.commit("LED 3 (green) on GPIO27", _circuit(parts + more_parts, wires + more_wires))
    b.pr("third-led", "Add a third LED", "Green LED on GPIO27 for 'upload done'.")
    b.checkout("main")


DEMOS = [
    DemoProject("pot-led", "Pot + LED", "ESP32 reading a potentiometer and driving a status LED.", _pot_led),
    DemoProject("weather-station", "Weather station", "ESP32 with a BME280 temperature/humidity sensor over I2C.", _weather_station),
    DemoProject("led-bar", "LED bar", "Three LEDs on GPIO25-27 through 220Ω resistors.", _led_bar),
]


# ── Seeding ───────────────────────────────────────────────────────────────────


def is_demo(path: Path) -> bool:
    return (path / MARKER).exists()


def seed(workspace: Path = DEFAULT_WORKSPACE, reset: bool = False) -> list[tuple[DemoProject, Path, str]]:
    """Create the demo projects in `workspace`. Returns (demo, path, what happened) for each.

    Existing folders are left alone, except generated demo projects when `reset` is set.
    """
    workspace.mkdir(parents=True, exist_ok=True)
    results = []
    for demo in DEMOS:
        path = workspace / demo.folder
        if path.exists():
            if not (reset and is_demo(path)):
                why = "exists (use --reset to rebuild)" if is_demo(path) else "exists and isn't a demo project, left alone"
                results.append((demo, path, why))
                continue
            shutil.rmtree(path)
        _create(demo, path)
        results.append((demo, path, "created"))
    return results


def _create(demo: DemoProject, path: Path) -> None:
    Repo.init(path).run("checkout", "--quiet", "-b", "main")  # whatever the machine's default branch is
    project, _ = Project.init(path)
    readme = path / "README.md"
    readme.write_text(f"# {demo.name}\n\n{demo.description}\n\n_Demo project generated by `benchlog demo seed`._\n")
    builder = _Builder(project, days=6)
    try:
        demo.build(builder)
    except ProjectError as e:
        raise ProjectError(f"building demo project {demo.folder}: {e}") from e
    (path / MARKER).write_text(json.dumps({"demo": demo.folder, "name": demo.name}) + "\n")
