#!/usr/bin/env python3
"""FACTS desktop dashboard.

A native Ubuntu/WSLg GUI for configuring and launching a local SSiSLS FACTS run.

Select the options, press Run, and the dashboard writes the experiment
config.yml, installs the location list, and drives run_facts_slr.sh in the
background while streaming progress back into the window.

The actual run logic is NOT reimplemented here. This wraps run_facts_slr.sh so
the preflight checks, task accounting and NetCDF copy-out stay in one place.

Usage:
    ./facts_dashboard.py

Requires: python3-tk (sudo apt install -y python3-tk)
"""

import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, scrolledtext, ttk

# --------------------------------------------------------------------------
# Paths. Overridable by environment, same variable names as run_facts_slr.sh.
# --------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
FACTS_DIR = os.environ.get("FACTS_DIR", HERE)
FACTS_REPO = os.environ.get("FACTS_REPO", os.path.expanduser("~/facts_ssisls/facts"))
RUNNER = os.path.join(FACTS_DIR, "run_facts_slr.sh")
SETUP_SCRIPT = os.path.join(FACTS_DIR, "setup_facts.sh")
LOCATION_SRC = os.path.join(FACTS_DIR, "configs", "location_48gauges.lst")

# Where a missing experiment definition is fetched from, in this order:
# the local clone shipped alongside this project, then the FACTS source itself.
LOCAL_CLONE = os.path.join(FACTS_DIR, "facts", "experiments_ssisls")
UPSTREAM = ("https://raw.githubusercontent.com/pkjr002/facts/"
            "demo/ssisls/experiments_ssisls")


# Remembers the last-used output directory so it does not have to be repicked.
SETTINGS = os.path.join(os.path.expanduser("~"), ".facts_dashboard.json")
DEFAULT_OUTPUT = os.path.join(FACTS_DIR, "outputs")


