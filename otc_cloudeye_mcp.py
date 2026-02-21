# /// script
# dependencies = [
#     "fastmcp",
#     "requests",
#     "pyyaml",
# ]
# ///

"""
OTC Telekom Cloud MCP Server for AGT Metrics
Refactored to use FastMCP and dynamic clouds.yaml configuration.
Supports OS_CLIENT_CONFIG_FILE environment variable.
"""

import os
import time
import datetime
import logging
import json
import yaml
from typing import Any, Dict, List, Optional
from pathlib import Path
import requests
from fastmcp import FastMCP

# Configurazione logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("otc-mcp-server")

# --- Constants & Configuration (COMPLETE) ---

# AGT Metrics configuration
AGT_METRICS = [
    {"name": "proc_zombie_count", "unit": "count", "description": "Number of zombie processes"},
    {"name": "proc_running_count", "unit": "count", "description": "Number of running processes"},
    {"name": "proc_idle_count", "unit": "count", "description": "Number of idle processes"},
    {"name": "net_bitSent", "unit": "bit/s", "description": "Network bits sent per second"},
    {"name": "net_bitRecv", "unit": "bit/s", "description": "Network bits received per second"},
    {"name": "mem_usedPercent", "unit": "%", "description": "Memory usage percentage"},
    {"name": "load_average5", "unit": "count", "description": "5-minute load average"},
    {"name": "cpu_usage", "unit": "%", "description": "CPU usage percentage"},
    {"name": "SlAsHbootSlAsHefi_disk_usedPercent", "unit": "%", "description": "Boot/EFI disk usage percentage"},
    {"name": "SlAsH_disk_usedPercent", "unit": "%", "description": "Root disk usage percentage"},
    {"name": "SlAsH_disk_ioUtils", "unit": "%", "description": "Root disk I/O utilization percentage"}
]

# ECS Metrics configuration
ECS_METRICS = [
    {"name": "network_vm_bandwidth_out", "unit": "B/s", "description": "Network VM bandwidth outbound"},
    {"name": "network_vm_bandwidth_in", "unit": "B/s", "description": "Network VM bandwidth inbound"},
    {"name": "disk_write_bytes_rate", "unit": "B/s", "description": "Disk write bytes rate"},
    {"name": "disk_read_bytes_rate", "unit": "B/s", "description": "Disk read bytes rate"},
    {"name": "cpu_util", "unit": "%", "description": "CPU utilization percentage"}
]

ALL_METRICS = {
    "AGT.ECS": AGT_METRICS,
    "SYS.ECS": ECS_METRICS
}

# --- Configuration Loading ---

def load_clouds_yaml(cloud_name: str = "otc") -> Dict[str, Any]:
    """
    Load cloud configuration.
    Priority 1: OS_CLIENT_CONFIG_FILE environment variable.
    Priority 2: Standard OpenStack search paths.
    """
    
    # Check for explicit environment variable override
    custom_path_env = os.getenv("OS_CLIENT_CONFIG_FILE")
    search_paths = []
    
    if custom_path_env:
        custom_path = Path(custom_path_env)
        if not custom_path.exists():
            raise FileNotFoundError(f"OS_CLIENT_CONFIG_FILE is set to '{custom_path}' but file does not exist.")
        search_paths.append(custom_path)
        logger.info(f"Using configuration file from OS_CLIENT_CONFIG_FILE: {custom_path}")
    else:
        # Standard OpenStack paths
        search_paths = [
            Path.cwd() / "clouds.yaml",
            Path.home() / ".config" / "openstack" / "clouds.yaml",
            Path("/etc/openstack/clouds.yaml")
        ]

    config = None
    used_path = None

    for path in search_paths:
        if path.exists():
            try:
                with open(path, "r") as f:
                    data = yaml.safe_load(f)
                    if data and "clouds" in data and cloud_name in data["clouds"]:
                        config = data["clouds"][cloud_name]
                        used_path = path
                        break
            except Exception as e:
                logger.warning(f"Error reading {path}: {e}")
                # If explicit path was given and failed to parse, we should probably fail hard
                if custom_path_env:
                    raise ValueError(f"Failed to parse explicitly requested config file {path}: {e}")

    if not config:
        if custom_path_env:
            raise ValueError(f"Cloud '{cloud_name}' not found in {custom_path_env}")
        else:
            raise FileNotFoundError(f"Could not find configuration for cloud '{cloud_name}' in standard locations.")

    logger.info(f"Loaded '{cloud_name}' configuration from {used_path}")
    
    # Extract Auth details
    auth = config.get("auth", {})
    
    return {
        "auth_url": auth.get("auth_url"),
        "username": auth.get("username"),
        "password": auth.get("password"),
        "domain_name": auth.get("user_domain_name") or auth.get("domain_name"),
        "project_name": auth.get("project_name"),
        "region": config.get("region_name", "eu-de")
    }

