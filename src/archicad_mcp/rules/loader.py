from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from archicad_mcp.rules.builtin.classification_required import ClassificationRequiredRule
from archicad_mcp.rules.builtin.ifc_readiness import IfcPropertyRequiredRule
from archicad_mcp.rules.builtin.layer_compliance import LayerComplianceRule
from archicad_mcp.rules.builtin.property_required import PropertyRequiredRule
from archicad_mcp.rules.builtin.zone_checks import ZoneNumberRequiredRule
from archicad_mcp.rules.engine import Rule
from archicad_mcp.rules.types import RuleConfigError

RULE_TYPES: dict[str, type] = {
    "property-required": PropertyRequiredRule,
    "classification-required": ClassificationRequiredRule,
    "layer-compliance": LayerComplianceRule,
    "zone-number-required": ZoneNumberRequiredRule,
    "ifc-property-required": IfcPropertyRequiredRule,
}

EXAMPLES_DIR = Path(__file__).parent / "examples"


@dataclass
class LoadedRules:
    rules: list[Rule] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    source: str = ""


def _load_yaml_file(path: Path, out: LoadedRules) -> None:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        out.errors.append(f"{path.name}: not valid YAML ({exc})")
        return
    if data is None:
        return
    if not isinstance(data, list):
        out.errors.append(f"{path.name}: expected a YAML list of rules")
        return
    for cfg in data:
        if not isinstance(cfg, dict):
            out.errors.append(f"{path.name}: rule entry is not a mapping: {cfg!r}")
            continue
        type_name = cfg.get("type")
        rule_cls = RULE_TYPES.get(type_name)
        if rule_cls is None:
            out.errors.append(f"{path.name}: unknown rule type {type_name!r} "
                              f"(known: {sorted(RULE_TYPES)})")
            continue
        try:
            out.rules.append(rule_cls.from_config(cfg))
        except RuleConfigError as exc:
            out.errors.append(f"{path.name}: {exc}")


def _load_plugin(path: Path, out: LoadedRules) -> None:
    try:
        spec = importlib.util.spec_from_file_location("archicad_mcp_custom_rules", path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        rules = getattr(module, "RULES", None)
        if not isinstance(rules, list):
            out.errors.append(f"{path.name}: must define module-level RULES = [...]")
            return
        out.rules.extend(rules)
    except Exception as exc:  # plugin code is user-supplied; never crash the server
        out.errors.append(f"{path.name}: failed to load ({exc})")


def load_rules(rules_dir: Path | None) -> LoadedRules:
    if rules_dir is None:
        out = LoadedRules(source="bundled examples")
        directory = EXAMPLES_DIR
    else:
        out = LoadedRules(source=str(rules_dir))
        directory = rules_dir
        if not directory.is_dir():
            out.errors.append(f"rules directory does not exist: {directory}")
            return out
    for path in sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml")):
        _load_yaml_file(path, out)
    plugin = directory / "custom_rules.py"
    if plugin.exists():
        _load_plugin(plugin, out)
    return out