def load_settings():
    try:
        with open(SETTINGS, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data):
    try:
        with open(SETTINGS, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except OSError:
        pass  # a non-writable home should never block a run


def exp_dir_for(scenario):
    """Experiment directory inside the FACTS repo for one scenario."""
    return os.path.join(FACTS_REPO, "experiments_ssisls", scenario)


def is_installed(scenario):
    """True when the experiment definition already exists in the repo."""
    return os.path.isdir(exp_dir_for(scenario))


def provision(scenario):
    """Fetch a missing experiment definition into the FACTS repo.

    Prefers the local clone under FACTS/facts; falls back to the upstream
    branch. Returns a list of (filename, source) describing what was fetched.
    Raises OSError or urllib.error.URLError on failure.
    """
    dest = exp_dir_for(scenario)
    os.makedirs(dest, exist_ok=True)
    fetched = []
    for name in ("config.yml", "location.lst"):
        target = os.path.join(dest, name)
        local = os.path.join(LOCAL_CLONE, scenario, name)
        if os.path.isfile(local):
            with open(local, "rb") as fh:
                data = fh.read()
            source = "local clone"
        else:
            url = "%s/%s/%s" % (UPSTREAM, scenario, name)
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = resp.read()
            source = "upstream"
        # The repo carries CRLF throughout; these files are read inside Linux.
        with open(target, "wb") as fh:
            fh.write(data.replace(b"\r\n", b"\n"))
        fetched.append((name, source))
    return fetched

# --------------------------------------------------------------------------
# Experiment definitions. These mirror experiments_ssisls/<ssp>/config.yml.
# Only global-options.scenario and the lws module's scenario vary per SSP.
# --------------------------------------------------------------------------

ALL_WF = ["wf1e", "wf1f", "wf2e", "wf2f", "wf3e", "wf3f", "wf4"]

SCENARIOS = [
    # id, lws scenario, installed in FACTS_REPO
    ("ssp119", "ssp1", False),
    ("ssp126", "ssp1", True),
    ("ssp245", "ssp2", True),
    ("ssp370", "ssp3", False),
    ("ssp585", "ssp5", True),
]

WORKFLOWS = {
    "wf1e": ("emulandice", "emulandice", "emulandice", 2100),
    "wf1f": ("AR5 (ipccar5)", "FittedISMIP", "ipccar5", 2150),
    "wf2e": ("LARMIP-2", "emulandice", "emulandice", 2100),
    "wf2f": ("LARMIP-2", "FittedISMIP", "ipccar5", 2150),
    "wf3e": ("DeConto21", "emulandice", "emulandice", 2100),
    "wf3f": ("DeConto21", "FittedISMIP", "ipccar5", 2150),
    "wf4": ("Bamber19 SEJ", "Bamber19 SEJ", "ipccar5", 2150),
}

# key, module_set, module, workflows, extra options, flags
MODULES = [
    ("GrIS1f", "FittedISMIP", "GrIS", ["wf1f", "wf2f", "wf3f"], [], {}),
    ("deconto21", "deconto21", "AIS", ["wf3e", "wf3f"], [], {}),
    ("bamber19", "bamber19", "icesheets", ["wf4"], [], {}),
    ("emuAIS", "emulandice", "AIS", ["wf1e"], [("pyear_end", 2100)], {}),
    ("emuGrIS", "emulandice", "GrIS", ["wf1e", "wf2e", "wf3e"], [("pyear_end", 2100)], {}),
    ("emuglaciers", "emulandice", "glaciers", ["wf1e", "wf2e", "wf3e"], [("pyear_end", 2100)], {}),
    ("larmip", "larmip", "AIS", ["wf2e", "wf2f"], [], {}),
    ("ar5glaciers", "ipccar5", "glaciers", ["wf1f", "wf2f", "wf3f", "wf4"], [("gmip", 2)], {}),
    ("ar5AIS", "ipccar5", "icesheets", ["wf1f"], [], {"ais_pipeline": True}),
    ("ocean", "tlm", "sterodynamics", list(ALL_WF), [], {}),
    ("k14vlm", "kopp14", "verticallandmotion", list(ALL_WF), [], {"optional": "vlm"}),
    ("lws", "ssp", "landwaterstorage", list(ALL_WF), [], {"lws": True}),
]

PAGE = "#F3F6F8"
SURFACE = "#FFFFFF"
INK = "#16252D"
MUTED = "#60717A"
BORDER = "#D9E2E7"
NAVY = "#0B2B3C"
NAVY_2 = "#123E51"
ACCENT = "#087F73"
ACCENT_HOVER = "#066A61"
ACCENT_SOFT = "#DDF3EF"
SIGNAL = "#B44D20"
SIGNAL_SOFT = "#FCE9E1"
OKCOL = "#177554"
TERMINAL = "#0C2029"
TERMINAL_TEXT = "#DCE9ED"

# Coastal regions for the 48-gauge set, assigned explicitly rather than by a
# lat/lon rule: around Florida a simple threshold puts Gulf-coast gauges on the
# Atlantic side and vice versa. Key West sits at the Atlantic/Gulf boundary and
# is grouped with the Gulf here; move it if your analysis treats it otherwise.
REGIONS = [
    ("Northeast", [
        "8410140", "8413320", "8418150", "8443970", "8447930",
        "8449130", "8461490", "8467150", "8510560"]),
    ("Mid-Atlantic", [
        "8516945", "8518750", "8531680", "8534720", "8536110", "8551910",
        "8557380", "8570283", "8574680", "8635750", "8638901"]),
    ("Southeast Atlantic", [
        "8651370", "8656483", "8658163", "8661070", "8665530", "8670870",
        "8720030", "8720218", "8721604", "8722670", "8723214", "8723970"]),
    ("Gulf", [
        "8724580", "8725110", "8726520", "8727520", "8729108", "8729840",
        "8735180", "8747437", "8761305", "8761724", "8768094", "8770822",
        "8772471", "8774770", "8775870", "8779770"]),
]


def load_sites(path=None):
    """Read the master location list. Returns [(name, id, lat, lon), ...]."""
    path = path or LOCATION_SRC
    sites = []
    if not os.path.isfile(path):
        return sites
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 4:
                continue
            name, sid, lat, lon = parts
            try:
                sites.append((name, sid.strip(), float(lat), float(lon)))
            except ValueError:
                continue
    return sites


# Finer grouping. Same 48 gauges, split by state instead of by coastal region.
STATES = [
    ("ME", ["8410140", "8413320", "8418150"]),
    ("MA", ["8443970", "8447930", "8449130"]),
    ("CT", ["8461490", "8467150"]),
    ("NY", ["8510560", "8516945", "8518750"]),
    ("NJ", ["8531680", "8534720", "8536110"]),
    ("DE", ["8551910", "8557380"]),
    ("MD", ["8570283", "8574680"]),
    ("VA", ["8635750", "8638901"]),
    ("NC", ["8651370", "8656483", "8658163"]),
    ("SC", ["8661070", "8665530"]),
    ("GA", ["8670870"]),
    ("FL", ["8720030", "8720218", "8721604", "8722670", "8723214", "8723970",
            "8724580", "8725110", "8726520", "8727520", "8729108", "8729840"]),
    ("AL", ["8735180"]),
    ("MS", ["8747437"]),
    ("LA", ["8761305", "8761724", "8768094"]),
    ("TX", ["8770822", "8772471", "8774770", "8775870", "8779770"]),
]

GROUPINGS = {"Coastal region": REGIONS, "State": STATES}


def region_of(site_id):
    for label, ids in REGIONS:
        if site_id in ids:
            return label
    return "Other"


# --------------------------------------------------------------------------
# Config generation
# --------------------------------------------------------------------------


def build_config(scenario, lws_scen, local, selected_wf, use_vlm, use_esl,
                 esl_years, nsamps, baseyear, pstart, pend, pstep):
    """Return the full experiment config.yml as a string."""
    out = []
    a = out.append

    mode = "LOCAL (per-site)" if local else "GLOBAL mean"
    a("# Generated by facts_dashboard.py: %s run" % mode)
    a("# %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    a("")
    a("global-options:")
    a("    nsamps: %d" % nsamps)
    a("    scenario: %s" % scenario)
    a("    pyear_start: %d" % pstart)
    a("    pyear_end: %d" % pend)
    a("    pyear_step: %d" % pstep)
    a("    baseyear: %d" % baseyear)
    a('    pipeline_file: "%s"' % ("pipeline.yml" if local else "pipeline.global.yml"))
    a("")
    a("climate_step:")
    a("    temperature:")
    a('        module_set: "fair"')
    a('        module: "temperature"')
    a("        generates_climate_output: true")
    a("")
    a("sealevel_step:")

    for key, mset, mod, wfs, opts, flags in MODULES:
        if flags.get("optional") == "vlm" and (not use_vlm or not local):
            continue
        wfs_kept = [w for w in wfs if w in selected_wf]
        if not wfs_kept:
            continue

        a("    %s:" % key)
        a('        module_set: "%s"' % mset)
        a('        module: "%s"' % mod)
        if flags.get("ais_pipeline"):
            a('        pipeline_file: "%s"'
              % ("pipeline.AIS.yml" if local else "pipeline.AIS.global.yml"))

        these_opts = list(opts)
        if flags.get("lws"):
            these_opts = [("scenario", '"%s"' % lws_scen), ("dcrate_lo", -0.4)]
        if these_opts:
            a("        options:")
            for name, val in these_opts:
                a("            %s: %s" % (name, val))

        a("        include_in_workflow:")
        for w in wfs_kept:
            a('            - "%s"' % w)
        a("")

    a("totaling_step:")
    a("    total:")
    a('        module_set: "facts"')
    a('        module: "total"')
    a("        loop_over_workflows: true")
    a("        loop_over_scales: true")
    a("        stages:")
    a("            - workflow")

    if use_esl and local:
        a("")
        a("esl_step:")
        a("    extremesealevel:")
        a("        loop_over_workflows: true")
        a('        module_set: "extremesealevel"')
        a('        module: "pointsoverthreshold"')
        a("        options:")
        a("            target_years: %s" % (esl_years or "2050,2100"))
        a('            total_localsl_file: "$SHARED/totaled/'
          '%EXPERIMENT_NAME%.total.workflow.%WORKFLOW_NAME%.local.nc"')

    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------


class Dashboard(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("FACTS Projection Studio")
        self.geometry("1280x840")
        self.minsize(1040, 680)
        self.configure(background=PAGE)

        self.msgq = queue.Queue()
        self.proc = None
        self.worker = None
        self.running = False
        self.setup_proc = None
        self.setup_running = False
        self.container_prefix = ""
        self.total_tasks = 68
        self.done_tasks = 0

        self._build_state()
        self._build_style()
        self._build_ui()
        self._refresh_summary()
        self.after(120, self._pump)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- state ------------------------------------------------------------

    def _build_state(self):
        self.v_scenario = tk.StringVar(value="ssp126")
        self.v_local = tk.BooleanVar(value=True)
        self.v_wf = {w: tk.BooleanVar(value=True) for w in ALL_WF}
        self.v_vlm = tk.BooleanVar(value=True)
        self.v_esl = tk.BooleanVar(value=False)
        self.v_eslyears = tk.StringVar(value="2050,2100")
        self.v_nsamps = tk.StringVar(value="2000")
        self.v_baseyear = tk.StringVar(value="2005")
        self.v_pstart = tk.StringVar(value="2020")
        self.v_pend = tk.StringVar(value="2150")
        self.v_pstep = tk.StringVar(value="10")
        self.v_cpu = tk.StringVar(value="8")
        self.v_mem = tk.StringVar(value="12g")
        self.settings = load_settings()
        self.v_outdir = tk.StringVar(
            value=self.settings.get("output_root", DEFAULT_OUTPUT))
        self.v_outpath = tk.StringVar(value="")
        self.sites = load_sites()
        self.site_path = LOCATION_SRC
        self.selected_ids = set(s[1] for s in self.sites)
        self.v_grouping = tk.StringVar(value="Coastal region")
        self.v_sitecount = tk.StringVar(value="")
        self.v_status = tk.StringVar(value="Idle")
        self.v_progress = tk.DoubleVar(value=0.0)
        self.v_summary = tk.StringVar(value="")
        self.v_warn = tk.StringVar(value="")
        self.v_setup_status = tk.StringVar(value="Checking installation…")
        self.v_setup_data = tk.BooleanVar(value=False)
        self.v_setup_rebuild = tk.BooleanVar(value=False)

        for var in (self.v_scenario, self.v_local, self.v_vlm, self.v_esl,
                    self.v_nsamps, self.v_pend, self.v_eslyears):
            var.trace_add("write", lambda *_: self._refresh_summary())
        for var in self.v_wf.values():
            var.trace_add("write", lambda *_: self._refresh_summary())

    def _build_style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", font=("DejaVu Sans", 10), foreground=INK,
                     background=SURFACE)
        st.configure("TFrame", background=SURFACE)
        st.configure("Page.TFrame", background=PAGE)
        st.configure("Header.TFrame", background=NAVY)
        st.configure("Footer.TFrame", background=SURFACE)
        st.configure("TLabel", background=SURFACE, foreground=INK)
        st.configure("Eyebrow.TLabel", background=NAVY, foreground="#86D5CB",
                     font=("DejaVu Sans", 9, "bold"))
        st.configure("Head.TLabel", background=NAVY, foreground="#FFFFFF",
                     font=("DejaVu Sans", 20, "bold"))
        st.configure("HeadSub.TLabel", background=NAVY, foreground="#BFD1D9",
                     font=("DejaVu Sans", 9))
        st.configure("HeadMeta.TLabel", background=NAVY_2, foreground="#E8F3F5",
                     font=("DejaVu Sans", 9, "bold"), padding=(12, 7))
        st.configure("Sub.TLabel", foreground=MUTED, background=SURFACE,
                     font=("DejaVu Sans", 9))
        st.configure("Mono.TLabel", font=("DejaVu Sans Mono", 9),
                     foreground="#405660", background=SURFACE)
        st.configure("PanelTitle.TLabel", font=("DejaVu Sans", 11, "bold"),
                     foreground=NAVY, background=SURFACE)
        st.configure("Sect.TLabelframe", background=SURFACE, bordercolor=BORDER,
                     lightcolor=BORDER, darkcolor=BORDER, relief="solid",
                     borderwidth=1)
        st.configure("Sect.TLabelframe.Label", font=("DejaVu Sans", 10, "bold"),
                     foreground=NAVY, background=SURFACE, padding=(2, 2))
        st.configure("TButton", font=("DejaVu Sans", 9, "bold"), padding=(10, 7),
                     background="#EAF0F2", foreground=INK, borderwidth=0)
        st.map("TButton", background=[("active", "#DCE6EA")],
               foreground=[("disabled", "#99A6AC")])
        st.configure("Run.TButton", font=("DejaVu Sans", 11, "bold"),
                     padding=(22, 11), background=ACCENT, foreground="#FFFFFF",
                     borderwidth=0)
        st.map("Run.TButton", background=[("active", ACCENT_HOVER),
                                           ("disabled", "#9BBDB8")])
        st.configure("Stop.TButton", font=("DejaVu Sans", 10, "bold"),
                     padding=(16, 10), background=SIGNAL_SOFT, foreground=SIGNAL,
                     borderwidth=0)
        st.map("Stop.TButton", background=[("active", "#F8D7CA")])
        st.configure("TCheckbutton", background=SURFACE, foreground=INK,
                     padding=(1, 3))
        st.configure("TRadiobutton", background=SURFACE, foreground=INK,
                     padding=(1, 3))
        st.map("TCheckbutton", background=[("active", SURFACE)])
        st.map("TRadiobutton", background=[("active", SURFACE)])
        st.configure("TEntry", fieldbackground="#FBFCFD", foreground=INK,
                     bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                     padding=6)
        st.configure("TCombobox", fieldbackground="#FBFCFD", foreground=INK,
                     bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                     padding=5)
        st.configure("Vertical.TScrollbar", background="#D7E1E5",
                     troughcolor=PAGE, borderwidth=0, arrowsize=12)
        st.configure("Bar.Horizontal.TProgressbar", background=ACCENT,
                     troughcolor="#DDE7EA", borderwidth=0, thickness=12)
        st.configure("TNotebook", background=PAGE, borderwidth=0, tabmargins=(18, 10, 0, 0))
        st.configure("TNotebook.Tab", font=("DejaVu Sans", 10, "bold"),
                     padding=(18, 9), background="#DDE7EA", foreground=MUTED,
                     borderwidth=0)
        st.map("TNotebook.Tab", background=[("selected", SURFACE),
                                             ("active", "#E8EFF2")],
               foreground=[("selected", NAVY), ("active", INK)])
        st.configure("Setup.Treeview", background=SURFACE, fieldbackground=SURFACE,
                     foreground=INK, rowheight=30, borderwidth=0)
        st.configure("Setup.Treeview.Heading", background="#EAF0F2",
                     foreground=NAVY, font=("DejaVu Sans", 9, "bold"),
                     relief="flat", padding=(6, 7))
        st.map("Setup.Treeview", background=[("selected", ACCENT_SOFT)],
               foreground=[("selected", INK)])

    # -- layout -----------------------------------------------------------

    def _build_ui(self):
        root = ttk.Frame(self, style="Page.TFrame")
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=0, minsize=410)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(1, weight=1)

        head = ttk.Frame(root, style="Header.TFrame", padding=(24, 16))
        head.grid(row=0, column=0, columnspan=2, sticky="ew")
        brand = ttk.Frame(head, style="Header.TFrame")
        brand.pack(side="left", fill="y")
        ttk.Label(brand, text="SEA-LEVEL PROJECTION WORKSPACE",
                  style="Eyebrow.TLabel").pack(anchor="w")
        ttk.Label(brand, text="FACTS Projection Studio",
                  style="Head.TLabel").pack(anchor="w", pady=(2, 0))
        ttk.Label(brand, text="Configure, run, and monitor probabilistic projections",
                  style="HeadSub.TLabel").pack(anchor="w", pady=(2, 0))

        meta = ttk.Frame(head, style="Header.TFrame")
        meta.pack(side="right", fill="y", padx=(24, 0))
        ttk.Label(meta, text="LOCAL · WSL · DOCKER",
                  style="HeadMeta.TLabel").pack(anchor="e", pady=(4, 5))
        ttk.Label(meta, textvariable=self.v_summary, style="HeadSub.TLabel",
                  wraplength=500, justify="right").pack(anchor="e")

        self.tabs = ttk.Notebook(root)
        self.tabs.grid(row=1, column=0, columnspan=2, sticky="nsew")

        run_tab = ttk.Frame(self.tabs, style="Page.TFrame")
        run_tab.columnconfigure(0, weight=0, minsize=410)
        run_tab.columnconfigure(1, weight=1)
        run_tab.rowconfigure(1, weight=1)

        self.setup_tab = ttk.Frame(self.tabs, style="Page.TFrame")
        self.setup_tab.columnconfigure(0, weight=1)
        self.setup_tab.rowconfigure(0, weight=1)
        self.tabs.add(self.setup_tab, text="FACTS Setup")
        self.tabs.add(run_tab, text="Run projections")

        self._build_options(run_tab)
        self._build_output(run_tab)
        self._build_bottom(run_tab)
        self._build_setup(self.setup_tab)
        self.tabs.select(self.setup_tab)
        self.after(350, self._refresh_setup)

    def _build_options(self, root):
        canvas = tk.Canvas(root, highlightthickness=0, width=400, bg=PAGE,
                           bd=0)
        sb = ttk.Scrollbar(root, orient="vertical", command=canvas.yview)
        pane = ttk.Frame(canvas, style="Page.TFrame")
        pane.bind("<Configure>",
                  lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=pane, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.grid(row=1, column=0, sticky="nsew", padx=(18, 0), pady=18)
        sb.grid(row=1, column=0, sticky="nse", padx=(0, 3), pady=18)

        def section(title):
            f = ttk.Labelframe(pane, text=title, padding=(12, 10),
                               style="Sect.TLabelframe")
            f.pack(fill="x", pady=(0, 12), padx=(0, 16))
            return f

        # scenario
        f = section("Scenario")
        row = ttk.Frame(f)
        row.pack(fill="x")
        self.scen_buttons = {}
        for sid, _lws, _flag in SCENARIOS:
            rb = ttk.Radiobutton(row, value=sid, variable=self.v_scenario)
            rb.pack(side="left", padx=(0, 6))
            self.scen_buttons[sid] = rb
        ttk.Label(f, text="●  not in the repo yet: fetched from the FACTS "
                          "source when you run it",
                  style="Sub.TLabel", wraplength=330).pack(anchor="w", pady=(4, 0))
        self._refresh_scenario_marks()

        # scale
        f = section("Output scale")
        ttk.Radiobutton(f, text="Local: relative sea level at each gauge",
                        value=True, variable=self.v_local).pack(anchor="w")
        ttk.Radiobutton(f, text="Global mean only (location list ignored)",
                        value=False, variable=self.v_local).pack(anchor="w")

        # sites
        f = section("Sites")
        self.sites_frame = f

        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="Group by").pack(side="left")
        combo = ttk.Combobox(top, textvariable=self.v_grouping, width=15,
                             state="readonly", values=list(GROUPINGS.keys()))
        combo.pack(side="left", padx=6)
        combo.bind("<<ComboboxSelected>>", lambda e: self._build_group_buttons())
        ttk.Button(top, text="Load list…", width=11,
                   command=self._load_list).pack(side="right")

        self.group_box = ttk.Frame(f)
        self.group_box.pack(fill="x", pady=(6, 4))

        sel = ttk.Frame(f)
        sel.pack(fill="x")
        ttk.Button(sel, text="All", width=6,
                   command=lambda: self._set_sites(
                       set(s[1] for s in self.sites))).pack(side="left")
        ttk.Button(sel, text="None", width=6,
                   command=lambda: self._set_sites(set())).pack(side="left", padx=4)
        ttk.Button(sel, text="Invert", width=7,
                   command=lambda: self._set_sites(
                       set(s[1] for s in self.sites) - self.selected_ids)).pack(side="left")
        ttk.Label(sel, textvariable=self.v_sitecount,
                  style="Sub.TLabel").pack(side="right")

        lb = ttk.Frame(f)
        lb.pack(fill="both", expand=True, pady=(6, 0))
        self.sitebox = tk.Listbox(lb, selectmode="extended", height=9,
                                  exportselection=False, activestyle="none",
                                  font=("DejaVu Sans Mono", 9), bd=0,
                                  highlightthickness=1,
                                  highlightbackground=BORDER,
                                  highlightcolor=ACCENT,
                                  bg="#F8FAFB", fg=INK,
                                  selectbackground=ACCENT,
                                  selectforeground="#FFFFFF")
        vs = ttk.Scrollbar(lb, orient="vertical", command=self.sitebox.yview)
        self.sitebox.configure(yscrollcommand=vs.set)
        self.sitebox.pack(side="left", fill="both", expand=True)
        vs.pack(side="left", fill="y")
        self.sitebox.bind("<<ListboxSelect>>", self._on_sitebox)

        ttk.Label(f, text="Selection is written to the experiment's location.lst. "
                          "Ignored in global mode.",
                  style="Sub.TLabel", wraplength=330).pack(anchor="w", pady=(4, 0))

        self._build_group_buttons()
        self._fill_sitebox()

        # workflows
        f = section("Workflows")
        for w in ALL_WF:
            ais, gris, _glac, end = WORKFLOWS[w]
            ttk.Checkbutton(
                f, variable=self.v_wf[w],
                text="%-5s  AIS %-14s GrIS %-13s → %d" % (w, ais, gris, end),
            ).pack(anchor="w")
        ttk.Label(f, text="Modules in no selected workflow are dropped from the config.",
                  style="Sub.TLabel", wraplength=330).pack(anchor="w", pady=(4, 0))

        # optional modules
        f = section("Optional modules")
        ttk.Checkbutton(f, text="Vertical land motion (k14vlm)",
                        variable=self.v_vlm).pack(anchor="w")
        ttk.Label(f, text="Relative, not geocentric, sea level. Local runs only.",
                  style="Sub.TLabel", wraplength=330).pack(anchor="w", padx=(20, 0))
        ttk.Checkbutton(f, text="Extreme sea level (pointsoverthreshold)",
                        variable=self.v_esl).pack(anchor="w", pady=(6, 0))
        ttk.Label(f, text="Return curves, amplification factors, allowances. Untested here.",
                  style="Sub.TLabel", wraplength=330).pack(anchor="w", padx=(20, 0))
        er = ttk.Frame(f)
        er.pack(anchor="w", padx=(20, 0), pady=(4, 0))
        ttk.Label(er, text="target_years").pack(side="left")
        ttk.Entry(er, textvariable=self.v_eslyears, width=14).pack(side="left", padx=6)

        # sampling
        f = section("Sampling and horizon")
        grid = ttk.Frame(f)
        grid.pack(fill="x")
        for i, (lbl, var) in enumerate([
            ("nsamps", self.v_nsamps), ("baseyear", self.v_baseyear),
            ("pyear start", self.v_pstart), ("pyear end", self.v_pend),
            ("pyear step", self.v_pstep),
        ]):
            ttk.Label(grid, text=lbl).grid(row=i // 2, column=(i % 2) * 2,
                                           sticky="w", padx=(0, 6), pady=2)
            ttk.Entry(grid, textvariable=var, width=9).grid(
                row=i // 2, column=(i % 2) * 2 + 1, sticky="w", pady=2)

        # resources
        f = section("Container resources")
        grid = ttk.Frame(f)
        grid.pack(fill="x")
        ttk.Label(grid, text="CPU_COUNT").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(grid, textvariable=self.v_cpu, width=9).grid(row=0, column=1, sticky="w")
        ttk.Label(grid, text="MEMORY_LIMIT").grid(row=0, column=2, sticky="w", padx=(12, 6))
        ttk.Entry(grid, textvariable=self.v_mem, width=9).grid(row=0, column=3, sticky="w")

        # output destination
        f = section("Results output directory")
        ttk.Label(
            f,
            text="Enter a WSL/Linux path or a Windows path such as D:\\FACTS Results.",
            style="Sub.TLabel", wraplength=330).pack(anchor="w", pady=(0, 7))
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Entry(row, textvariable=self.v_outdir).pack(
            side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse…", width=10,
                   command=self._pick_outdir).pack(side="left", padx=(6, 0))
        ttk.Label(f, textvariable=self.v_outpath, style="Sub.TLabel",
                  wraplength=330).pack(anchor="w", pady=(4, 0))
        ttk.Button(f, text="Open folder", width=13,
                   command=self._open_outdir).pack(anchor="w", pady=(4, 0))
        self.v_outdir.trace_add("write", lambda *_: self._refresh_outpath())
        self._refresh_outpath()

        # paths
        f = section("Paths")
        ttk.Label(f, text="repo   " + FACTS_REPO, style="Mono.TLabel",
                  wraplength=330).pack(anchor="w")
        ttk.Label(f, text="runner " + RUNNER, style="Mono.TLabel",
                  wraplength=330).pack(anchor="w")

    def _build_output(self, root):
        wrap = ttk.Frame(root, padding=(16, 14), style="Footer.TFrame")
        wrap.grid(row=1, column=1, sticky="nsew", padx=(0, 18), pady=18)
        wrap.rowconfigure(1, weight=1)
        wrap.columnconfigure(0, weight=1)

        bar = ttk.Frame(wrap)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        title = ttk.Frame(bar)
        title.pack(side="left")
        ttk.Label(title, text="Execution console", style="PanelTitle.TLabel").pack(anchor="w")
        ttk.Label(title, text="Live task output and validation messages",
                  style="Sub.TLabel").pack(anchor="w", pady=(1, 0))
        ttk.Button(bar, text="Preview configuration",
                   command=self._preview).pack(side="right")

        self.log = scrolledtext.ScrolledText(
            wrap, wrap="none", font=("DejaVu Sans Mono", 9),
            bg=TERMINAL, fg=TERMINAL_TEXT, insertbackground=TERMINAL_TEXT,
            relief="flat", bd=0, padx=14, pady=12)
        self.log.grid(row=1, column=0, sticky="nsew")
        self.log.tag_config("info", foreground="#7FD1C6")
        self.log.tag_config("warn", foreground="#E8C98A")
        self.log.tag_config("err", foreground="#E2856A")
        self.log.tag_config("ok", foreground="#8FD9A8")
        self.log.configure(state="disabled")

        self._say("FACTS Dashboard ready.", "info")
        self._say("Select options on the left, then press Run.", "")
        if not os.path.isfile(RUNNER):
            self._say("Runner not found: %s" % RUNNER, "err")

    def _build_bottom(self, root):
        bottom = ttk.Frame(root, padding=(20, 13), style="Footer.TFrame")
        bottom.grid(row=2, column=0, columnspan=2, sticky="ew")
        bottom.columnconfigure(0, weight=1)

        self.warnlbl = ttk.Label(bottom, textvariable=self.v_warn,
                                 foreground=SIGNAL, wraplength=1100, justify="left")
        self.warnlbl.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))

        self.pbar = ttk.Progressbar(bottom, style="Bar.Horizontal.TProgressbar",
                                    variable=self.v_progress, maximum=100.0)
        self.pbar.grid(row=1, column=0, sticky="ew", padx=(0, 16))

        ttk.Label(bottom, textvariable=self.v_status, style="Mono.TLabel").grid(
            row=1, column=1, sticky="e")

        btns = ttk.Frame(bottom)
        btns.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))

        self.runbtn = ttk.Button(btns, text="▶  Run projection", style="Run.TButton",
                                 command=self._on_run)
        self.runbtn.pack(side="right")
        self.stopbtn = ttk.Button(btns, text="Stop run", style="Stop.TButton",
                                  command=self._on_stop,
                                  state="disabled")
        self.stopbtn.pack(side="right", padx=(0, 8))
        ttk.Label(btns, textvariable=self.v_summary, style="Sub.TLabel").pack(side="left")

    def _build_setup(self, root):
        body = ttk.Frame(root, style="Page.TFrame", padding=18)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=5)
        body.columnconfigure(1, weight=4)
        body.rowconfigure(1, weight=1)

        intro = ttk.Frame(body, padding=(16, 14), style="Footer.TFrame")
        intro.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        intro.columnconfigure(0, weight=1)
        ttk.Label(intro, text="Installation and environment",
                  style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            intro,
            text="Automated for Windows with WSL2 and native Linux. macOS is not supported by this installer.",
            style="Sub.TLabel").grid(row=1, column=0, sticky="w", pady=(3, 0))
        ttk.Label(intro, textvariable=self.v_setup_status,
                  style="Mono.TLabel").grid(row=0, column=1, rowspan=2,
                                             sticky="e", padx=(20, 0))

        checks = ttk.Frame(body, padding=(16, 14), style="Footer.TFrame")
        checks.grid(row=1, column=0, sticky="nsew", padx=(0, 7))
        checks.columnconfigure(0, weight=1)
        checks.rowconfigure(2, weight=1)
        ttk.Label(checks, text="System readiness",
                  style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(checks, text="Refresh after changing Docker or WSL settings.",
                  style="Sub.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 10))

        self.setup_tree = ttk.Treeview(
            checks, columns=("status", "detail"), show="tree headings",
            style="Setup.Treeview", selectmode="none")
        self.setup_tree.heading("#0", text="Component", anchor="w")
        self.setup_tree.heading("status", text="Status", anchor="w")
        self.setup_tree.heading("detail", text="Details", anchor="w")
        self.setup_tree.column("#0", width=165, minwidth=130, stretch=False)
        self.setup_tree.column("status", width=105, minwidth=90, stretch=False)
        self.setup_tree.column("detail", width=330, minwidth=180, stretch=True)
        self.setup_tree.grid(row=2, column=0, sticky="nsew")

        opts = ttk.Frame(checks)
        opts.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        ttk.Checkbutton(
            opts, variable=self.v_setup_data,
            text="Full local module data (large, resumable download; allow about 60 GB)"
        ).pack(anchor="w")
        ttk.Checkbutton(
            opts, variable=self.v_setup_rebuild,
            text="Rebuild Docker images even when they already exist"
        ).pack(anchor="w")

        actions = ttk.Frame(checks)
        actions.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        self.setup_refresh_btn = ttk.Button(
            actions, text="Refresh checks", command=self._refresh_setup)
        self.setup_refresh_btn.pack(side="left")
        self.setup_install_btn = ttk.Button(
            actions, text="Install / repair FACTS", style="Run.TButton",
            command=self._on_setup)
        self.setup_install_btn.pack(side="right")
        self.setup_stop_btn = ttk.Button(
            actions, text="Stop setup", style="Stop.TButton",
            command=self._on_stop_setup, state="disabled")
        self.setup_stop_btn.pack(side="right", padx=(0, 8))

        console = ttk.Frame(body, padding=(16, 14), style="Footer.TFrame")
        console.grid(row=1, column=1, sticky="nsew", padx=(7, 0))
        console.columnconfigure(0, weight=1)
        console.rowconfigure(2, weight=1)
        ttk.Label(console, text="Setup console",
                  style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(console, text="Repository, data, and image installation output",
                  style="Sub.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 10))
        self.setup_log = scrolledtext.ScrolledText(
            console, wrap="word", font=("DejaVu Sans Mono", 9),
            bg=TERMINAL, fg=TERMINAL_TEXT, insertbackground=TERMINAL_TEXT,
            relief="flat", bd=0, padx=14, pady=12)
        self.setup_log.grid(row=2, column=0, sticky="nsew")
        self.setup_log.tag_config("info", foreground="#7FD1C6")
        self.setup_log.tag_config("warn", foreground="#E8C98A")
        self.setup_log.tag_config("err", foreground="#E2856A")
        self.setup_log.tag_config("ok", foreground="#8FD9A8")
        self.setup_log.insert(
            "end", "FACTS setup is ready. Refresh checks, then install only what is missing.\n")
        self.setup_log.configure(state="disabled")

    # -- setup -----------------------------------------------------------

    @staticmethod
    def _check_command(args, timeout=15):
        try:
            result = subprocess.run(args, capture_output=True, text=True,
                                    timeout=timeout, errors="replace")
            text = (result.stdout or result.stderr or "").strip()
            return result.returncode == 0, text
        except (OSError, subprocess.SubprocessError) as exc:
            return False, str(exc)

    def _refresh_setup(self):
        if self.setup_running:
            return
        self.v_setup_status.set("Checking installation…")
        self.setup_refresh_btn.configure(state="disabled")
        threading.Thread(target=self._scan_setup, daemon=True).start()

    def _scan_setup(self):
        rows = []
        release = ""
        try:
            with open("/proc/version", "r", encoding="utf-8") as fh:
                release = fh.read().lower()
        except OSError:
            pass
        platform_name = "Windows + WSL2" if "microsoft" in release else "Linux"
        rows.append(("Operating system", "Ready", platform_name))

        git_ok, git_detail = self._check_command(["git", "--version"])
        rows.append(("Git", "Ready" if git_ok else "Missing",
                     git_detail or "Install Git"))

        docker_ok, docker_detail = self._check_command(["docker", "info"])
        if docker_ok:
            _ok, version = self._check_command(
                ["docker", "version", "--format", "{{.Server.Version}}"])
            docker_detail = "Engine " + (version or "responding")
        rows.append(("Docker engine", "Ready" if docker_ok else "Needs attention",
                     docker_detail.splitlines()[-1] if docker_detail else
                     "Start Docker Desktop or the Linux Docker service"))

        repo_ok = (os.path.isdir(os.path.join(FACTS_REPO, ".git")) and
                   os.path.isdir(os.path.join(FACTS_REPO, "modules")))
        branch = ""
        if repo_ok:
            _ok, branch = self._check_command(
                ["git", "-C", FACTS_REPO, "branch", "--show-current"])
        rows.append(("FACTS repository", "Ready" if repo_ok else "Missing",
                     (FACTS_REPO + (" · " + branch if branch else ""))))

        image_ok, _detail = self._check_command(
            ["docker", "image", "inspect", "ssisls"])
        rows.append(("FACTS image", "Ready" if image_ok else "Missing",
                     "ssisls" if image_ok else "Will be built by setup"))

        url_list = os.path.join(FACTS_REPO, "modules-data", "modules-data.urls.txt")
        total = present = 0
        if os.path.isfile(url_list):
            try:
                with open(url_list, "r", encoding="utf-8", errors="replace") as fh:
                    for raw in fh:
                        url = raw.strip()
                        if not url or url.startswith("#"):
                            continue
                        total += 1
                        name = url.rsplit("/", 1)[-1]
                        path = os.path.join(os.path.dirname(url_list), name)
                        if os.path.isfile(path) and os.path.getsize(path) > 0:
                            present += 1
            except OSError:
                pass
        data_ready = total > 0 and present == total
        rows.append(("Local module data", "Ready" if data_ready else "Optional",
                     "%d of %d official archives present" % (present, total)
                     if total else "Available after the FACTS repository is cloned"))

        dash_ok = os.path.isdir(os.path.join(
            os.path.expanduser("~/facts_ssisls/facts.plotting.dashboard"), ".git"))
        rows.append(("Dashboard source", "Ready" if dash_ok else "Missing",
                     os.path.expanduser("~/facts_ssisls/facts.plotting.dashboard")))
        viz_ok, _detail = self._check_command(
            ["docker", "image", "inspect", "facts-viz"])
        rows.append(("Dashboard image", "Ready" if viz_ok else "Missing",
                     "facts-viz" if viz_ok else "Will be built by setup"))

        launchers_ok = all(os.path.isfile(p) for p in
                           (RUNNER, SETUP_SCRIPT, os.path.join(FACTS_DIR, "facts-dashboard")))
        rows.append(("Local launchers", "Ready" if launchers_ok else "Missing",
                     FACTS_DIR))

        required_ready = git_ok and docker_ok and repo_ok and image_ok and launchers_ok
        self.msgq.put(("setup_scan", rows,
                       "Ready to run" if required_ready else "Setup required"))

    def _apply_setup_scan(self, rows, summary):
        for item in self.setup_tree.get_children():
            self.setup_tree.delete(item)
        self.setup_tree.tag_configure("ready", foreground=OKCOL)
        self.setup_tree.tag_configure("missing", foreground=SIGNAL)
        self.setup_tree.tag_configure("optional", foreground="#8B641A")
        for component, status, detail in rows:
            tag = ("ready" if status == "Ready" else
                   "optional" if status == "Optional" else "missing")
            self.setup_tree.insert("", "end", text=component,
                                   values=(status, detail), tags=(tag,))
        self.v_setup_status.set(summary)
        self.setup_refresh_btn.configure(state="normal")
        if summary != "Ready to run" and not self.running:
            self.tabs.select(self.setup_tab)

    def _setup_say(self, text, tag=""):
        self.setup_log.configure(state="normal")
        self.setup_log.insert("end", text + "\n", tag)
        self.setup_log.see("end")
        self.setup_log.configure(state="disabled")

    def _on_setup(self):
        if self.setup_running or self.running:
            messagebox.showinfo("FACTS is busy",
                                "Wait for the current run or setup operation to finish.")
            return
        if not os.path.isfile(SETUP_SCRIPT):
            messagebox.showerror("Setup script missing", "Not found:\n" + SETUP_SCRIPT)
            return

        full_data = self.v_setup_data.get()
        rebuild = self.v_setup_rebuild.get()
        data_note = (
            "Full local module data: YES\n"
            "This is a large resumable download; keep about 60 GB free."
            if full_data else
            "Full local module data: NO\n"
            "The repositories and images will be prepared, but local runs may still need data.")
        if not messagebox.askokcancel(
                "Install or repair FACTS",
                "Target: Windows/WSL2 or Linux\n"
                "Install root: %s\n\n%s\n\n"
                "Existing repositories and outputs will be preserved. Proceed?"
                % (os.path.expanduser("~/facts_ssisls"), data_note)):
            return

        args = ["bash", SETUP_SCRIPT]
        if full_data:
            args.append("--with-data")
        if rebuild:
            args.append("--rebuild")

        self.setup_running = True
        self.setup_install_btn.configure(state="disabled")
        self.setup_refresh_btn.configure(state="disabled")
        self.setup_stop_btn.configure(state="normal")
        self.runbtn.configure(state="disabled")
        self.v_setup_status.set("Installing…")
        self._setup_say("", "")
        self._setup_say("=" * 58, "info")
        self._setup_say("Starting automated FACTS setup", "info")
        self._setup_say("=" * 58, "info")
        threading.Thread(target=self._setup_thread, args=(args,), daemon=True).start()

    def _setup_thread(self, args):
        try:
            self.setup_proc = subprocess.Popen(
                args, cwd=FACTS_DIR, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1, errors="replace")
        except OSError as exc:
            self.msgq.put(("setup_raw", "Could not start setup: %s" % exc, "err"))
            self.msgq.put(("setup_done", 1, ""))
            return
        for raw in self.setup_proc.stdout:
            self.msgq.put(("setup_raw", raw.rstrip("\n"), ""))
        self.msgq.put(("setup_done", self.setup_proc.wait(), ""))

    def _on_stop_setup(self):
        if not self.setup_running:
            return
        if not messagebox.askokcancel(
                "Stop setup",
                "Stop the current setup process? Completed downloads are kept and can resume later."):
            return
        if self.setup_proc and self.setup_proc.poll() is None:
            try:
                self.setup_proc.terminate()
                self._setup_say("Stopping setup…", "warn")
            except OSError as exc:
                self._setup_say("Could not stop setup: %s" % exc, "err")

    def _finish_setup(self, code):
        self.setup_running = False
        self.setup_install_btn.configure(state="normal")
        self.setup_refresh_btn.configure(state="normal")
        self.setup_stop_btn.configure(state="disabled")
        self.runbtn.configure(state="normal")
        if code == 0:
            self.v_setup_status.set("Setup complete")
            self._setup_say("Setup completed successfully.", "ok")
            self._refresh_scenario_marks()
            self.after(300, self._refresh_setup)
        else:
            self.v_setup_status.set("Setup stopped or failed")
            self._setup_say("Setup ended with exit code %d." % code, "err")

    # -- helpers ----------------------------------------------------------

    def _say(self, text, tag=""):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _int(self, var, fallback):
        try:
            return int(str(var.get()).strip())
        except (ValueError, AttributeError):
            return fallback

    def _selected_wf(self):
        return [w for w in ALL_WF if self.v_wf[w].get()]

    def _scenario_row(self):
        for row in SCENARIOS:
            if row[0] == self.v_scenario.get():
                return row
        return SCENARIOS[1]

    # -- output destination -----------------------------------------------

    @staticmethod
    def _portable_path(path):
        """Translate a typed Windows drive path to its normal WSL mount."""
        path = os.path.expanduser(path.strip())
        match = re.match(r"^([A-Za-z]):[\\/](.*)$", path)
        if match:
            drive = match.group(1).lower()
            tail = match.group(2).replace("\\", "/")
            return "/mnt/%s/%s" % (drive, tail)
        return path

    def _out_root(self):
        return self._portable_path(self.v_outdir.get()) or DEFAULT_OUTPUT

    def _out_dir(self):
        """Where this scenario's NetCDFs will land."""
        return os.path.join(self._out_root(), self.v_scenario.get())

    def _refresh_outpath(self):
        root = self._out_root()
        note = "Files land in:  %s" % self._out_dir()
        if not os.path.isdir(root):
            note += "\n(will be created)"
        self.v_outpath.set(note)

    def _pick_outdir(self):
        start = self._out_root()
        while start and not os.path.isdir(start):
            parent = os.path.dirname(start)
            if parent == start:
                break
            start = parent
        chosen = filedialog.askdirectory(
            title="Choose where FACTS output should be saved",
            initialdir=start or os.path.expanduser("~"), mustexist=False)
        if chosen:
            self.v_outdir.set(chosen)
            self.settings["output_root"] = chosen
            save_settings(self.settings)

    def _open_outdir(self):
        target = self._out_dir()
        if not os.path.isdir(target):
            target = self._out_root()
        if not os.path.isdir(target):
            messagebox.showinfo("Nothing there yet",
                                "This folder does not exist until a run finishes:\n\n%s"
                                % self._out_dir())
            return
        for opener in (["xdg-open", target], ["explorer.exe", "."]):
            try:
                subprocess.Popen(opener, cwd=target,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except OSError:
                continue
        messagebox.showinfo("Output folder", target)

    # -- site selection ---------------------------------------------------

    def _build_group_buttons(self):
        """One toggle button per group in the current grouping."""
        for w in self.group_box.winfo_children():
            w.destroy()
        groups = GROUPINGS.get(self.v_grouping.get(), REGIONS)
        present = set(s[1] for s in self.sites)
        row = ttk.Frame(self.group_box)
        row.pack(fill="x")
        count = 0
        for label, ids in groups:
            here = [i for i in ids if i in present]
            if not here:
                continue
            if count and count % 4 == 0:
                row = ttk.Frame(self.group_box)
                row.pack(fill="x")
            ttk.Button(row, text="%s (%d)" % (label, len(here)), width=17,
                       command=lambda h=here: self._toggle_group(h)).pack(
                           side="left", padx=(0, 3), pady=1)
            count += 1
        if not count:
            ttk.Label(self.group_box,
                      text="No known groups for this list: use the gauge list below.",
                      style="Sub.TLabel", wraplength=330).pack(anchor="w")

    def _toggle_group(self, ids):
        """Add the group if any member is missing, else remove it."""
        ids = set(ids)
        if ids <= self.selected_ids:
            self._set_sites(self.selected_ids - ids)
        else:
            self._set_sites(self.selected_ids | ids)

    def _set_sites(self, ids):
        self.selected_ids = set(ids)
        self._sync_sitebox()
        self._refresh_summary()

    def _fill_sitebox(self):
        self.sitebox.delete(0, "end")
        for name, sid, lat, lon in self.sites:
            self.sitebox.insert("end", "%-8s %-30s %6.2f %8.2f"
                                % (sid, name[:30], lat, lon))
        self._sync_sitebox()

    def _sync_sitebox(self):
        """Push self.selected_ids into the listbox highlight."""
        self._syncing = True
        self.sitebox.selection_clear(0, "end")
        for i, (_n, sid, _la, _lo) in enumerate(self.sites):
            if sid in self.selected_ids:
                self.sitebox.selection_set(i)
        self._syncing = False
        self.v_sitecount.set("%d of %d selected"
                             % (len(self.selected_ids), len(self.sites)))

    def _on_sitebox(self, _evt):
        if getattr(self, "_syncing", False):
            return
        picked = set(self.sitebox.curselection())
        self.selected_ids = set(self.sites[i][1] for i in picked)
        self.v_sitecount.set("%d of %d selected"
                             % (len(self.selected_ids), len(self.sites)))
        self._refresh_summary()

    def _load_list(self):
        """Point the dashboard at a different location list."""
        path = filedialog.askopenfilename(
            title="Select a FACTS location list",
            initialdir=os.path.join(FACTS_DIR, "configs"),
            filetypes=[("Location lists", "*.lst"), ("All files", "*.*")])
        if not path:
            return
        sites = load_sites(path)
        if not sites:
            messagebox.showerror(
                "Unusable list",
                "No valid rows found in:\n%s\n\n"
                "Expected tab-separated:  NAME<TAB>ID<TAB>LAT<TAB>LON" % path)
            return
        self.sites = sites
        self.site_path = path
        self.selected_ids = set(s[1] for s in sites)
        self._build_group_buttons()
        self._fill_sitebox()
        self._refresh_summary()
        self._say("Loaded %d sites from %s" % (len(sites), path), "ok")

    def _refresh_scenario_marks(self):
        """Label each scenario with whether its definition is present."""
        for sid, rb in getattr(self, "scen_buttons", {}).items():
            rb.configure(text=sid if is_installed(sid) else sid + " ●")

    def _refresh_summary(self):
        sel = self._selected_wf()
        local = self.v_local.get()
        sid, _lws, _flag = self._scenario_row()
        installed = is_installed(sid)
        self._refresh_scenario_marks()

        n_mod = 0
        for _k, _s, _m, wfs, _o, flags in MODULES:
            if flags.get("optional") == "vlm" and (not self.v_vlm.get() or not local):
                continue
            if any(w in sel for w in wfs):
                n_mod += 1

        nsites = len(self.selected_ids)
        self.v_summary.set(
            "%s  ·  %s  ·  %d workflows  ·  %d modules%s"
            % (sid, "local" if local else "global", len(sel), n_mod,
               "  ·  %d sites" % nsites if local else ""))

        warns = []
        if not sel:
            warns.append("No workflows selected: FACTS would produce no totals.")
        if not installed:
            warns.append("%s is not in the repo yet: it will be fetched from the "
                         "FACTS source before the run starts." % sid)
        if self.v_esl.get() and not local:
            warns.append("Extreme sea level needs local totals; it is skipped in global mode.")
        if local and not self.v_vlm.get():
            warns.append("Without k14vlm the result is geocentric, not relative, sea level.")
        e_sel = [w for w in sel if WORKFLOWS[w][3] == 2100]
        if e_sel and self._int(self.v_pend, 2150) > 2100:
            warns.append("%s capped at 2100 (emulandice); FACTS takes the minimum "
                         "pyear_end across a workflow." % ", ".join(e_sel))
        if local and not self.selected_ids:
            warns.append("No sites selected: a local run needs at least one gauge.")
        load = self._int(self.v_nsamps, 2000) * max(nsites, 1) * max(len(sel), 1)
        if local and load > 900000:
            warns.append("Heavy configuration (%s samples × %d sites × %d workflows)."
                         % ("{:,}".format(self._int(self.v_nsamps, 2000)),
                            nsites, len(sel)))
        self.v_warn.set("   ".join("⚠ " + w for w in warns))

    def _compose(self):
        sid, lws, _installed = self._scenario_row()
        return build_config(
            sid, lws, self.v_local.get(), self._selected_wf(),
            self.v_vlm.get(), self.v_esl.get(), self.v_eslyears.get().strip(),
            self._int(self.v_nsamps, 2000), self._int(self.v_baseyear, 2005),
            self._int(self.v_pstart, 2020), self._int(self.v_pend, 2150),
            self._int(self.v_pstep, 10))

    def _preview(self):
        win = tk.Toplevel(self)
        win.title("config.yml: %s" % self.v_scenario.get())
        win.geometry("760x680")
        txt = scrolledtext.ScrolledText(win, wrap="none", font=("DejaVu Sans Mono", 9),
                                        bg="#12201E", fg="#D7E3E0", relief="flat",
                                        padx=8, pady=6)
        txt.pack(fill="both", expand=True)
        txt.insert("1.0", self._compose())
        txt.configure(state="disabled")

    # -- run --------------------------------------------------------------

    def _on_run(self):
        if self.running:
            return
        sel = self._selected_wf()
        if not sel:
            messagebox.showerror("No workflows", "Select at least one workflow.")
            return
        sid, _lws, _flag = self._scenario_row()
        if not os.path.isfile(RUNNER):
            messagebox.showerror("Runner missing", "Not found:\n%s" % RUNNER)
            return

        # Fetch the experiment definition from the FACTS source if absent.
        if not is_installed(sid):
            if not messagebox.askokcancel(
                    "Fetch experiment",
                    "%s is not in your FACTS repo yet.\n\n"
                    "Fetch its definition now?\n\n"
                    "Source order:\n"
                    "  1. local clone   %s\n"
                    "  2. FACTS branch  demo/ssisls"
                    % (sid, LOCAL_CLONE)):
                return
            try:
                got = provision(sid)
                for name, src in got:
                    self._say("Fetched %s from %s" % (name, src), "ok")
            except (OSError, urllib.error.URLError) as exc:
                messagebox.showerror(
                    "Fetch failed",
                    "Could not obtain %s.\n\n%s\n\n"
                    "Check your connection, or copy the folder in by hand."
                    % (sid, exc))
                return
            self._refresh_scenario_marks()
            self._refresh_summary()

        exp_dir = exp_dir_for(sid)

        # output destination must exist and be writable before anything runs
        out_root = self._out_root()
        try:
            os.makedirs(out_root, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Output folder unusable",
                                 "Could not create:\n%s\n\n%s" % (out_root, exc))
            return
        if not os.access(out_root, os.W_OK):
            messagebox.showerror("Output folder not writable",
                                 "No write permission for:\n%s" % out_root)
            return

        n_local = len(sel) * (2 if self.v_local.get() else 1)
        nsites = len(self.selected_ids)
        if not messagebox.askokcancel(
                "Run FACTS",
                "Scenario:   %s\nScale:      %s\nWorkflows:  %d\nTotals:     %d\n"
                "%s\n"
                "Save to:    %s\n\n"
                "This overwrites:\n%s/config.yml\n(a timestamped backup is kept)\n\n"
                "The run can take well over an hour. Proceed?"
                % (sid, "local" if self.v_local.get() else "global",
                   len(sel), n_local,
                   "Sites:      %d" % nsites if self.v_local.get() else "",
                   self._out_dir(), exp_dir)):
            return

        self.settings["output_root"] = out_root
        save_settings(self.settings)

        # write config, keeping a backup
        cfg_path = os.path.join(exp_dir, "config.yml")
        try:
            if os.path.isfile(cfg_path):
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                bak = cfg_path + ".bak." + stamp
                shutil.copy2(cfg_path, bak)
                self._say("Backed up existing config -> %s" % os.path.basename(bak), "info")
            with open(cfg_path, "w", newline="\n") as fh:
                fh.write(self._compose())
            self._say("Wrote %s" % cfg_path, "ok")
        except OSError as exc:
            messagebox.showerror("Could not write config", str(exc))
            return

        # install the location list for local runs
        if self.v_local.get():
            dst = os.path.join(exp_dir, "location.lst")
            chosen = [s for s in self.sites if s[1] in self.selected_ids]
            if chosen:
                try:
                    # Tab separated, LF endings: the file is parsed inside Linux.
                    # NB: do not bind a loop variable named `sid` here; it holds
                    # the scenario id for the rest of this method.
                    with open(dst, "w", newline="\n", encoding="utf-8") as fh:
                        for s_name, s_id, s_lat, s_lon in chosen:
                            fh.write("%s\t%s\t%.2f\t%.2f\n"
                                     % (s_name, s_id, s_lat, s_lon))
                    regions = sorted(set(region_of(s[1]) for s in chosen))
                    self._say("Installed location list: %d sites (%s)"
                              % (len(chosen), ", ".join(regions)), "ok")
                except OSError as exc:
                    messagebox.showerror("Could not install location list", str(exc))
                    return
            else:
                self._say("No sites selected: leaving the existing location.lst "
                          "in place.", "warn")

        # launch
        self.running = True
        self.done_tasks = 0
        self.v_progress.set(0.0)
        self.runbtn.configure(state="disabled")
        self.stopbtn.configure(state="normal")
        self.container_prefix = "facts_%s_" % sid
        self.v_status.set("starting…")
        self._say("", "")
        self._say("=" * 66, "info")
        self._say("Launching run_facts_slr.sh %s" % sid, "info")
        self._say("=" * 66, "info")

        self._say("Output destination: %s" % self._out_dir(), "info")

        env = dict(os.environ)
        env["CPU_COUNT"] = str(self._int(self.v_cpu, 8))
        env["MEMORY_LIMIT"] = self.v_mem.get().strip() or "12g"
        env["FACTS_REPO"] = FACTS_REPO
        env["OUTPUT_ROOT"] = self._out_root()

        self.worker = threading.Thread(target=self._run_thread, args=(sid, env), daemon=True)
        self.worker.start()

    def _run_thread(self, scenario, env):
        started = time.time()
        try:
            self.proc = subprocess.Popen(
                ["bash", RUNNER, scenario],
                cwd=FACTS_DIR, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, errors="replace")
        except OSError as exc:
            self.msgq.put(("line", "Could not start runner: %s" % exc, "err"))
            self.msgq.put(("done", 1, time.time() - started))
            return

        for raw in self.proc.stdout:
            self.msgq.put(("raw", raw.rstrip("\n"), ""))

        code = self.proc.wait()
        self.msgq.put(("done", code, time.time() - started))

    def _on_stop(self):
        if not self.running:
            return
        if not messagebox.askokcancel("Stop run", "Terminate the running FACTS container?"):
            return
        self._say("Stopping…", "warn")
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass
        # run_facts_slr.sh names the container facts_<scenario>_<timestamp>
        try:
            out = subprocess.run(
                ["docker", "ps", "-q", "--filter", "name=" + self.container_prefix],
                capture_output=True, text=True, timeout=20).stdout.split()
            for cid in out:
                subprocess.run(["docker", "kill", cid], capture_output=True, timeout=30)
                self._say("Killed container %s" % cid, "warn")
        except (OSError, subprocess.SubprocessError) as exc:
            self._say("Could not kill container: %s" % exc, "err")

    # -- message pump -----------------------------------------------------

    PROG = re.compile(r"tasks (\d+)/(\d+)")
    DONE = re.compile(r"Completed:\s*(.+?)\s*$")

    def _pump(self):
        try:
            while True:
                kind, a, b = self.msgq.get_nowait()
                if kind == "raw":
                    self._handle_line(a)
                elif kind == "line":
                    self._say(a, b)
                elif kind == "done":
                    self._finish(a, b)
                elif kind == "setup_scan":
                    self._apply_setup_scan(a, b)
                elif kind == "setup_raw":
                    low = a.lower()
                    tag = ("err" if "error" in low or "missing" in low else
                           "warn" if "warn" in low else
                           "ok" if "complete" in low or "already exists" in low else "")
                    if a.strip():
                        self._setup_say(a, tag)
                elif kind == "setup_done":
                    self._finish_setup(a)
        except queue.Empty:
            pass
        self.after(120, self._pump)

    def _handle_line(self, line):
        m = self.PROG.search(line)
        if m:
            self.done_tasks = int(m.group(1))
            self.total_tasks = max(int(m.group(2)), 1)
            pct = min(self.done_tasks * 100.0 / self.total_tasks, 99.0)
            self.v_progress.set(pct)
            d = self.DONE.search(line)
            self.v_status.set("%d/%d  %s" % (self.done_tasks, self.total_tasks,
                                             d.group(1) if d else ""))
            return
        low = line.lower()
        tag = ""
        if "fail" in low or "error" in low or "missing" in low:
            tag = "err"
        elif "warn" in low:
            tag = "warn"
        elif "complete" in low:
            tag = "ok"
        if line.strip():
            self._say(line, tag)

    def _finish(self, code, elapsed):
        self.running = False
        self.runbtn.configure(state="normal")
        self.stopbtn.configure(state="disabled")
        mins = int(elapsed // 60)
        secs = int(elapsed % 60)
        if code == 0:
            self.v_progress.set(100.0)
            self.v_status.set("complete in %dm %ds" % (mins, secs))
            self._say("", "")
            self._say("Run complete in %dm %ds." % (mins, secs), "ok")
            outdir = self._out_dir()
            self._say("Outputs: %s" % outdir, "ok")
            try:
                ncs = [f for f in os.listdir(outdir) if f.endswith(".nc")]
                loc = [f for f in ncs if "local" in f]
                self._say("%d NetCDF files, %d local." % (len(ncs), len(loc)),
                          "ok" if loc or not self.v_local.get() else "warn")
                if self.v_local.get() and not loc:
                    self._say("No local files: check pipeline_file in the config.", "err")
            except OSError:
                pass
        else:
            self.v_status.set("failed (exit %d) after %dm %ds" % (code, mins, secs))
            self._say("", "")
            self._say("Run FAILED with exit code %d." % code, "err")
            self._say("Detailed log: %s"
                      % os.path.join(FACTS_DIR, "FACTS_RUN_%s.log" % self.v_scenario.get()),
                      "err")

    def _on_close(self):
        if self.running or self.setup_running:
            activity = "run" if self.running else "setup operation"
            if not messagebox.askokcancel(
                    "Quit", "A %s is in progress. Quit the dashboard anyway?" % activity):
                return
        self.destroy()


def main():
    app = Dashboard()
    app.mainloop()


if __name__ == "__main__":
    main()
