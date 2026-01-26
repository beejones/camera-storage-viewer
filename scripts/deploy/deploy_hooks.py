from __future__ import annotations

import argparse
import importlib
import importlib.util
import os
import sys
import logging
from dataclasses import dataclass, field
from pathlib import Path
try:
    from scripts.deploy.env_schema import VarsEnum
except ImportError:
    from env_schema import VarsEnum

from typing import Any, Protocol, runtime_checkable, MutableMapping

logger = logging.getLogger("deploy_hooks")

@dataclass
class DeployContext:
    """Read-only context passed to hooks.
    
    NOTE: 'env' is a reference to a dictionary. Hooks can modify it (e.g. in pre_validate_env)
    to inject defaults before strict validation.
    """
    repo_root: Path
    env: MutableMapping[str, str]  # Merged runtime + deploy env
    args: argparse.Namespace
    # We might add compose_defaults here later if needed
    
    # Simple logger wrapper or just expose the logger
    def log(self, msg: str) -> None:
        print(f"🪝 [hook] {msg}")

@dataclass
class DeployPlan:
    """Mutable plan that hooks can modify."""
    name: str # Container group name
    location: str
    dns_label: str
    
    # Terminology refinements
    deploy_mode: str # e.g. "full", "web-caddy", "ftp"
    compose_service_name: str # e.g. "web", "ftp", "caddy"
    deploy_role: str # e.g. "app", "caddy", "other"
    
    # Image references
    app_image: str
    caddy_image: str
    other_image: str | None
    
    # Resources
    app_cpu: float
    app_memory: float
    caddy_cpu: float
    caddy_memory: float
    other_cpu: float
    other_memory: float
    
    # Networking
    public_domain: str
    
    # Ports/Command
    app_port: int  # Principal application port
    app_ports: list[int] = field(default_factory=list) # Additional ports if any
    web_command: list[str] | None = None
    
    # Deployment Metadata
    service_mode: str = "app"  # e.g. "app", "sidecar", "worker"
    ftp_passive_range: str | None = None # e.g. "30000-30009"
    
    # Extra bag for future expansion or downstream-specific data
    extra_metadata: dict[str, Any] = field(default_factory=dict)

@runtime_checkable
class DeployHooksProtocol(Protocol):
    """Protocol defining the available hooks.
    Implementations can implement any subset of these.
    """
    def pre_validate_env(self, ctx: DeployContext) -> None: ...
    def post_validate_env(self, ctx: DeployContext) -> None: ...
    def build_deploy_plan(self, ctx: DeployContext, plan: DeployPlan) -> None: ...
    def pre_render_yaml(self, ctx: DeployContext, plan: DeployPlan) -> None: ...
    def post_render_yaml(self, ctx: DeployContext, plan: DeployPlan, yaml_text: str) -> str: ...
    def pre_az_apply(self, ctx: DeployContext, plan: DeployPlan, yaml_path: Path) -> None: ...
    def post_deploy(self, ctx: DeployContext, plan: DeployPlan, deploy_result: Any) -> None: ...
    def on_error(self, ctx: DeployContext, exc: Exception) -> None: ...

class DeployHooks:
    """Wrapper that holds the loaded hooks object (if any) and safely calls methods."""
    def __init__(self, impl: Any | None, soft_fail: bool = False):
        self._impl = impl
        self._soft_fail = soft_fail

    def call(self, hook_name: str, *args, **kwargs) -> Any:
        if not self._impl:
            return None
            
        method = getattr(self._impl, hook_name, None)
        if not method:
            # Hook not implemented, no-op
            return None
        
        try:
            return method(*args, **kwargs)
        except Exception as e:
            if self._soft_fail:
                print(f"⚠️  [hook] Hook '{hook_name}' failed: {e} (soft-fail enabled)", file=sys.stderr)
                return None
            else:
                print(f"❌ [hook] Hook '{hook_name}' failed: {e}", file=sys.stderr)
                raise

def load_hooks(repo_root: Path, module_path: str | None = None, soft_fail: bool | None = None) -> DeployHooks:
    """Load hooks from a module.
    
    Resolution order:
    1. CLI argument/Env var (if provided) -> Must exist or error.
    2. Default 'scripts/deploy/deploy_customizations.py' -> If exists, load. Else no-op.
    
    Default case uses file-path loading to avoid PYTHONPATH issues.
    """
    
    # Soft fail config: CLI arg > Env Var > Default False
    if soft_fail is None:
        soft_fail = os.getenv(VarsEnum.DEPLOY_HOOKS_SOFT_FAIL.value, "").lower() == "true"
    
    # Determine target module path
    target_path = module_path
    must_exist = True
    
    if not target_path:
        # Check env var
        target_path = os.getenv(VarsEnum.DEPLOY_HOOKS_MODULE.value)
    
    if not target_path:
        # Check default path relative to repo_root
        default_file = repo_root / "scripts" / "deploy" / "deploy_customizations.py"
        if default_file.exists():
            target_path = str(default_file.resolve())
            must_exist = False # If it somehow fails but wasn't requested, we'll handle below
        else:
            # No hooks configured
            return DeployHooks(None, soft_fail=soft_fail)

    print(f"🪝 [hooks] Loading hooks from: {target_path}")
    
    try:
        if target_path.endswith(".py") or "/" in target_path or "\\" in target_path:
            # Load from file path (more robust for default/CWD independence)
            path_obj = Path(target_path).resolve()
            if not path_obj.exists():
                if must_exist:
                    raise FileNotFoundError(f"Hook module not found at: {path_obj}")
                return DeployHooks(None, soft_fail=soft_fail)
                
            spec = importlib.util.spec_from_file_location("deploy_customizations", path_obj)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules["deploy_customizations"] = module
                spec.loader.exec_module(module)
            else:
                 raise ImportError(f"Could not load spec from {path_obj}")
        else:
            # Import as module
            module = importlib.import_module(target_path)
            
        # Look for 'get_hooks'
        if hasattr(module, "get_hooks"):
            hooks_impl = module.get_hooks()
        else:
            # Assume module itself is the hooks object (standalone functions)
            hooks_impl = module
            
        return DeployHooks(hooks_impl, soft_fail=soft_fail)

    except Exception as e:
        # If soft_fail is enabled, log the loader failure and return no hooks
        if soft_fail:
             print(f"⚠️  [hooks] Failed to load hooks from {target_path}: {e} (soft-fail enabled)", file=sys.stderr)
             return DeployHooks(None, soft_fail=soft_fail)
             
        # If successfully resolved but failed to import, it's a hard error
        # regardless of whether it was requested or default.
        raise ImportError(f"Failed to load hooks from {target_path}: {e}")