# --- Client Logic (Preserved) ---

class OTCCloudEyeClient:
    """OTC Cloud Eye client for retrieving metrics"""
    
    def __init__(self, auth_url: str, username: str, password: str, 
                 domain_name: str, project_name: str, region: str):
        self.auth_url = auth_url
        self.username = username
        self.password = password
        self.domain_name = domain_name
        self.project_name = project_name
        self.region = region
        self.token = None
        self.project_id = None
        self.token_expires = None
        self.cloud_eye_endpoint = f"ces.{region}.otc.t-systems.com"
        self.ecs_endpoint = f"ecs.{region}.otc.t-systems.com"

    def authenticate(self) -> bool:
        """Authenticate with OTC and get a token"""
        auth_data = {
            "auth": {
                "identity": {
                    "methods": ["password"],
                    "password": {
                        "user": {
                            "name": self.username,
                            "password": self.password,
                            "domain": {
                                "name": self.domain_name
                            }
                        }
                    }
                },
                "scope": {
                    "project": {
                        "name": self.project_name
                    }
                }
            }
        }
        
        headers = {"Content-Type": "application/json"}
        try:
            # Clean auth_url if it ends with slash
            base_url = self.auth_url.rstrip('/')
            response = requests.post(f"{base_url}/auth/tokens", 
                                    json=auth_data, 
                                    headers=headers,
                                    timeout=30)
            
            if response.status_code == 201:
                self.token = response.headers['X-Subject-Token']
                resp_data = response.json()
                self.project_id = resp_data['token']['project']['id']
                self.token_expires = resp_data['token']['expires_at']
                logger.info(f"Authentication successful. Token expires at {self.token_expires}")
                return True
            else:
                logger.error(f"Authentication failed: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            logger.error(f"Authentication error: {e}")
            return False

    def check_token_validity(self) -> bool:
        """Check if token is still valid, renew if needed"""
        if not self.token or not self.token_expires:
            return self.authenticate()
            
        try:
            # Handle timestamps safely
            expiry_time = datetime.datetime.strptime(self.token_expires, "%Y-%m-%dT%H:%M:%S.%fZ")
            current_time = datetime.datetime.utcnow()
            
            if (expiry_time - current_time).total_seconds() < 300:
                return self.authenticate()
            
            return True
        except Exception as e:
            logger.error(f"Token validation error: {e}")
            return self.authenticate()
        
    def list_projects(self) -> Optional[List[Dict]]:
        """List all available projects for the authenticated user"""
        auth_data = {
            "auth": {
                "identity": {
                    "methods": ["password"],
                    "password": {
                        "user": {
                            "name": self.username,
                            "password": self.password,
                            "domain": {
                                "name": self.domain_name
                            }
                        }
                    }
                },
                "scope": {
                    "domain": {
                        "name": self.domain_name
                    }
                }
            }
        }
        
        headers = {"Content-Type": "application/json"}
        try:
            base_url = self.auth_url.rstrip('/')
            response = requests.post(f"{base_url}/auth/tokens", 
                                    json=auth_data, 
                                    headers=headers,
                                    timeout=30)
            
            if response.status_code == 201:
                domain_token = response.headers['X-Subject-Token']
                
                projects_url = f"{base_url}/auth/projects"
                headers = {
                    "Content-Type": "application/json",
                    "X-Auth-Token": domain_token
                }
                
                projects_response = requests.get(projects_url, headers=headers, timeout=30)
                
                if projects_response.status_code == 200:
                    projects_data = projects_response.json()
                    return projects_data.get('projects', [])
                else:
                    logger.error(f"Failed to list projects: {projects_response.status_code} - {projects_response.text}")
                    return None
            else:
                logger.error(f"Domain authentication failed: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logger.error(f"Error listing projects: {e}")
            return None

    def set_project(self, project_name: str) -> bool:
        """Set the project for subsequent operations"""
        self.project_name = project_name
        return self.authenticate()

    def list_servers(self) -> Optional[List[Dict]]:
        """List all ECS instances"""
        if not self.check_token_validity():
            return None
            
        url = f"https://{self.ecs_endpoint}/v2/{self.project_id}/servers/detail"
        
        headers = {
            "Content-Type": "application/json",
            "X-Auth-Token": self.token
        }
        
        try:
            response = requests.get(url, headers=headers, timeout=30)
            
            if response.status_code == 200:
                return response.json().get('servers', [])
            else:
                logger.error(f"Failed to list servers: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logger.error(f"Error listing servers: {e}")
            return None
        
    def query_ecs_metrics(self, instance_id: str = None) -> Optional[Dict]:
        """Query ECS metrics availability"""
        if not self.check_token_validity():
            return None
            
        url = f"https://{self.cloud_eye_endpoint}/V1.0/{self.project_id}/metrics"
        
        params = {
            'namespace': 'SYS.ECS',
            'limit': 1000
        }
        
        if instance_id:
            params['dim.0'] = f'instance_id,{instance_id}'
            
        headers = {
            "Content-Type": "application/json",
            "X-Auth-Token": self.token
        }
        
        try:
            response = requests.get(url, params=params, headers=headers, timeout=30)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            logger.error(f"Error querying ECS metrics: {e}")
            return None

    def query_agt_metrics(self, instance_id: str = None) -> Optional[Dict]:
        """Query AGT metrics availability"""
        if not self.check_token_validity():
            return None
            
        url = f"https://{self.cloud_eye_endpoint}/V1.0/{self.project_id}/metrics"
        
        params = {
            'namespace': 'AGT.ECS',
            'limit': 1000
        }
        
        if instance_id:
            params['dim.0'] = f'instance_id,{instance_id}'
            
        headers = {
            "Content-Type": "application/json",
            "X-Auth-Token": self.token
        }
        
        try:
            response = requests.get(url, params=params, headers=headers, timeout=30)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            logger.error(f"Error querying AGT metrics: {e}")
            return None

    def query_metric_data_generic(self, namespace: str, metric_name: str, instance_id: str, 
                                from_time: int, to_time: int, period: int = 300, 
                                filter_type: str = "average") -> Optional[Dict]:
        """Unified method to query data for both AGT and SYS namespaces"""
        if not self.check_token_validity():
            return None
            
        url = f"https://{self.cloud_eye_endpoint}/V1.0/{self.project_id}/metric-data"
        
        params = {
            'namespace': namespace,
            'metric_name': metric_name,
            'from': from_time,
            'to': to_time,
            'period': period,
            'filter': filter_type,
            'dim.0': f'instance_id,{instance_id}'
        }
            
        headers = {
            "Content-Type": "application/json",
            "X-Auth-Token": self.token
        }
        
        try:
            response = requests.get(url, params=params, headers=headers, timeout=30)
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Failed to query metric data for {metric_name}: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logger.error(f"Error querying metric data for {metric_name}: {e}")
            return None

    def get_all_ecs_metrics_data(self, instance_id: str, from_time: int, to_time: int) -> Dict:
        """Get all ECS (SYS.ECS) metrics data for an instance"""
        results = {}
        
        for metric_conf in ECS_METRICS:
            metric_name = metric_conf["name"]
            metric_data = self.query_metric_data_generic(
                namespace="SYS.ECS",
                metric_name=metric_name,
                instance_id=instance_id,
                from_time=from_time,
                to_time=to_time,
                period=300,
                filter_type="average"
            )
            
            if metric_data and metric_data.get('datapoints'):
                latest_datapoint = max(metric_data['datapoints'], key=lambda x: x['timestamp'])
                results[metric_name] = {
                    "latest_value": latest_datapoint.get('average'),
                    "timestamp": latest_datapoint['timestamp'],
                    "total_datapoints": len(metric_data['datapoints']),
                    "all_datapoints": metric_data['datapoints']
                }
            else:
                results[metric_name] = {
                    "latest_value": None,
                    "timestamp": None,
                    "total_datapoints": 0,
                    "all_datapoints": []
                }
        
        return results

# --- Global State Management ---

otc_client: Optional[OTCCloudEyeClient] = None

def get_client() -> OTCCloudEyeClient:
    """Singleton-like access to the client, initializing from clouds.yaml if needed"""
    global otc_client
    if otc_client:
        return otc_client

    try:
        # Allow overriding the profile name via env var (default to 'otc')
        cloud_profile = os.getenv("OS_CLOUD", "otc")
        
        config = load_clouds_yaml(cloud_profile)
        
        # Validation
        required_fields = ["auth_url", "username", "password", "domain_name", "project_name"]
        missing = [f for f in required_fields if not config.get(f)]
        
        if missing:
            raise ValueError(f"Missing required fields in clouds.yaml for profile '{cloud_profile}': {', '.join(missing)}")
        
        logger.info(f"Initializing OTC Client for project: {config['project_name']} (Profile: {cloud_profile})")
        client = OTCCloudEyeClient(
            auth_url=config['auth_url'],
            username=config['username'],
            password=config['password'],
            domain_name=config['domain_name'],
            project_name=config['project_name'],
            region=config['region']
        )
        
        if not client.authenticate():
            raise RuntimeError("Initial authentication failed. Check credentials in clouds.yaml.")
        
        otc_client = client
        return otc_client
        
    except Exception as e:
        logger.error(f"Failed to initialize client: {e}")
        raise

# --- FastMCP Server Definition ---

mcp = FastMCP("OTC Cloud Eye Complete", dependencies=["requests", "pyyaml"])

@mcp.tool()
def list_available_projects() -> str:
    """List all available projects for the authenticated user."""
    try:
        client = get_client()
        projects = client.list_projects()
        if projects is None:
            return "Failed to retrieve projects."
        return json.dumps({"projects": projects}, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def set_active_project(project_name: str) -> str:
    """Set the active project for subsequent operations."""
    try:
        client = get_client()
        success = client.set_project(project_name)
        if success:
            return f"Successfully switched to project: {project_name}"
        else:
            return f"Failed to switch to project {project_name}. Check logs."
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def list_servers() -> str:
    """List all ECS servers in the current project."""
    try:
        client = get_client()
        servers = client.list_servers()
        if servers is None:
            return "Failed to retrieve servers."
        return json.dumps({"servers": servers}, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def get_agt_metrics_definitions(instance_id: Optional[str] = None) -> str:
    """
    Get available AGT metrics definitions.
    If instance_id is provided, checks actual availability on that instance.
    Otherwise returns the standard supported list.
    """
    try:
        client = get_client()
        if instance_id:
            metrics = client.query_agt_metrics(instance_id)
            return json.dumps(metrics, indent=2) if metrics else "No metrics found or error occurred."
        else:
            return json.dumps({"metrics": AGT_METRICS}, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def get_ecs_metrics_definitions(instance_id: Optional[str] = None) -> str:
    """
    Get available ECS (System) metrics definitions.
    If instance_id is provided, checks actual availability on that instance.
    Otherwise returns the standard supported list.
    """
    try:
        client = get_client()
        if instance_id:
            metrics = client.query_ecs_metrics(instance_id)
            return json.dumps(metrics, indent=2) if metrics else "No metrics found or error occurred."
        else:
            return json.dumps({"metrics": ECS_METRICS}, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def get_single_metric_data(namespace: str, metric_name: str, instance_id: str, 
                           hours_back: int = 1, period: int = 300, filter_type: str = "average") -> str:
    """
    Get historical data for a specific metric (AGT.ECS or SYS.ECS).
    """
    try:
        client = get_client()
        now_ms = int(time.time() * 1000)
        from_time_ms = now_ms - (hours_back * 3600 * 1000)
        
        data = client.query_metric_data_generic(
            namespace=namespace,
            metric_name=metric_name,
            instance_id=instance_id,
            from_time=from_time_ms,
            to_time=now_ms,
            period=period,
            filter_type=filter_type
        )
        
        if data is None:
            return f"Failed to retrieve data for {metric_name}"
            
        return json.dumps(data, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def get_combined_metrics_snapshot(instance_id: str, hours_back: int = 1) -> str:
    """
    Get a snapshot of ALL metrics (AGT and ECS) for a specific instance.
    Returns latest values and basic statistics for all configured metrics.
    """
    try:
        client = get_client()
        now_ms = int(time.time() * 1000)
        from_time_ms = now_ms - (hours_back * 3600 * 1000)
        
        # 1. Fetch AGT Metrics
        agt_results = {}
        for metric_config in AGT_METRICS:
            metric_name = metric_config["name"]
            metric_data = client.query_metric_data_generic(
                namespace="AGT.ECS",
                metric_name=metric_name,
                instance_id=instance_id,
                from_time=from_time_ms,
                to_time=now_ms,
                period=300
            )
            
            if metric_data and metric_data.get('datapoints'):
                latest_datapoint = max(metric_data['datapoints'], key=lambda x: x['timestamp'])
                agt_results[metric_name] = {
                    "description": metric_config["description"],
                    "unit": metric_config["unit"],
                    "latest_value": latest_datapoint.get('average'),
                    "timestamp": latest_datapoint['timestamp'],
                    "total_datapoints": len(metric_data['datapoints']),
                    "namespace": "AGT.ECS"
                }
            else:
                agt_results[metric_name] = {
                    "description": metric_config["description"],
                    "unit": metric_config["unit"],
                    "latest_value": None,
                    "namespace": "AGT.ECS",
                    "note": "No data points found"
                }
        
        # 2. Fetch ECS Metrics (using helper)
        ecs_raw_results = client.get_all_ecs_metrics_data(instance_id, from_time_ms, now_ms)
        ecs_results = {}
        for metric_name, data in ecs_raw_results.items():
            metric_config = next((m for m in ECS_METRICS if m["name"] == metric_name), 
                               {"description": metric_name, "unit": ""})
            ecs_results[metric_name] = {
                "description": metric_config["description"],
                "unit": metric_config["unit"],
                "latest_value": data["latest_value"],
                "timestamp": data["timestamp"],
                "total_datapoints": data["total_datapoints"],
                "namespace": "SYS.ECS"
            }
            
        combined_response = {
            "instance_id": instance_id,
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "metrics": {**agt_results, **ecs_results}
        }
        
        return json.dumps(combined_response, indent=2)
        
    except Exception as e:
        logger.exception("Error in combined metrics")
        return f"Error processing combined metrics: {str(e)}"

if __name__ == "__main__":
    mcp.run()