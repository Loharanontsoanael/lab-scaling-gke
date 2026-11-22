"""Analyse des résultats d'une expérience → tableaux + graphiques pour le rapport.

Usage : python experiments/analyze.py results/<scénario>/<horodatage>

Produit dans ce dossier :
  steps.csv          métriques par palier stable (toutes répétitions)
  summary.md         tableaux moyenne ± IC95 % + conclusions chiffrées
  figures/*.png      graphiques
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy import stats  # noqa: E402
from scipy.optimize import curve_fit  # noqa: E402

WARMUP_FRACTION = 0.25  # on ignore le premier quart de chaque palier (transitoire)
THINK_TIME_S = 1.0  # moyenne de between(0.5, 1.5) dans le locustfile
plt.rcParams.update({"figure.dpi": 130, "axes.grid": True, "grid.alpha": 0.3})


# --------------------------------------------------------------------------- chargement
def load_run(run_dir: Path) -> dict:
    meta = json.loads((run_dir / "meta.json").read_text())
    hist = pd.read_csv(run_dir / "locust_stats_history.csv", na_values=["N/A"])
    hist = hist[hist["Name"] == "Aggregated"].copy()
    prom = pd.read_csv(run_dir / "prometheus.csv")
    return {"dir": run_dir, "meta": meta, "hist": hist, "prom": prom}


def steady_steps(run: dict) -> pd.DataFrame:
    """Découpe l'historique Locust en paliers et garde la partie stable de chacun."""
    h, m = run["hist"], run["meta"]
    t0 = h["Timestamp"].min()
    step_s = m["step_seconds"]
    h = h.assign(step=((h["Timestamp"] - t0) // step_s).astype(int), t_in_step=(h["Timestamp"] - t0) % step_s)
    h = h[h["t_in_step"] >= step_s * WARMUP_FRACTION]
    rows = []
    for step, g in h.groupby("step"):
        rps = g["Requests/s"].mean()
        fail = g["Failures/s"].mean()
        rows.append(
            {
                "scenario": m["scenario"],
                "config": m["config"]["label"],
                "replicas_cfg": m["config"]["replicas"],
                "rep": m["repetition"],
                "step": step,
                "users": int(g["User Count"].median()),
                "throughput_rps": rps,
                "error_rate_pct": 100 * fail / rps if rps else np.nan,
                "p50_ms": g["50%"].median(),
                "p95_ms": g["95%"].median(),
                "p99_ms": g["99%"].median(),
                "avg_ms": _window_avg(g),
                "replicas_avail": _prom_mean(run, g["Timestamp"].min(), g["Timestamp"].max(), "replicas_available"),
                "cpu_cores": _prom_mean(run, g["Timestamp"].min(), g["Timestamp"].max(), "cpu_cores"),
                "throttled_pct": _prom_mean(run, g["Timestamp"].min(), g["Timestamp"].max(), "throttled_pct"),
            }
        )
    return pd.DataFrame(rows)


def _window_avg(g: pd.DataFrame) -> float:
    """Temps de réponse moyen sur la fenêtre, à partir des cumuls Locust (moyenne × nombre)."""
    c = g["Total Request Count"].to_numpy(float)
    a = g["Total Average Response Time"].to_numpy(float)
    if len(c) < 2 or c[-1] <= c[0]:
        return float(a[-1]) if len(a) else np.nan
    return float((a[-1] * c[-1] - a[0] * c[0]) / (c[-1] - c[0]))


def _prom_mean(run: dict, t1: float, t2: float, col: str) -> float:
    p = run["prom"]
    sel = p[(p["timestamp"] >= t1) & (p["timestamp"] <= t2)][col]
    return float(sel.mean()) if len(sel) else np.nan


def ci95(x: pd.Series) -> float:
    x = x.dropna()
    if len(x) < 2:
        return 0.0
    return float(stats.t.ppf(0.975, len(x) - 1) * x.std(ddof=1) / np.sqrt(len(x)))


def aggregate(steps: pd.DataFrame) -> pd.DataFrame:
    cols = ["throughput_rps", "p95_ms", "p99_ms", "avg_ms", "error_rate_pct", "replicas_avail", "throttled_pct"]
    g = steps.groupby(["config", "step", "users"], sort=False)
    out = g[cols].mean().add_suffix("_mean")
    for c in ["throughput_rps", "p95_ms"]:
        out[f"{c}_ci95"] = g[c].apply(ci95)
    out["n"] = g.size()
    return out.reset_index()


# --------------------------------------------------------------------------- analyses
def hpa_reaction(run: dict) -> pd.DataFrame:
    """Pour chaque montée de palier : délai de décision du HPA et délai jusqu'aux pods prêts."""
    m, p = run["meta"], run["prom"].dropna(subset=["replicas_desired"])
    t0, step_s = run["hist"]["Timestamp"].min(), m["step_seconds"]
    rows = []
    for k in range(1, len(m["steps"].split(","))):
        ts = t0 + k * step_s
        before = p[p["timestamp"] <= ts]["replicas_desired"]
        if before.empty:
            continue
        base = before.iloc[-1]
        after = p[(p["timestamp"] > ts) & (p["timestamp"] < ts + step_s)]
        up = after[after["replicas_desired"] > base]
        if up.empty:
            rows.append({"step": k, "replicas_before": base, "decision_s": np.nan, "ready_s": np.nan})
            continue
        t_dec, target = up["timestamp"].iloc[0], up["replicas_desired"].iloc[0]
        ready = after[(after["timestamp"] >= t_dec) & (after["replicas_available"] >= target)]
        rows.append(
            {
                "step": k,
                "replicas_before": base,
                "replicas_target": target,
                "decision_s": t_dec - ts,
                "ready_s": (ready["timestamp"].iloc[0] - ts) if len(ready) else np.nan,
            }
        )
    return pd.DataFrame(rows)


def usl(n, lam, alpha, beta):
    """Universal Scalability Law (Gunther) : X(N) = λN / (1 + α(N−1) + βN(N−1))."""
    return lam * n / (1 + alpha * (n - 1) + beta * n * (n - 1))


def fit_usl(steps: pd.DataFrame) -> dict | None:
    pts = steps.groupby("replicas_cfg")["throughput_rps"].mean()
    if len(pts) < 3:
        return None
    n, x = pts.index.to_numpy(float), pts.to_numpy(float)
    (lam, a, b), _ = curve_fit(usl, n, x, p0=[x[0], 0.01, 0.0001], bounds=([0, 0, 0], [np.inf, 1, 1]))
    n_star = np.sqrt((1 - a) / b) if b > 0 else np.inf
    return {"lambda": lam, "alpha": a, "beta": b, "n_star": n_star, "n": n, "x": x}


# --------------------------------------------------------------------------- graphiques
def fig_load_curves(agg: pd.DataFrame, figdir: Path) -> None:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.5))
    for cfg, g in agg.groupby("config", sort=False):
        ax1.errorbar(g["users"], g["throughput_rps_mean"], yerr=g["throughput_rps_ci95"], marker="o", capsize=3, label=cfg)
        ax2.errorbar(g["users"], g["p95_ms_mean"], yerr=g["p95_ms_ci95"], marker="o", capsize=3, label=cfg)
    ax1.set(xlabel="Utilisateurs simultanés", ylabel="Débit (req/s)", title="Débit selon la charge")
    ax2.set(xlabel="Utilisateurs simultanés", ylabel="Latence p95 (ms)", title="Latence p95 selon la charge")
    ax2.set_yscale("log")
    for ax in (ax1, ax2):
        ax.legend()
    fig.tight_layout()
    fig.savefig(figdir / "load_curves.png")
    plt.close(fig)


