"""Pure BACH/Ocean selection contract: preferences never grant authority.

The consumer reads versioned settings/task overrides from its canonical store
and independently verifies repository authority, leases and execution guards.
No IO, provider imports, task claims, or alternate policy storage lives here.
"""
from dataclasses import dataclass, asdict

CONTRACT_VERSION = "worker-selection.v1"
FIELDS = frozenset({"require_assigned_slot", "enforce_model_binding"})


@dataclass(frozen=True)
class SelectionPolicy:
    require_assigned_slot: bool = False
    enforce_model_binding: bool = False

    def __post_init__(self):
        if any(type(value) is not bool for value in asdict(self).values()):
            raise ValueError("Selection flags must be explicit booleans")

    @classmethod
    def merge(cls, defaults, override=None, profile=None):
        """Task overrides inherit on None; profile restrictions remain binding."""
        if (type(defaults) is not dict or override is not None and type(override) is not dict
                or profile is not None and type(profile) is not dict):
            raise ValueError("Selection records must be dictionaries")
        override = override or {}
        profile = profile or {}
        values = {}
        for name in FIELDS:
            base = defaults.get(name, False)
            value = override.get(name)
            value = base if value is None else value
            worker = profile.get(name, False)
            if type(value) is not bool or type(worker) is not bool:
                raise ValueError("Selection flags must be explicit booleans")
            values[name] = value or worker
        return cls(**values)

    def snapshot(self):
        return asdict(self)

    def rejection(self, task, worker):
        """Only explicit effective restrictions make routing fields exclusive."""
        if self.require_assigned_slot:
            target = str(task.get("assigned_slot") or "").strip()
            actual = str(worker.get("id") or "").strip()
            if not target or not actual:
                return "explicit_slot_required"
            if target != actual:
                return "slot_binding"
        if self.enforce_model_binding:
            target = str(task.get("required_model") or "").strip()
            actual = str(worker.get("model") or "").strip()
            if target and target != actual:
                return "model_binding"
        # assigned_to is a preference, including role/provider/human labels.
        return None


def repository_rejection(repository, allowed_repositories):
    """An empty optional selection list is open; repo authority is separate."""
    if (type(allowed_repositories) not in (list, tuple)
            or len(allowed_repositories) > 32
            or any(type(value) is not str or not value for value in allowed_repositories)):
        raise ValueError("Malformed repository selection restriction")
    return ("repository_not_allowed" if allowed_repositories and
            repository not in allowed_repositories else None)


def priority_key(task):
    """Unrestricted priorities sort, including P0; no value is excluded here."""
    return ({"P0":0,"P1":1,"P2":2,"P3":3,"P4":4}.get(task.get("priority"),5),
            str(task.get("created_at") or ""),int(task["id"]))
