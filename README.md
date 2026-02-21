# otc_cloudeye_mcp_server

```
{
  "mcpServers": {
    "otc-cloud": {
      "command": "uvx",
      "args": [
        "/path/to/your/otc_mcp.py"
      ]
    }
  }
}
```

Ecco come funziona ora la logica di caricamento:

Priorità assoluta: Se definisci la variabile d'ambiente OS_CLIENT_CONFIG_FILE, lo script userà solo quel file. Se il file non esiste, darà errore (fail fast).

Fallback: Se la variabile non è definita, cercherà nei percorsi standard (./clouds.yaml, ~/.config/openstack/..., /etc/openstack/...).

Cloud dinamico: Ho anche aggiunto il supporto per OS_CLOUD (default "otc"), così puoi scegliere quale profilo usare dentro il yaml senza toccare il codice python.