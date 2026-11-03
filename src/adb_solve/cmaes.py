"""Minimal full-covariance CMA-ES, dependency free.

The (μ/μ_w, λ) scheme from Hansen's tutorial, without numpy: a cyclic Jacobi
eigen-decomposition of the covariance matrix is cheap at the sizes this tool
sees (≈15 dimensions) and keeps the package to the standard library.

Callers are expected to work in a transformed space (``log(x/x0)``), so the
sampler never proposes negative values; clamping to the search box is the
caller's job (see ``adb_solve.optimize.repair``).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Sequence


def jacobi(matrix: Sequence[Sequence[float]], sweeps: int = 60) -> tuple[list[float], list[list[float]]]:
    """Eigenvalues and eigenvectors (columns) of a symmetric matrix."""
    n = len(matrix)
    a = [list(row) for row in matrix]
    v = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for _ in range(sweeps):
        off = sum(a[i][j] * a[i][j] for i in range(n) for j in range(n) if i != j)
        if off < 1e-24:
            break
        for p in range(n - 1):
            for q in range(p + 1, n):
                if abs(a[p][q]) < 1e-18:
                    continue
                theta = (a[q][q] - a[p][p]) / (2.0 * a[p][q])
                t = math.copysign(1.0, theta) / (abs(theta) + math.sqrt(theta * theta + 1.0))
                c = 1.0 / math.sqrt(t * t + 1.0)
                s = t * c
                for k in range(n):
                    akp, akq = a[k][p], a[k][q]
                    a[k][p] = c * akp - s * akq
                    a[k][q] = s * akp + c * akq
                for k in range(n):
                    apk, aqk = a[p][k], a[q][k]
                    a[p][k] = c * apk - s * aqk
                    a[q][k] = s * apk + c * aqk
                for k in range(n):
                    vkp, vkq = v[k][p], v[k][q]
                    v[k][p] = c * vkp - s * vkq
                    v[k][q] = s * vkp + c * vkq
    return [a[i][i] for i in range(n)], v


def _outer(column: Sequence[float]) -> list[list[float]]:
    return [[a * b for b in column] for a in column]


def _matmul(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]]) -> list[list[float]]:
    n = len(a)
    return [[sum(a[i][k] * b[k][j] for k in range(n)) for j in range(n)] for i in range(n)]


def _transpose(a: Sequence[Sequence[float]]) -> list[list[float]]:
    n = len(a)
    return [[a[j][i] for j in range(n)] for i in range(n)]


@dataclass
class CMA:
    """Full-covariance CMA-ES state; ``ask`` then ``tell`` per generation."""

    dimension: int
    sigma: float = 0.3
    lam: int | None = None
    mean: list[float] = field(default_factory=list)
    generation: int = 0

    def __post_init__(self) -> None:
        n = self.dimension
        if n < 1:
            raise ValueError("dimension must be positive")
        if not self.mean:
            self.mean = [0.0] * n
        self.lam = self.lam or 4 + int(3.0 * math.log(n))
        self.mu = self.lam // 2
        raw = [math.log(self.mu + 0.5) - math.log(i + 1) for i in range(self.mu)]
        total = sum(raw)
        self.weights = [w / total for w in raw]
        self.mu_eff = 1.0 / sum(w * w for w in self.weights)
        self.cc = (4.0 + self.mu_eff / n) / (n + 4.0 + 2.0 * self.mu_eff / n)
        self.cs = (self.mu_eff + 2.0) / (n + self.mu_eff + 5.0)
        self.c1 = 2.0 / ((n + 1.3) ** 2 + self.mu_eff)
        self.cmu = min(1.0 - self.c1, 2.0 * (self.mu_eff - 2.0 + 1.0 / self.mu_eff) / ((n + 2.0) ** 2 + self.mu_eff))
        self.damps = 1.0 + 2.0 * max(0.0, math.sqrt((self.mu_eff - 1.0) / (n + 1.0)) - 1.0) + self.cs
        self.chi_n = math.sqrt(n) * (1.0 - 1.0 / (4.0 * n) + 1.0 / (21.0 * n * n))
        self.cov = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
        self.pc = [0.0] * n
        self.ps = [0.0] * n
        self._basis: list[list[float]] = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
        self._scales: list[float] = [1.0] * n

    # -- sampling ----------------------------------------------------------
    def _refresh(self) -> None:
        values, basis = jacobi(self.cov)
        self._scales = [math.sqrt(max(value, 1e-20)) for value in values]
        self._basis = basis

    def ask(self, rng: random.Random) -> list[list[float]]:
        """λ candidate points in the transformed space."""
        self._refresh()
        n = self.dimension
        lam = self.lam or 1
        samples: list[list[float]] = []
        for _ in range(lam):
            z = [rng.gauss(0.0, 1.0) for _ in range(n)]
            scaled = [self._scales[j] * z[j] for j in range(n)]
            y = [sum(self._basis[i][j] * scaled[j] for j in range(n)) for i in range(n)]
            samples.append([self.mean[i] + self.sigma * y[i] for i in range(n)])
        return samples

    # -- update ------------------------------------------------------------
    def tell(self, samples: Sequence[Sequence[float]], losses: Sequence[float]) -> None:
        if len(samples) != len(losses):
            raise ValueError("samples and losses must have the same length")
        n = self.dimension
        order = sorted(range(len(samples)), key=lambda i: (losses[i], i))
        mu = self.mu or 1
        old_mean = list(self.mean)
        self.mean = [
            sum(self.weights[k] * samples[order[k]][i] for k in range(mu)) for i in range(n)
        ]
        y_w = [(self.mean[i] - old_mean[i]) / self.sigma for i in range(n)]

        inv_sqrt = [
            [sum(self._basis[i][k] / self._scales[k] * self._basis[j][k] for k in range(n)) for j in range(n)]
            for i in range(n)
        ]
        ps = [
            (1.0 - self.cs) * self.ps[i]
            + math.sqrt(self.cs * (2.0 - self.cs) * self.mu_eff) * sum(inv_sqrt[i][j] * y_w[j] for j in range(n))
            for i in range(n)
        ]
        norm_ps = math.sqrt(sum(value * value for value in ps))
        hsig = norm_ps / math.sqrt(1.0 - (1.0 - self.cs) ** (2.0 * (self.generation + 1))) / self.chi_n < (
            1.4 + 2.0 / (n + 1.0)
        )
        self.pc = [
            (1.0 - self.cc) * self.pc[i]
            + (1.0 if hsig else 0.0) * math.sqrt(self.cc * (2.0 - self.cc) * self.mu_eff) * y_w[i]
            for i in range(n)
        ]

        rank_mu = [[0.0] * n for _ in range(n)]
        for k in range(mu):
            elite = samples[order[k]]
            y_k = [(elite[i] - old_mean[i]) / self.sigma for i in range(n)]
            weight = self.weights[k]
            for i in range(n):
                for j in range(n):
                    rank_mu[i][j] += weight * y_k[i] * y_k[j]
        rank_one = _outer(self.pc)
        decay = 1.0 - self.c1 - self.cmu
        correction = 0.0 if hsig else self.cc * (2.0 - self.cc)
        self.cov = [
            [
                decay * self.cov[i][j]
                + self.c1 * (rank_one[i][j] + correction * self.cov[i][j])
                + self.cmu * rank_mu[i][j]
                for j in range(n)
            ]
            for i in range(n)
        ]
        # Keep C symmetric against accumulated round-off.
        for i in range(n):
            for j in range(i + 1, n):
                mean = 0.5 * (self.cov[i][j] + self.cov[j][i])
                self.cov[i][j] = self.cov[j][i] = mean

        self.sigma *= math.exp((self.cs / self.damps) * (norm_ps / self.chi_n - 1.0))
        self.sigma = min(max(self.sigma, 1e-8), 1e3)
        self.ps = ps
        self.generation += 1
