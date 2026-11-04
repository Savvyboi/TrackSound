"""Scoring machinery of the lightning solver (vectorised over grid points).

Observation model for thunder onset k (recording time ``a_k``) from strike j (UTC ``t_j``,
catalogued at ``p_j`` with 1-sigma error ``sigma_j``), listener at x, clock T = UTC of
recording time 0, sound speed c::

    r_kj = a_k + T - t_j - |x - p_j| / c                 (residual, s)
    sigma_late^2  = sigma_k^2 + (sigma_j / c)^2 + (0.01 |x - p_j| / c)^2 + sigma_grid^2 + 0.05^2
    sigma_early^2 = sigma_late^2 + (early_j / c)^2       (first arrival from the nearest part
                                                          of the channel: onsets come early)
    K_kj = exp(-u^2 / 2) * taper(|x - p_j|),  u = r / sigma_late (r >= 0) or r / sigma_early

Score (a robust soft count of explained onsets)::

    S = min( sum_k max_j K_kj , sum_j max_k K_kj )  -  0.5 * sum_j missed_j

The ``min`` of the two sums stops one strike explaining two onsets (or one onset two
strikes). ``missed_j`` (0..1) is large for a strike that is surely close (within 3-8 km even
allowing for its position error), whose thunder should have started inside the recording,
but which has no onset within a few seconds of its predicted arrival ("silence" evidence).
``taper`` softly ignores strikes beyond ``max_range``. ``sigma_grid`` widens the tolerance on
coarse grids, where the true position can be up to half a cell away.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Iterator

import numpy as np

from .catalogue import StrikeArrays
from .geo import LocalFrame

SIGMA_REL = 0.01          # relative travel-time error (sound speed, wind)
SIGMA_FLOOR_S = 0.05
MATCH_U = 2.0             # |u| <= 2 counts as a match
MATCH_K = math.exp(-0.5 * MATCH_U ** 2)
MISS_WEIGHT = 0.5         # per missed close strike, when onsets come from the detector (complete)
MISS_WEIGHT_MANUAL = 0.1  # when every onset was picked by hand (the user may mark only a few)
MISS_FULL_M = 3000.0      # strikes this close (pessimistically) must be heard ...
MISS_ZERO_M = 8000.0      # ... and beyond this they may well be inaudible
MISS_TOL_S = 2.0          # an onset within ~2 s of the predicted arrival counts as heard
EDGE_MARGIN_S = 3.0       # onsets cannot be detected right at the recording edges
MAX_ELEMS = 1_500_000     # grid points x pairs per vectorised block


@dataclass
class PointScores:
    score: np.ndarray
    n_matched: np.ndarray
    dT: np.ndarray            # mean-shift step for the clock (s)
    penalty: np.ndarray


class Problem:
    """Onsets, strikes and constants of one solve, projected into a local frame."""

    def __init__(self, onset_t, onset_sigma, onset_ids, strikes: StrikeArrays, c: float, duration_s: float,
                 frame: LocalFrame, max_range_m: float, miss_weight: float = MISS_WEIGHT):
        o = np.argsort(np.asarray(onset_t, dtype=np.float64), kind="stable")
        self.a = np.asarray(onset_t, dtype=np.float64)[o]
        self.sk = np.maximum(np.asarray(onset_sigma, dtype=np.float64)[o], 0.02)
        self.onset_ids = [onset_ids[i] for i in o]
        self.strikes = strikes
        self.st = strikes.t
        self.sy, self.su = frame.to_yu(strikes.lat, strikes.lon)
        self.c = float(c)
        self.sp = strikes.sigma_m / self.c
        self.se = strikes.early_m / self.c
        self.D = float(duration_s)
        self.frame = frame
        self.R = float(max_range_m)
        self.taper_w = max(300.0, 0.03 * self.R)
        self.R_eff = self.R + 4.0 * self.taper_w
        self.miss_weight = float(miss_weight)

    @property
    def K(self) -> int:
        return len(self.a)

    def taper(self, d: np.ndarray) -> np.ndarray:
        z = np.clip((d - self.R) / self.taper_w, -50.0, 50.0)
        return 1.0 / (1.0 + np.exp(z))

    # ---- pruning ------------------------------------------------------------------------------
    def strikes_near(self, gy: np.ndarray, gu: np.ndarray, extra_m: float, t_lo: float = -np.inf,
                     t_hi: float = np.inf) -> np.ndarray:
        """Indices of strikes within extra_m of the bounding box of the points, in [t_lo, t_hi]."""
        a = int(np.searchsorted(self.st, t_lo, side="left"))
        b = int(np.searchsorted(self.st, t_hi, side="right"))
        if b <= a or len(gy) == 0:
            return np.zeros(0, dtype=np.int64)
        idx = np.arange(a, b)
        y0, y1 = float(gy.min()) - extra_m, float(gy.max()) + extra_m
        k = float(np.min(self.frame.east_scale(np.array([y0, y1]))))
        du = extra_m / max(k, 1e-6)
        u0, u1 = float(gu.min()) - du, float(gu.max()) + du
        sy, su = self.sy[idx], self.su[idx]
        return idx[(sy >= y0) & (sy <= y1) & (su >= u0) & (su <= u1)]

    def pairs(self, j_idx: np.ndarray, dt_lo: float, dt_hi: float) -> tuple[np.ndarray, np.ndarray]:
        """(onset index, strike index) pairs with t_j - a_k in [dt_lo, dt_hi]; sorted by onset."""
        if len(j_idx) == 0 or self.K == 0:
            return np.zeros(0, np.int64), np.zeros(0, np.int64)
        ts = self.st[j_idx]                          # j_idx is time-sorted (strikes are)
        lo = np.searchsorted(ts, self.a + dt_lo, side="left")
        hi = np.searchsorted(ts, self.a + dt_hi, side="right")
        cnt = np.maximum(hi - lo, 0)
        total = int(cnt.sum())
        if total == 0:
            return np.zeros(0, np.int64), np.zeros(0, np.int64)
        pk = np.repeat(np.arange(self.K), cnt)
        starts = np.repeat(lo - np.concatenate([[0], np.cumsum(cnt)[:-1]]), cnt)
        return pk, j_idx[np.arange(total) + starts]

    def sigma_bound(self, sig_grid: float) -> float:
        """Largest early-side sigma any pair can have (for pair pruning)."""
        sp = float(self.sp.max()) if len(self.sp) else 0.0
        se = float(self.se.max()) if len(self.se) else 0.0
        sk = float(self.sk.max()) if self.K else 0.0
        return math.sqrt(sk ** 2 + sp ** 2 + (SIGMA_REL * self.R_eff / self.c) ** 2 + sig_grid ** 2
                         + SIGMA_FLOOR_S ** 2 + se ** 2)

    def _block_pairs(self, y, u, T_lo, T_hi, smax):
        j_idx = self.strikes_near(y, u, self.R_eff, T_lo - smax - 1.0, T_hi + smax + self.R_eff / self.c + self.D + 1.0)
        return self.pairs(j_idx, T_lo - smax - self.R_eff / self.c, T_hi + smax)

    def _blocks(self, idx: np.ndarray, gy, gu, pair_fn: Callable, per_point_extra: int = 0
                ) -> Iterator[tuple[np.ndarray, np.ndarray, np.ndarray]]:
        """Consecutive runs of the points ``idx`` (keep them spatially ordered) with their
        pairs, sized so that points x (pairs + extra) stays under MAX_ELEMS."""
        s, per = 0, 2048
        n = len(idx)
        while s < n:
            e = min(n, s + per)
            sel = idx[s:e]
            pk, pj = pair_fn(gy[sel], gu[sel])
            cost = len(pk) + per_point_extra + 1
            if e - s > 1 and cost * (e - s) > MAX_ELEMS:
                per = max(1, MAX_ELEMS // cost)
                continue
            yield sel, pk, pj
            s = e
            per = max(1, min(4096, int(1.5 * MAX_ELEMS // cost)))

    # ---- kernel ----------------------------------------------------------------------------------
    def _dist(self, gy, gu, pj) -> np.ndarray:
        js, inv = np.unique(pj, return_inverse=True)
        d = self.frame.dist(gy[:, None], gu[:, None], self.sy[js][None, :], self.su[js][None, :])
        return d[:, inv]

    def _sigmas(self, d, pk, pj, sig_grid):
        tt = d / self.c
        base = (self.sk[pk] ** 2 + self.sp[pj] ** 2 + sig_grid ** 2 + SIGMA_FLOOR_S ** 2)[None, :] + (SIGMA_REL * tt) ** 2
        return np.sqrt(base), np.sqrt(base + (self.se[pj] ** 2)[None, :])

    def _kernel(self, d, r, pk, pj, sig_grid):
        s_late, s_early = self._sigmas(d, pk, pj, sig_grid)
        sig = np.where(r >= 0, s_late, s_early)
        u = r / sig
        return np.exp(-0.5 * u * u) * self.taper(d), sig

    # ---- clock votes --------------------------------------------------------------------------
    def vote_clock(self, gy, gu, T_lo: float, T_hi: float, sig_grid: float, bin_w: float,
                   n_peaks: int = 1) -> tuple[np.ndarray, np.ndarray]:
        """Best clock values per point by box voting (a Hough transform over T).

        Every (onset, strike) pair implies ``T = t_j + d/c - a_k`` and votes for the interval
        of T where it would match within one sigma. Returns (T, votes), shape (n, n_peaks);
        T is NaN where a point has no votes. Peaks are at least ~3 sigma apart.
        """
        n = len(gy)
        outT = np.full((n, n_peaks), np.nan)
        outV = np.zeros((n, n_peaks))
        if n == 0 or self.K == 0 or T_hi <= T_lo:
            return outT, outV
        smax = self.sigma_bound(sig_grid)
        n_bins = max(1, int(math.ceil((T_hi - T_lo) / bin_w)))
        sep = max(1, int(round(3.0 * smax / bin_w)))
        stride = n_bins + 1
        pair_fn = lambda y, u: self._block_pairs(y, u, T_lo, T_hi, smax)   # noqa: E731
        for sel, pk, pj in self._blocks(np.arange(n), gy, gu, pair_fn, per_point_extra=stride):
            if len(pk) == 0:
                continue
            y, u = gy[sel], gu[sel]
            d = self._dist(y, u, pj)
            T_imp = self.st[pj][None, :] + d / self.c - self.a[pk][None, :]
            s_late, s_early = self._sigmas(d, pk, pj, sig_grid)
            w = self.taper(d)
            b0 = np.floor((T_imp - s_early - T_lo) / bin_w).astype(np.int64)
            b1 = np.floor((T_imp + s_late - T_lo) / bin_w).astype(np.int64) + 1
            ok = (b1 > 0) & (b0 < n_bins) & (w > 1e-3)
            b0 = np.clip(b0, 0, n_bins)
            b1 = np.clip(b1, 0, n_bins)
            rows = np.broadcast_to(np.arange(len(sel))[:, None], d.shape)
            size = len(sel) * stride
            h = (np.bincount((rows * stride + b0)[ok], weights=w[ok], minlength=size)
                 - np.bincount((rows * stride + b1)[ok], weights=w[ok], minlength=size))
            h = np.cumsum(h.reshape(len(sel), stride), axis=1)[:, :n_bins]
            cols = np.arange(n_bins)[None, :]
            for p in range(n_peaks):
                i = np.argmax(h, axis=1)
                v = h[np.arange(len(sel)), i]
                good = v > 1e-6
                outT[sel, p] = np.where(good, T_lo + (i + 0.5) * bin_w, np.nan)
                outV[sel, p] = np.where(good, v, 0.0)
                if p + 1 < n_peaks:
                    h = np.where(np.abs(cols - i[:, None]) <= sep, -np.inf, h)
        return outT, outV

    # ---- scoring ---------------------------------------------------------------------------------
    def evaluate(self, gy, gu, T, sig_grid: float) -> PointScores:
        """Score, matched count, clock mean-shift step and silence penalty at points with clocks T."""
        n = len(gy)
        score, dT, pen = np.zeros(n), np.zeros(n), np.zeros(n)
        nm = np.zeros(n, dtype=np.int64)
        T = np.asarray(T, dtype=np.float64)
        valid = np.isfinite(T)
        if n == 0 or not valid.any():
            return PointScores(score, nm, dT, pen)
        smax = 3.0 * self.sigma_bound(sig_grid)
        # group points by clock (buckets) so each block has a small pair set; keep spatial order within
        bucket_w = max(60.0, 2.0 * smax)
        vidx = np.flatnonzero(valid)
        tb = np.floor(T[vidx] / bucket_w)
        vidx = vidx[np.lexsort((vidx, tb))]
        tb = np.floor(T[vidx] / bucket_w)
        cuts = np.flatnonzero(np.diff(tb)) + 1
        for run in np.split(vidx, cuts):
            T_lo, T_hi = float(T[run].min()), float(T[run].max())
            pair_fn = lambda y, u: self._block_pairs(y, u, T_lo, T_hi, smax)   # noqa: E731
            for sel, pk, pj in self._blocks(run, gy, gu, pair_fn):
                y, u, Tc = gy[sel], gu[sel], T[sel]
                if len(pk) and self.K:
                    d = self._dist(y, u, pj)
                    r = self.a[pk][None, :] + Tc[:, None] - self.st[pj][None, :] - d / self.c
                    Km, sig = self._kernel(d, r, pk, pj, sig_grid)
                    ks = np.flatnonzero(np.r_[True, pk[1:] != pk[:-1]])
                    max_k = np.maximum.reduceat(Km, ks, axis=1)
                    perm = np.argsort(pj, kind="stable")
                    pjs = pj[perm]
                    js = np.flatnonzero(np.r_[True, pjs[1:] != pjs[:-1]])
                    max_j = np.maximum.reduceat(Km[:, perm], js, axis=1)
                    score[sel] = np.minimum(max_k.sum(axis=1), max_j.sum(axis=1))
                    nm[sel] = np.minimum((max_k >= MATCH_K).sum(axis=1), (max_j >= MATCH_K).sum(axis=1))
                    w = Km / (sig * sig)
                    sw = w.sum(axis=1)
                    dT[sel] = np.where(sw > 0, -(w * r).sum(axis=1) / np.maximum(sw, 1e-300), 0.0)
                pen[sel] = self.silence_penalty(y, u, Tc, sig_grid)
        score -= pen
        return PointScores(score, nm, dT, pen)

    def silence_penalty(self, gy, gu, T, sig_grid: float) -> np.ndarray:
        """miss_weight x (number of surely-close strikes whose thunder should be in the recording
        but has no onset within ~2 s of its predicted arrival)."""
        n = len(gy)
        out = np.zeros(n)
        if n == 0 or self.miss_weight <= 0:
            return out
        T_lo, T_hi = float(np.min(T)), float(np.max(T))
        j = self.strikes_near(gy, gu, MISS_ZERO_M, T_lo + EDGE_MARGIN_S - MISS_ZERO_M / self.c - 1.0,
                              T_hi + self.D - EDGE_MARGIN_S + 1.0)
        j = j[self.strikes.sigma_m[j] < MISS_ZERO_M - MISS_FULL_M] if len(j) else j
        if len(j) == 0:
            return out
        d = self.frame.dist(gy[:, None], gu[:, None], self.sy[j][None, :], self.su[j][None, :])
        cw = np.clip((MISS_ZERO_M - (d + self.strikes.sigma_m[j][None, :])) / (MISS_ZERO_M - MISS_FULL_M), 0.0, 1.0)
        if not (cw > 0).any():
            return out
        tau = self.st[j][None, :] + d / self.c - T[:, None]
        iw = np.clip((tau - EDGE_MARGIN_S) / 2.0, 0.0, 1.0) * np.clip((self.D - EDGE_MARGIN_S - tau) / 2.0, 0.0, 1.0)
        if self.K:
            idx = np.searchsorted(self.a, tau)
            prev = self.a[np.clip(idx - 1, 0, self.K - 1)]
            nxt = self.a[np.clip(idx, 0, self.K - 1)]
            rmin = np.minimum(np.abs(tau - prev), np.abs(tau - nxt))
            sw2 = MISS_TOL_S ** 2 + (self.sp[j] ** 2)[None, :] + sig_grid ** 2
            heard = np.exp(-0.5 * rmin * rmin / sw2)
        else:
            heard = np.zeros_like(tau)
        return self.miss_weight * (cw * iw * (1.0 - heard)).sum(axis=1)

    def missed_strikes(self, y: float, u: float, T: float) -> list[int]:
        """Strike indices that count as 'should have been heard but were not' at one point."""
        gy, gu = np.array([y]), np.array([u])
        j = self.strikes_near(gy, gu, MISS_ZERO_M, T + EDGE_MARGIN_S - MISS_ZERO_M / self.c - 1.0,
                              T + self.D - EDGE_MARGIN_S + 1.0)
        out = []
        for jj in j:
            d = float(self.frame.dist(y, u, self.sy[jj], self.su[jj]))
            if d + self.strikes.sigma_m[jj] > 0.5 * (MISS_FULL_M + MISS_ZERO_M):
                continue
            tau = self.st[jj] + d / self.c - T
            if not (EDGE_MARGIN_S <= tau <= self.D - EDGE_MARGIN_S):
                continue
            if self.K == 0 or float(np.min(np.abs(self.a - tau))) > 2.0 * MISS_TOL_S:
                out.append(int(jj))
        return out

    # ---- one point in detail ---------------------------------------------------------------------
    def matches_at(self, y: float, u: float, T: float) -> list[dict]:
        """One-to-one matches at a point: [{k, j, r, d, sigma, K}] with K >= MATCH_K, by onset time."""
        if self.K == 0:
            return []
        smax = 3.0 * self.sigma_bound(0.0)
        gy, gu = np.array([y]), np.array([u])
        pk, pj = self._block_pairs(gy, gu, T, T, smax)
        if len(pk) == 0:
            return []
        d = self._dist(gy, gu, pj)
        r = self.a[pk][None, :] + T - self.st[pj][None, :] - d / self.c
        Kv, sig = self._kernel(d, r, pk, pj, 0.0)
        Kv, sig, d, r = Kv[0], sig[0], d[0], r[0]
        used_k, used_j, out = set(), set(), []
        for p in np.argsort(-Kv, kind="stable"):
            if Kv[p] < MATCH_K:
                break
            k, jj = int(pk[p]), int(pj[p])
            if k in used_k or jj in used_j:
                continue
            used_k.add(k)
            used_j.add(jj)
            out.append({"k": k, "j": jj, "r": float(r[p]), "d": float(d[p]), "sigma": float(sig[p]), "K": float(Kv[p])})
        out.sort(key=lambda m: self.a[m["k"]])
        return out

    def polish(self, y: float, u: float, T: float, T_prior: tuple[float, float] | None,
               iters: int = 10) -> tuple[float, float, float, np.ndarray | None, list[dict]]:
        """Weighted least squares on the matched pairs (Gauss-Newton on y, u, T, Huber weights).

        Returns (y, u, T, covariance of (north m, east m, T s) or None, matches used). Without a
        clock prior it needs at least 4 matches (3 unknowns plus one to check the fit).
        """
        m = self.matches_at(y, u, T)
        if len(m) < (2 if T_prior is not None else 4):
            return y, u, T, None, m
        k = np.array([x["k"] for x in m])
        j = np.array([x["j"] for x in m])
        p = np.array([y, u, T], dtype=np.float64)
        scale = float(self.frame.east_scale(np.array([y]))[0])

        def resid(q):
            d = self.frame.dist(q[0], q[1], self.sy[j], self.su[j])
            r = self.a[k] + q[2] - self.st[j] - d / self.c
            base = self.sk[k] ** 2 + self.sp[j] ** 2 + SIGMA_FLOOR_S ** 2 + (SIGMA_REL * d / self.c) ** 2
            z = r / np.where(r >= 0, np.sqrt(base), np.sqrt(base + self.se[j] ** 2))
            if T_prior is not None:
                z = np.append(z, (q[2] - T_prior[0]) / T_prior[1])
            return z

        steps = np.array([1.0, 1.0 / max(scale, 1e-6), 1e-3])

        def jac(q, z):
            J = np.empty((len(z), 3))
            for c in range(3):
                qq = q.copy()
                qq[c] += steps[c]
                J[:, c] = (resid(qq) - z) / steps[c]
            return J

        lim = np.array([2000.0, 2000.0 / max(scale, 1e-6), 10.0])
        for _ in range(iters):
            z = resid(p)
            J = jac(p, z)
            w = np.where(np.abs(z) <= 1.5, 1.0, 1.5 / np.maximum(np.abs(z), 1e-9))
            JW = J * w[:, None]
            try:
                delta = -np.linalg.solve(JW.T @ J + 1e-9 * np.eye(3), JW.T @ z)
            except np.linalg.LinAlgError:
                return y, u, T, None, m
            delta = np.clip(delta, -lim, lim)
            p = p + delta
            if np.all(np.abs(delta) < np.array([0.5, 0.5 / max(scale, 1e-6), 1e-3])):
                break
        z = resid(p)
        J = jac(p, z)
        try:
            C = np.linalg.inv(J.T @ J + 1e-12 * np.eye(3))
        except np.linalg.LinAlgError:
            return float(p[0]), float(p[1]), float(p[2]), None, m
        dof = max(1, len(z) - 3)
        C *= max(1.0, float(np.sum(z * z)) / dof)
        S = np.diag([1.0, scale, 1.0])
        return float(p[0]), float(p[1]), float(p[2]), S @ C @ S, m