def fig_timeline(run: dict, figdir: Path) -> None:
    h, p, m = run["hist"], run["prom"].copy(), run["meta"]
    t0 = m["t_start"]
    p["t"] = p["timestamp"] - t0
    fig, axes = plt.subplots(4, 1, figsize=(11, 10), sharex=True)
    axes[0].plot(h["Timestamp"] - t0, h["User Count"], color="tab:gray", label="utilisateurs")
    axes[0].set_ylabel("Utilisateurs")
    ax0b = axes[0].twinx()
    ax0b.plot(h["Timestamp"] - t0, h["Requests/s"], color="tab:blue", label="req/s")
    ax0b.set_ylabel("req/s")
    axes[1].plot(p["t"], p["replicas_desired"], "--", label="pods voulus")
    axes[1].plot(p["t"], p["replicas_available"], label="pods prêts")
    axes[1].plot(p["t"], p["nodes_ready"], ":", label="nœuds prêts")
    axes[1].set_ylabel("Nombre")
    axes[1].legend(loc="upper left")
    axes[2].plot(h["Timestamp"] - t0, h["95%"], color="tab:red", label="p95 client")
    axes[2].set_ylabel("p95 (ms)")
    axes[2].set_yscale("log")
    axes[3].plot(p["t"], p["cpu_cores"], label="CPU total utilisé (cœurs)")
    axes[3].plot(p["t"], p["cpu_request_per_pod"], label="CPU demandé / pod (cœurs)")
    axes[3].set_ylabel("vCPU")
    axes[3].legend(loc="upper left")
    axes[3].set_xlabel("Temps depuis le début (s)")
    load_end = m["t_load_end"] - t0
    for ax in axes:
        ax.axvline(load_end, color="k", alpha=0.3)
    fig.suptitle(f"{m['title']} — {m['config']['label']} (rép. {m['repetition']})")
    fig.tight_layout()
    fig.savefig(figdir / f"timeline_{run['dir'].name}.png")
    plt.close(fig)


def fig_usl(fit: dict, figdir: Path) -> None:
    n = np.linspace(1, max(fit["n"].max() * 1.5, 16), 200)
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.plot(fit["n"], fit["x"], "o", label="mesuré")
    ax.plot(n, usl(n, fit["lambda"], fit["alpha"], fit["beta"]), label=f"USL α={fit['alpha']:.3f} β={fit['beta']:.5f}")
    ax.plot(n, fit["lambda"] * n, ":", color="gray", label="linéaire idéal")
    ax.set(xlabel="Nombre de pods N", ylabel="Débit max (req/s)", title="Scalabilité horizontale (USL)")
    ax.set_ylim(0, max(fit["x"]) * 1.6)
    ax.legend()
    fig.tight_layout()
    fig.savefig(figdir / "usl.png")
    plt.close(fig)


