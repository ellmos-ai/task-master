# -*- coding: utf-8 -*-
"""
taskplan -- Eigenstaendiges Task-Modul der .CONTROL-Saeule
==========================================================

Als Modul `task-master` gefuehrt (Umzug [U 2026-07-26] aus der
Gedaechtnis-Saeule nach .CONTROL: .MEMORY haelt Speicher, .CONTROL
steuert ihn). Das importierbare Paket heisst weiterhin `taskplan`.
Die inhaltliche Achse USMC + GARDENER + TASKPLAN [U 2026-07-11]
besteht fort, auch wenn die Ordner auseinanderliegen.

Extrahiert aus rinnsal/tasks; Rinnsal importiert es seither zurueck
(Seam mit gebuendeltem Fallback).

Abgrenzung: Tasks sind KEINE Tickets. Tickets (z. B. das dateibasierte
Ticket-System) koennen ueber api.add_from_ticket() zu Tasks fuehren,
muessen es aber nicht -- beide Systeme bleiben getrennt.
"""
__version__ = "0.4.0"

from .client import (  # noqa: F401
    TaskClient,
    TASK_SCHEMA_SQL,
    SCHEMA_V2_COLUMNS,
    VALID_STATUSES,
    VALID_PRIORITIES,
    VALID_EFFORTS,
    VALID_SCOPES,
    get_default_db_path,
    count_tasks_in,
)

from .config import (  # noqa: F401
    load_config,
    find_config_file,
    configured_db_path,
)

from .workflows import (  # noqa: F401
    TASKSOLVER,
    TASKWRITER,
    MAINTAINER,
    get_workflow_prompt,
    get_workflow_prompt_path,
    list_workflows,
)

from .runtime import (  # noqa: F401
    goal_objective,
    runtime_profile,
    startup_prompt,
)

from .starters import (  # noqa: F401
    get_starter_path,
    list_starters,
)

from .traversal import (  # noqa: F401
    Level,
    Project,
    TraversalConfig,
    discover_projects,
    find_projects,
    find_roots,
)

from .registry import (  # noqa: F401
    RegistryEntry,
    add_project,
    load_registry,
    registered_projects,
    remove_project,
)

from .locks import (  # noqa: F401
    CREATE,
    MODIFY,
    READ,
    LockView,
    build_lock_view,
    scan_lockmaster,
)

from .selector import (  # noqa: F401
    DEEP,
    SURFACE,
    Bundle,
    SelectorConfig,
    next_bundle,
)
