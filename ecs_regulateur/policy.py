"""Pure, read-only ECS policy. Decisions are proposals, never actuator commands."""
from dataclasses import dataclass, asdict
from math import isfinite
from datetime import datetime

@dataclass(frozen=True)
class Settings:
    fuel_target: int = 50
    electric_target: int = 55
    electric_max_minutes: int = 120
    rearm_below: int = 48

    def __post_init__(self):
        if not (self.fuel_target == 50 and 55 <= self.electric_target <= 60
                and 1 <= self.electric_max_minutes <= 120
                and 40 <= self.rearm_below < self.fuel_target):
            raise ValueError("Paramètres ECS hors limites")

@dataclass
class Cycle:
    phase: str = "locked"
    deadline: float | None = None
    started: float | None = None
    reason: str = "attente nouveau cycle"

@dataclass(frozen=True)
class Proposal:
    phase: str
    fuel_target: int
    electric: bool
    reason: str
    remaining_seconds: int = 0

def evaluate(cycle, settings, *, now, temp, color, period, pump, allowed):
    """Mutate simulated cycle only; an interrupted complement is not restarted."""
    valid = (isinstance(temp, (float, int)) and not isinstance(temp, bool)
             and isfinite(temp) and 0 <= temp <= 95
             and color in ("bleu", "blanc", "rouge")
             and period in ("hp", "hc") and pump in ("on", "off")
             and allowed is True)
    permitted = valid and (color != "rouge" or period == "hc")
    target = settings.fuel_target if permitted else settings.electric_target
    # The physical Diematic setpoint has 5-degree steps; never propose e.g. 53.
    target = 50 if permitted else 55
    def lock(reason):
        cycle.phase, cycle.reason = "locked", reason
        cycle.deadline = cycle.started = None
        return Proposal("locked", target, False, reason)
    if not valid:
        return lock("données indisponibles ou pilotage non autorisé")
    if not permitted:
        return lock("rouge HP : fioul 55 °C")
    if cycle.phase == "electric":
        if (cycle.deadline is None or cycle.started is None or now < cycle.started
                or cycle.deadline <= now
                or now - cycle.started >= settings.electric_max_minutes * 60):
            return lock("durée électrique maximale")
        if temp >= settings.electric_target:
            return lock("cible électrique atteinte")
        if temp < settings.rearm_below or pump == "on":
            return lock("nouveau soutirage ou pompe ECS active : complément interrompu")
        return Proposal("electric", target, True, "complément simulé",
                        max(0, int(min(cycle.deadline, cycle.started + settings.electric_max_minutes * 60) - now)))
    if temp < settings.rearm_below:
        cycle.phase, cycle.reason = "fuel", "nouvelle recharge"
    if cycle.phase == "fuel":
        if temp >= settings.electric_target:
            return lock("cible déjà atteinte")
        if temp >= settings.fuel_target and pump == "off":
            cycle.phase = "electric"
            cycle.started = now
            cycle.deadline = now + settings.electric_max_minutes * 60
            cycle.reason = "début complément simulé"
            return Proposal("electric", target, True, cycle.reason, settings.electric_max_minutes * 60)
        return Proposal("fuel", target, False, "attente 50 °C et fin pompe ECS")
    return Proposal("locked", target, False, cycle.reason)

def unused_recharge(records, history, *, now, threshold=48):
    """Legacy migration only: require recorded cold sample after both last OFF/idle transitions."""
    try:
        sw, timer = records["switch.chauffe_eau"], records["timer.ecs_hybride_limite"]
        if sw["state"] != "off" or timer["state"] != "idle":
            return False
        if float(records["sensor.chauffe_eau_puissance"]["state"]) != 0:
            return False
        def stamp(value):
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        cutoff = max(stamp(sw["last_changed"]), stamp(timer["last_changed"]), now - 10800)
        cold = None
        for item in history:
            try:
                value = float(item["state"])
                when = stamp(item.get("last_changed") or item["last_updated"])
            except (KeyError, ValueError, TypeError):
                continue
            if not cutoff < when <= now:
                continue
            if value >= 55:
                cold = None
            elif 0 <= value < threshold:
                cold = when
        return cold is not None
    except (KeyError, ValueError, TypeError):
        return False

def restore(data, now, settings):
    """Corrupt/expired/reversed clock state fails closed."""
    try:
        c = Cycle(**data)
        if c.phase not in ("locked", "fuel", "electric"):
            raise ValueError("phase")
        if c.phase == "electric":
            if not (isinstance(c.started, (int, float)) and isinstance(c.deadline, (int, float))
                    and isfinite(c.started) and isfinite(c.deadline)
                    and c.started <= now < c.deadline
                    and c.deadline <= c.started + 7200
                    and now < c.started + settings.electric_max_minutes * 60):
                return Cycle(reason="complément expiré au redémarrage")
        return c
    except (TypeError, ValueError):
        return Cycle(reason="état persistant invalide")
