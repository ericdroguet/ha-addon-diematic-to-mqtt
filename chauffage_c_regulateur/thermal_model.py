"""Circuit C: calibrated hydraulic context, never writes to Home Assistant."""
from collections import deque
from statistics import mean
from math import isfinite


class HydraulicModel:
    """One-hour contextual reference, calibrated on 119 hours, Nov 21-25 2026.

    The reference is conditional, not a heat-output estimate. Coefficients stay
    fixed until more independent data are available; inputs adapt continuously.
    """
    def __init__(self):
        self.samples = deque()

    def reset(self):
        self.samples.clear()

    def observe(self, now, snapshot, rooms, outdoor):
        if len(rooms) != 12 or outdoor is None or not isfinite(outdoor):
            self.reset()
            return
        if not snapshot or snapshot[1] != "ok":
            return
        stamp, _, departure, returned, _ = snapshot
        if not 0 <= now - stamp <= 150 or departure is None or returned is None:
            return
        delta = departure - returned
        if not -20 <= delta <= 20:
            return
        signature = tuple(sorted((name, target) for name, current, target in rooms))
        gaps = tuple(target - current for name, current, target in sorted(rooms))
        if self.samples and (signature != self.samples[-1][1] or stamp - self.samples[-1][0] > 600):
            self.reset()
        if self.samples and stamp <= self.samples[-1][0]:
            return
        n = sum(g >= 0.3 - 1e-6 for g in gaps)
        deficit = sum(max(0.0, g) for g in gaps)
        self.samples.append((stamp, signature, gaps, n, deficit, outdoor, departure, delta))
        while self.samples and stamp - self.samples[0][0] > 3600:
            self.samples.popleft()

    def evaluate(self, now, rooms, forecast, persistence):
        info = {}
        if len(rooms) != 12 or not self.samples:
            return None, "modele_zones", info
        a = list(self.samples)
        signature = tuple(sorted((name, target) for name, current, target in rooms))
        if signature != a[-1][1] or not 0 <= now - a[-1][0] <= 150:
            return None, "modele_transition", info
        coverage = sum(b[0] - c[0] for c, b in zip(a, a[1:]) if 0 < b[0] - c[0] <= 100) / 3600
        info["model_coverage"] = round(100 * coverage, 1)
        if coverage < 0.8:
            return None, "modele_historique", info
        end = a[-1][0]
        early = [x for x in a if x[0] <= end - 2700]
        late = [x for x in a if x[0] >= end - 900]
        if len(early) < 5 or len(late) < 5:
            return None, "modele_tendance", info
        n, outdoor, departure, delta = (mean(x[k] for x in a) for k in (3, 5, 6, 7))
        # Avoid extrapolation beyond the observed hourly training domain.
        if not (0 <= n <= 5 and 7.5 <= outdoor <= 12 and 36 <= departure <= 56):
            return None, "modele_hors_domaine", info
        expected = 3.512 + 0.223 * (n - 1.5) - 0.003 * (outdoor - 9.5) + 0.067 * (departure - 48)
        residual = delta - expected
        trend = mean(x[7] for x in late) - mean(x[7] for x in early)
        demand_trend = mean(x[4] for x in late) - mean(x[4] for x in early)
        worst_worsening = max(mean(x[2][i] for x in late) - mean(x[2][i] for x in early) for i in range(12))
        gaps = [target - current for _, current, target in rooms]
        max_gap = max(gaps)
        current_n = sum(g >= 0.3 - 1e-6 for g in gaps)
        deficit = sum(max(0.0, g) for g in gaps)
        info.update(expected_delta=round(expected, 3), measured_delta=round(delta, 3),
                    residual=round(residual, 3), delta_trend=round(trend, 3),
                    demand_trend=round(demand_trend, 3), worst_worsening=round(worst_worsening, 3),
                    actual_deficit=round(deficit, 3), actual_count=current_n,
                    outdoor_mean=round(outdoor, 3), departure_mean=round(departure, 3),
                    forecast=forecast)
        if forecast is None or not isfinite(forecast):
            return None, "modele_meteo", info
        # Comfort protection: never reduce while a room is materially cold,
        # cooling, or a cold spell is imminent. Delta alone is not power.
        if (max_gap <= 0.4 and current_n <= 2 and deficit <= 1.2
                and demand_trend <= 0.1 and worst_worsening <= 0.2 and forecast >= -0.5
                and (residual <= -0.4 or (trend <= -0.5 and residual <= 0.1))):
            return -0.5, "modele_reduction", info
        if (max_gap >= 0.7 and current_n >= 2 and persistence >= 0.25
                and (demand_trend >= 0.2 or forecast <= -0.5)
                and (residual >= 0.4 or (trend >= 0.5 and residual >= -0.1))):
            return 0.5, "modele_renfort", info
        return 0.0, "modele_neutre", info
