"""V0.3.5 - diagnostics de confort et anti-oscillation météo.

Module volontairement autonome : aucun appel Home Assistant et aucune écriture
sur la Diematic. Le mode comparatif reste actif tant que l'option
comfort_guard_enabled n'est pas explicitement activée.
"""
from dataclasses import dataclass
import time


COLD_GAP = 0.5
LOCAL_SECONDS = 60 * 60
COLLECTIVE_SECONDS = 45 * 60
COLLECTIVE_FADE_SECONDS = 15 * 60
WEATHER_HYSTERESIS_SECONDS = 15 * 60
ROLLING_SECONDS = 24 * 60 * 60


@dataclass
class ComfortAssessment:
    mode: str
    local_room: str | None
    local_minutes: float
    collective_minutes: float
    cold_rooms: int
    total_minutes_24h: float
    rooms: list


class WeatherHysteresis:
    """Retarde les corrections météo qui réduisent le chauffage.

    Une correction moins négative (ou positive) est immédiate pour préserver
    le confort. Les nouvelles réductions doivent persister 15 minutes.
    L'écart de 0,25 °C évite de réagir à chaque bruit de prévision.
    """

    def __init__(self):
        self.applied = None
        self.pending = None
        self.pending_since = None

    def apply(self, proposed, now):
        if proposed is None:
            self.pending = None
            self.pending_since = None
            return 0.0
        proposed = round(float(proposed) * 4) / 4
        if self.applied is None or proposed >= self.applied + 0.25:
            self.applied = proposed
            self.pending = None
            self.pending_since = None
        elif proposed <= self.applied - 0.25:
            if self.pending is None or abs(proposed - self.pending) > 0.25:
                self.pending = proposed
                self.pending_since = now
            elif now - self.pending_since >= WEATHER_HYSTERESIS_SECONDS:
                self.applied = proposed
                self.pending = None
                self.pending_since = None
        else:
            self.pending = None
            self.pending_since = None
        return self.applied


class ComfortGuard:
    """Suit les déficits réels, sans confondre une seule pièce et tout le circuit."""

    def __init__(self, memory=None, now=None):
        now = time.time() if now is None else now
        memory = memory if isinstance(memory, dict) else {}
        since = memory.get("since", {})
        self.since = {
            str(name): float(stamp) for name, stamp in since.items()
            if isinstance(stamp, (int, float)) and 0 <= now - stamp <= ROLLING_SECONDS
        } if isinstance(since, dict) else {}
        stamp = memory.get("collective_since")
        self.collective_since = (
            float(stamp) if isinstance(stamp, (int, float))
            and 0 <= now - stamp <= ROLLING_SECONDS else None
        )
        buckets = memory.get("buckets", {})
        self.buckets = {}
        if isinstance(buckets, dict):
            for bucket, rows in buckets.items():
                try:
                    bucket_int = int(bucket)
                except (TypeError, ValueError):
                    continue
                if now - ROLLING_SECONDS - 300 <= bucket_int * 300 <= now and isinstance(rows, dict):
                    self.buckets[str(bucket_int)] = {
                        str(name): max(0.0, min(5.0, float(minutes)))
                        for name, minutes in rows.items()
                        if isinstance(minutes, (int, float))
                    }

    def snapshot(self):
        return {
            "since": dict(self.since),
            "collective_since": self.collective_since,
            "buckets": self.buckets,
        }

    def observe(self, gaps, valid, elapsed, now=None):
        now = time.time() if now is None else now
        elapsed = max(0.0, min(120.0, float(elapsed)))
        positions = {str(name): max(0.0, float(gap)) for name, gap in gaps}
        cold = {name: gap for name, gap in positions.items() if gap >= COLD_GAP}
        for name in positions:
            if name in cold:
                self.since.setdefault(name, now)
            else:
                self.since.pop(name, None)
        for missing in set(self.since) - set(positions):
            del self.since[missing]

        if len(cold) >= 2:
            if self.collective_since is None:
                self.collective_since = now
        else:
            self.collective_since = None

        # Les minutes sont additionnées entre pièces, comme l'indicateur
        # historique de stabilisation, et non comme des minutes calendaires.
        bucket = str(int(now // 300))
        values = self.buckets.setdefault(bucket, {})
        for name in cold:
            values[name] = round(values.get(name, 0.0) + elapsed / 60.0, 3)
        cutoff = int((now - ROLLING_SECONDS - 300) // 300)
        for key in list(self.buckets):
            if int(key) < cutoff:
                del self.buckets[key]

        totals = {name: 0.0 for name in positions}
        for rows in self.buckets.values():
            for name, minutes in rows.items():
                totals[name] = totals.get(name, 0.0) + minutes

        collective_minutes = (
            max(0.0, (now - self.collective_since) / 60.0)
            if self.collective_since is not None else 0.0
        )
        isolated_name = next(iter(cold)) if len(cold) == 1 else None
        local_minutes = (
            max(0.0, (now - self.since[isolated_name]) / 60.0)
            if isolated_name else 0.0
        )
        # Une anomalie locale n'est constatée qu'avec suffisamment de capteurs.
        if valid >= 8 and isolated_name and local_minutes >= LOCAL_SECONDS / 60:
            mode = "local"
        elif valid >= 8 and len(cold) >= 2 and collective_minutes >= COLLECTIVE_SECONDS / 60:
            mode = "collective"
        else:
            mode = "normal"

        rooms = [
            {
                "name": name,
                "gap_c": round(gap, 2),
                "deficit_minutes_24h": round(totals.get(name, 0.0)),
                "continuous_minutes": round((now - self.since[name]) / 60, 1)
                    if name in self.since else 0.0,
            }
            for name, gap in positions.items()
        ]
        rooms.sort(key=lambda item: (-item["deficit_minutes_24h"], item["name"]))
        return ComfortAssessment(
            mode=mode,
            local_room=isolated_name if mode == "local" else None,
            local_minutes=local_minutes if mode == "local" else 0.0,
            collective_minutes=collective_minutes,
            cold_rooms=len(cold),
            total_minutes_24h=round(sum(totals.values()), 1),
            rooms=rooms,
        )


def protected_weather(correction, assessment):
    """Neutralise progressivement le frein météo uniquement si déficit collectif."""
    if correction >= 0 or assessment.mode != "collective":
        return correction
    elapsed_after_threshold = max(0.0, assessment.collective_minutes - 45.0)
    factor = max(0.0, 1.0 - elapsed_after_threshold / 15.0)
    return correction * factor
