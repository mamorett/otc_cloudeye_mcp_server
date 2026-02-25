# otc_cloudeye_mcp_server

```json
{
  "mcpServers": {
    "otc-cloud": {
      "command": "uv",
      "args": [
        "run",
        "/percorso/assoluto/del/file/otc_cloudeye_mcp.py"
      ],
      "env": {
         "OS_CLIENT_CONFIG_FILE": "/percorso/a/clouds.yaml"
      }
    }
  }
}
```

Ecco come funziona ora la logica di caricamento:

**Priorità assoluta**: Se definisci la variabile d'ambiente `OS_CLIENT_CONFIG_FILE`, lo script userà solo quel file. Se il file non esiste, darà errore (fail fast).

**Fallback**: Se la variabile non è definita, cercherà nei percorsi standard (`./clouds.yaml`, `~/.config/openstack/...`, `/etc/openstack/...`).

**Cloud dinamico**: Ho anche aggiunto il supporto per `OS_CLOUD` (default "otc"), così puoi scegliere quale profilo usare dentro il yaml senza toccare il codice python.

```bash
env OS_CLIENT_CONFIG_FILE=/gorgon/ia/otc_cloudeye_mcp_server/clouds.yaml uv run /gorgon/ia/otc_cloudeye_mcp_server/otc_cloudeye_mcp.py
```

## Available MCP Methods

The `otc_cloudeye_mcp.py` script exposes the following tools via FastMCP:

### `list_available_projects()`
List all available projects for the authenticated user.

### `set_active_project(project_name: str)`
Set the active project for subsequent operations. Takes the target `project_name` as a parameter.

### `list_servers()`
List all ECS servers available within the currently active project.

### `get_agt_metrics_definitions(instance_id: Optional[str] = None)`
Get the definitions of available Agent (AGT) metrics. If an `instance_id` is provided, it checks the actual availability of the metrics on that specific instance. If not provided, it returns the standard supported metric list.

### `get_ecs_metrics_definitions(instance_id: Optional[str] = None)`
Get the definitions of available System (SYS.ECS) metrics. Similar to AGT metrics, checking actual availability if `instance_id` is given, otherwise returning the standard supported list.

### `get_single_metric_data(namespace: str, metric_name: str, instance_id: str, hours_back: int = 1, period: int = 300, filter_type: str = "average")`
Get historical data for a specific metric.
- `namespace`: E.g., `AGT.ECS` or `SYS.ECS`.
- `metric_name`: The metric to query.
- `instance_id`: Target ECS instance ID.
- `hours_back`: Query range going backwards from now in hours (default 1).
- `period`: Data point aggregation period in seconds (default 300).
- `filter_type`: Aggregation method (default `average`).

### `get_combined_metrics_snapshot(instance_id: str, hours_back: int = 1)`
Get a unified snapshot of ALL metrics (AGT and ECS combined) for a specific instance. Returns the latest values, timestamps, and basic statistics for all the configured metrics.