def fig_bars(agg: pd.DataFrame, figdir: Path) -> None:
    last = agg.loc[agg.groupby("config", sort=False)["users"].idxmax()]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))
    ax1.bar(last["config"], last["throughput_rps_mean"], yerr=last["throughput_rps_ci95"], capsize=4)
    ax1.set(ylabel="Débit (req/s)", title="Débit à charge max")
    ax2.bar(last["config"], last["p95_ms_mean"], yerr=last["p95_ms_ci95"], capsize=4, color="tab:red")
    ax2.set(ylabel="p95 (ms)", title="Latence p95 à charge max")
    fig.tight_layout()
    fig.savefig(figdir / "comparison.png")
    plt.close(fig)


# --------------------------------------------------------------------------- main
def main(exp_dir: Path) -> None:
    runs = [load_run(d) for d in exp_dir.iterdir() if (d / "meta.json").exists()]
    runs.sort(key=lambda r: (r["meta"]["config"]["replicas"], r["meta"]["config"]["label"], r["meta"]["repetition"]))
    if not runs:
        sys.exit(f"Aucun run dans {exp_dir}")
    figdir = exp_dir / "figures"
    figdir.mkdir(exist_ok=True)
    meta = runs[0]["meta"]

    steps = pd.concat([steady_steps(r) for r in runs], ignore_index=True)
    steps.to_csv(exp_dir / "steps.csv", index=False)
    agg = aggregate(steps)
    # Loi du temps de réponse interactif (modèle fermé) : N = X · (R + Z)
    agg["little_N_pred"] = agg["throughput_rps_mean"] * (agg["avg_ms_mean"] / 1000 + THINK_TIME_S)

    fig_load_curves(agg, figdir)
    if meta["overlay"] != "fixed":  # chronogrammes utiles seulement quand un autoscaler agit
        for r in runs:
            fig_timeline(r, figdir)

    md = [f"# {meta['title']}", "", f"**Question :** {meta['question']}", "",
          f"Runs : {len(runs)} · paliers : {meta['steps']} utilisateurs × {meta['step_seconds']} s · "
          f"profil `{meta['profile']}` · cluster : {meta['cluster']}", "",
          "## Résultats par palier (moyenne ± IC 95 %)", "",
          "| Config | Utilisateurs | Débit (req/s) | p95 (ms) | p99 (ms) | Erreurs % | Pods | Throttling % | N prédit (Little) |",
          "|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for _, r in agg.iterrows():
        md.append(
            f"| {r['config']} | {r['users']} | {r['throughput_rps_mean']:.1f} ± {r['throughput_rps_ci95']:.1f} "
            f"| {r['p95_ms_mean']:.0f} ± {r['p95_ms_ci95']:.0f} | {r['p99_ms_mean']:.0f} | {r['error_rate_pct_mean']:.2f} "
            f"| {r['replicas_avail_mean']:.1f} | {r['throttled_pct_mean']:.0f} | {r['little_N_pred']:.0f} |"
        )

    if meta["overlay"] == "horizontal":
        react = pd.concat([hpa_reaction(r).assign(rep=r["meta"]["repetition"]) for r in runs], ignore_index=True)
        react.to_csv(exp_dir / "hpa_reaction.csv", index=False)
        md += ["", "## Réactivité du HPA", "",
               f"- Délai de décision moyen : **{react['decision_s'].mean():.0f} s** (± {ci95(react['decision_s']):.0f})",
               f"- Délai jusqu'aux pods prêts : **{react['ready_s'].mean():.0f} s** (± {ci95(react['ready_s']):.0f})",
               "- Pics à > 60 s = pods en attente d'un nouveau nœud (Cluster Autoscaler)."]  # fmt: skip

    if meta["scenario"].startswith("e3"):
        fit = fit_usl(steps)
        if fit:
            fig_usl(fit, figdir)
            md += ["", "## Universal Scalability Law", "",
                   f"- λ (débit d'1 pod) = {fit['lambda']:.1f} req/s",
                   f"- α (contention) = {fit['alpha']:.4f}, β (cohérence) = {fit['beta']:.6f}",
                   f"- Pic de scalabilité théorique N* = √((1−α)/β) ≈ **{fit['n_star']:.0f} pods**"]  # fmt: skip

    if len(agg["config"].unique()) > 1:
        fig_bars(agg, figdir)

    md += ["", "## Figures", ""] + [f"![{f.stem}](figures/{f.name})" for f in sorted(figdir.glob("*.png"))]
    (exp_dir / "summary.md").write_text("\n".join(md) + "\n")
    print(f"✅ {exp_dir / 'summary.md'}  ({len(list(figdir.glob('*.png')))} figures)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]))
